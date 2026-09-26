"""
qb_loader.py

Loads QuickBooks Desktop exports (Account Listing, and the grouped
"Transaction Detail by Account" / GL report) into the qb.* schema
defined in sql/schema.sql.

Design point: these exports are NOT laid out consistently between
companies. dntl's transaction detail uses a flat, single-level section
per account; hyg's uses column indentation to represent a 2-3 level
account hierarchy (a sub-account's rows sit one column to the right of
its parent's). Column positions for the same header ("Type", "Date", ...)
also shift between files depending on how QuickBooks spaced the export.
So instead of hardcoding column indices, every parser here first finds
the header row and maps column name -> column index from it, then walks
indentation to reconstruct the account hierarchy live.

Reload model: every run REPLACES one company's fiscal year. All of that
company's journal entries dated inside the fiscal year are deleted and
the year is re-inserted from the export, in a single transaction - so
the loader can be rerun as often as the bookkeeper re-exports, and a
failure midway leaves the previous load untouched. Each run is recorded
in qb.LoadRuns. See the "Fiscal-year reload" section below.

Validation gate: a section is only loadable if its own running balance
re-derives cleanly from the parsed rows. By default, if ANY section
fails, the whole reload is refused and nothing changes; --allow-partial
reloads anyway and leaves the failed accounts empty for that year. New
Company / ChartOfAccounts / Class rows are created automatically as
they're encountered (normal dimension growth, not a schema change).

JournalEntry grouping (tested against all three companies' real files): this report shows every transaction
once per account it touches, with no stable transaction ID, so the
loader has to infer which rows belong to the same real-world transaction.
RefNum (cheque #, journal entry #) is the only field QuickBooks keeps
consistent across every account a transaction touches, so rows sharing
a RefNum are merged into one JournalEntry - verified to balance to zero
across every ref-grouped entry in all three companies' files. Name/Memo/
Class are NOT reliably consistent across accounts for the same
transaction in this report format (a bank deposit shows a generic
"Deposit" memo on the bank side but itemized per-payment memos on the
income side; a payroll cheque's liability/expense legs can post several
separate lines to the very same account) - matching on those produced
both false merges and false splits in testing. So a row with no RefNum
(the majority - roughly three-quarters of rows in these files) becomes
its own one-line JournalEntry rather than a guessed grouping. This only
affects how rows are grouped for GL drill-down; every row's dollar
amount still lands under the correct ChartAccountID either way, and
validate_section()'s per-account running-balance check - the actual
load gate - is unaffected.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text

# One shared engine/connection definition for the whole project.
from sql_helper import get_engine  # noqa: F401  (re-exported for run_qb_load.py)


# =====================================================================
# Shared: locate the header row and map column name -> column index
# =====================================================================

def _find_header_row(df, required=("Type", "Date", "Amount", "Balance")):
    """
    Scans the top of the sheet for the row containing all of `required`
    as cell values, and returns (row_index, {column_name: column_index}).
    Raises if it can't find one in the first 5 rows.
    """
    for i in range(min(5, len(df))):
        row = df.iloc[i]
        values = {str(v).strip(): idx for idx, v in row.items() if pd.notna(v)}
        if all(r in values for r in required):
            return i, values
    raise ValueError(f"Could not find a header row containing {required} in the first 5 rows")


def _cell(row, col_map, name, default=None):
    idx = col_map.get(name)
    if idx is None:
        return default
    val = row[idx]
    return default if pd.isna(val) else val


# =====================================================================
# Account Listing parser
# =====================================================================

@dataclass
class ChartAccountRow:
    account_number: str
    account_name: str
    account_type: str
    description: Optional[str]
    tax_line: Optional[str]
    parent_account_number: Optional[str]  # derived from "Parent · Name:Child · Name" in the Account column


def parse_account_list(path) -> list[ChartAccountRow]:
    df = pd.read_excel(path, sheet_name="Sheet1", header=None)
    header_row, col_map = _find_header_row(df, required=("Account", "Type", "Accnt. #"))

    out = []
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        account_number = _cell(row, col_map, "Accnt. #")
        if account_number is None:
            continue
        full_name = str(_cell(row, col_map, "Account", "")).strip()

        # "1000 · CASH AND BANK:1010 · Bank - Scotia A/C ..." -> parent is
        # "1000 · CASH AND BANK", this account's own label is the last segment.
        segments = full_name.split(":")
        own_label = segments[-1].strip()
        parent_number = None
        if len(segments) > 1:
            parent_label = segments[-2].strip()
            m = re.match(r"^([\w.]+)\s*·", parent_label)
            parent_number = m.group(1) if m else None

        out.append(ChartAccountRow(
            account_number=str(account_number).strip(),
            account_name=own_label,
            account_type=str(_cell(row, col_map, "Type", "")).strip(),
            description=_cell(row, col_map, "Description"),
            tax_line=_cell(row, col_map, "Tax Line"),
            parent_account_number=parent_number,
        ))
    return out


# =====================================================================
# Transaction Detail by Account parser
# =====================================================================

@dataclass
class TxnLine:
    account_label: str       # e.g. "1010 · Bank - Scotia A/C 26492 0068810"
    txn_type: str
    txn_date: object
    ref_num: Optional[str]
    name: Optional[str]
    memo: Optional[str]
    class_name: Optional[str]
    split: Optional[str]
    amount: float
    balance: float
    row_index: int
    occurrence_rank: int = 1  # Nth time this exact (type,date,ref,name,memo)
                               # repeats within its own section - stored for
                               # reference only, not used for JE matching
                               # (see module docstring / _find_or_create_journal_entry)


@dataclass
class Section:
    account_label: str
    depth: int
    rows: list = field(default_factory=list)
    opening_balance: float = 0.0


def parse_transaction_detail(path) -> list[Section]:
    """
    Walks the sheet, tracking a stack of open sections by column depth
    (the column the account label appeared in). A transaction row is
    assigned to the deepest currently-open section. A "Total <label>"
    row closes that section (and anything nested deeper than it).
    """
    df = pd.read_excel(path, sheet_name="Sheet1", header=None)
    header_row, col_map = _find_header_row(df)
    type_col = col_map["Type"]

    sections: list[Section] = []
    stack: list[Section] = []  # open sections, index 0 = shallowest

    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        type_val = _cell(row, col_map, "Type")

        if type_val is None:
            # Either a section header or a "Total X" trailer - the label
            # sits somewhere left of the Type column. Its column position
            # is the depth.
            label_col, label = None, None
            for c in range(0, type_col):
                v = row[c] if c in row.index else None
                if pd.notna(v):
                    label_col, label = c, str(v).strip()
            if label is None:
                continue  # fully blank row, skip

            if label.startswith("Total "):
                # Close this section and anything deeper than it.
                while stack and stack[-1].depth >= label_col:
                    stack.pop()
                continue

            # New section header. Close anything at the same or deeper
            # depth first (siblings), then open this one under whatever
            # remains on the stack (its parent).
            while stack and stack[-1].depth >= label_col:
                stack.pop()
            sec = Section(account_label=label, depth=label_col)
            sec._seen = defaultdict(int)  # natural-key occurrence counter,
                                           # scoped to this section - see
                                           # TxnLine.occurrence_rank
            sections.append(sec)
            stack.append(sec)
            continue

        # A transaction row - belongs to the deepest open section.
        if not stack:
            continue  # malformed / unexpected row before any header
        current = stack[-1]
        amount = _cell(row, col_map, "Amount")
        balance = _cell(row, col_map, "Balance")
        if amount is None or balance is None:
            continue

        txn_type = str(type_val).strip()
        txn_date = _cell(row, col_map, "Date")
        ref_num = _str_or_none(_cell(row, col_map, "Num"))
        name = _str_or_none(_cell(row, col_map, "Name"))
        memo = _str_or_none(_cell(row, col_map, "Memo"))

        # Same-day rows with an identical (type, date, ref, name, memo) and
        # no ref number - e.g. four separate "Deposit" rows with no cheque
        # number - are genuinely distinct transactions, not legs of one
        # entry. Track how many times this exact key has already been seen
        # in THIS section; the loader pairs legs across sections using the
        # same key + rank, so the Nth "Deposit" here pairs only with the
        # Nth "Deposit" on the other side of the entry, not all of them.
        base_key = (txn_type, txn_date, ref_num, name, memo)
        current._seen[base_key] += 1
        occurrence_rank = current._seen[base_key]

        current.rows.append(TxnLine(
            account_label=current.account_label,
            txn_type=txn_type,
            txn_date=txn_date,
            ref_num=ref_num,
            name=name,
            memo=memo,
            class_name=_str_or_none(_cell(row, col_map, "Class")),
            split=_str_or_none(_cell(row, col_map, "Split")),
            amount=float(amount),
            balance=float(balance),
            row_index=i,
            occurrence_rank=occurrence_rank,
        ))

    return sections


def _str_or_none(v):
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


# =====================================================================
# Validation gate: does the section's own running balance re-derive?
# =====================================================================

@dataclass
class ValidationResult:
    is_valid: bool
    opening_balance: float
    computed_ending: float
    stated_ending: float
    first_mismatch_row: Optional[int] = None


def validate_section(section: Section, tolerance: float = 0.01) -> ValidationResult:
    if not section.rows:
        return ValidationResult(True, 0.0, 0.0, 0.0)

    # Opening balance = first row's stated balance minus its own amount.
    opening = round(section.rows[0].balance - section.rows[0].amount, 2)
    running = opening
    for r in section.rows:
        running = round(running + r.amount, 2)
        if abs(running - r.balance) > tolerance:
            return ValidationResult(False, opening, running, r.balance, first_mismatch_row=r.row_index)

    return ValidationResult(True, opening, running, section.rows[-1].balance)


# =====================================================================
# Fiscal-year reload
#
# The loader works one (company, fiscal year) at a time and REPLACES
# that year's ledger on every run: all qb.JournalEntries (and, via
# ON DELETE CASCADE, their qb.JournalEntryLines) for the company dated
# inside the fiscal year are deleted, then the year is reloaded from the
# export - all inside ONE database transaction. If anything fails midway
# the transaction rolls back and the previous load is left untouched.
# Run it as many times as the bookkeeper re-exports; the result is
# always exactly one copy of that year.
#
# Rows in the export dated outside the fiscal year are ignored (and
# counted in the report), so an export that also covers the prior or
# next year can't wipe out or duplicate a neighbouring year.
#
# Chart of accounts and classes are shared across years, so they are
# upserted (never deleted).
# =====================================================================

@dataclass
class FiscalPeriod:
    fy_end_year: int
    start: date
    end: date

    def __str__(self):
        return f"FY ending {self.end:%b %d, %Y} ({self.start:%Y-%m-%d} to {self.end:%Y-%m-%d})"


def fiscal_period(fy_end_year: int, start_month: int = 9) -> FiscalPeriod:
    """
    Fiscal years are named by the calendar year they END in.
    With the default September start: fy_end_year=2025 -> 2024-09-01 .. 2025-08-31.
    """
    fy_end_year = int(fy_end_year)
    start = date(fy_end_year - 1, start_month, 1) if start_month != 1 else date(fy_end_year, 1, 1)
    next_start = date(start.year + 1, start.month, 1)
    end = date.fromordinal(next_start.toordinal() - 1)
    return FiscalPeriod(fy_end_year, start, end)


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


# =====================================================================
# Dimension get-or-create (auto-applied - these are just new data,
# not structural schema changes). Committed BEFORE the reload
# transaction so the reload itself only touches journal tables.
# =====================================================================

def get_company(engine, company_code):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT CompanyID, FiscalYearStartMonth FROM qb.Companies WHERE CompanyCode = :c"),
            {"c": company_code},
        ).fetchone()
    return (row[0], int(row[1])) if row else (None, 9)


def get_or_create_company(engine, company_code, legal_name=None):
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"),
            {"c": company_code},
        ).fetchone()
        if existing:
            if legal_name:
                conn.execute(
                    text("UPDATE qb.Companies SET LegalName = :n WHERE CompanyID = :id AND LegalName <> :n"),
                    {"n": legal_name, "id": existing[0]},
                )
            return existing[0]
        result = conn.execute(
            text("""INSERT INTO qb.Companies (CompanyCode, LegalName)
                     OUTPUT INSERTED.CompanyID
                     VALUES (:code, :name)"""),
            {"code": company_code, "name": legal_name or company_code},
        ).fetchone()
        return result[0]


def resolve_classes(engine, company_id, class_names) -> dict:
    """Returns {ClassName: ClassID}, creating any that don't exist yet."""
    out = {}
    with engine.begin() as conn:
        for name in sorted(set(n for n in class_names if n)):
            row = conn.execute(
                text("SELECT ClassID FROM qb.Classes WHERE CompanyID = :cid AND ClassName = :name"),
                {"cid": company_id, "name": name},
            ).fetchone()
            if row is None:
                row = conn.execute(
                    text("""INSERT INTO qb.Classes (CompanyID, ClassName)
                             OUTPUT INSERTED.ClassID
                             VALUES (:cid, :name)"""),
                    {"cid": company_id, "name": name},
                ).fetchone()
            out[name] = row[0]
    return out


def load_chart_of_accounts(engine, company_id, rows: list[ChartAccountRow]):
    """
    Two passes: create every account first (so parent lookups always
    succeed regardless of file order), then wire up ParentChartAccountID.
    Returns {account_number: ChartAccountID}.
    """
    number_to_id = {}
    with engine.begin() as conn:
        for r in rows:
            existing = conn.execute(
                text("SELECT ChartAccountID FROM qb.ChartOfAccounts WHERE CompanyID = :cid AND AccountNumber = :num"),
                {"cid": company_id, "num": r.account_number},
            ).fetchone()
            if existing:
                number_to_id[r.account_number] = existing[0]
                conn.execute(
                    text("""UPDATE qb.ChartOfAccounts
                            SET AccountName = :name, AccountType = :atype,
                                Description = :desc, TaxLine = :tax
                            WHERE ChartAccountID = :id"""),
                    {"name": r.account_name, "atype": r.account_type,
                     "desc": r.description, "tax": r.tax_line, "id": existing[0]},
                )
            else:
                result = conn.execute(
                    text("""INSERT INTO qb.ChartOfAccounts
                                (CompanyID, AccountNumber, AccountName, AccountType, Description, TaxLine)
                            OUTPUT INSERTED.ChartAccountID
                            VALUES (:cid, :num, :name, :atype, :desc, :tax)"""),
                    {"cid": company_id, "num": r.account_number, "name": r.account_name,
                     "atype": r.account_type, "desc": r.description, "tax": r.tax_line},
                ).fetchone()
                number_to_id[r.account_number] = result[0]

        for r in rows:
            if r.parent_account_number and r.parent_account_number in number_to_id:
                conn.execute(
                    text("UPDATE qb.ChartOfAccounts SET ParentChartAccountID = :pid WHERE ChartAccountID = :id"),
                    {"pid": number_to_id[r.parent_account_number], "id": number_to_id[r.account_number]},
                )

    return number_to_id


def _account_number_from_label(account_label):
    m = re.match(r"^([\w.]+)\s*·", account_label)
    return m.group(1) if m else account_label


def _resolve_chart_account_id(engine, company_id, account_label, cache):
    """
    account_label looks like "1010 · Bank - Scotia A/C 26492 0068810".
    Pull the leading account number and look it up (via the cache built
    by load_chart_of_accounts, falling back to a live query for accounts
    that only ever appear in the detail export, not the Account Listing).
    """
    number = _account_number_from_label(account_label)
    if number in cache:
        return cache[number]

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT ChartAccountID FROM qb.ChartOfAccounts WHERE CompanyID = :cid AND AccountNumber = :num"),
            {"cid": company_id, "num": number},
        ).fetchone()
        if row:
            cache[number] = row[0]
            return row[0]

        # Account never appeared in the Account Listing export - create a
        # bare row from just what the detail report tells us, so nothing
        # gets lost. AccountType is left generic; worth reconciling by
        # hand against a fresh Account Listing pull later.
        name = account_label.split("·", 1)[-1].strip() if "·" in account_label else account_label
        result = conn.execute(
            text("""INSERT INTO qb.ChartOfAccounts (CompanyID, AccountNumber, AccountName, AccountType)
                     OUTPUT INSERTED.ChartAccountID
                     VALUES (:cid, :num, :name, 'Unknown')"""),
            {"cid": company_id, "num": number, "name": name},
        ).fetchone()
        cache[number] = result[0]
        return result[0]


# =====================================================================
# Plan: parse + validate + filter to the fiscal year, no DB writes
# =====================================================================

@dataclass
class LoadPlan:
    period: FiscalPeriod
    sections_ok: list = field(default_factory=list)       # [(Section, [TxnLine in period])]
    failures: list = field(default_factory=list)          # validation failures (dicts)
    rows_in_period: int = 0
    rows_outside_period: int = 0
    rows_bad_date: int = 0


def build_plan(sections: list[Section], period: FiscalPeriod) -> LoadPlan:
    plan = LoadPlan(period=period)
    for section in sections:
        # Validate the WHOLE section (all dates) - the running balance only
        # re-derives correctly over the full sequence of rows.
        result = validate_section(section)
        if not result.is_valid:
            plan.failures.append({
                "account": section.account_label,
                "row": result.first_mismatch_row,
                "computed": result.computed_ending,
                "stated": result.stated_ending,
            })
            continue

        in_period = []
        for r in section.rows:
            d = _to_date(r.txn_date)
            if d is None:
                plan.rows_bad_date += 1
            elif period.start <= d <= period.end:
                r.txn_date = d
                in_period.append(r)
            else:
                plan.rows_outside_period += 1
        plan.rows_in_period += len(in_period)
        if in_period:
            plan.sections_ok.append((section, in_period))
    return plan


def _count_existing(conn, company_id, period):
    row = conn.execute(
        text("""SELECT COUNT(DISTINCT je.JournalEntryID), COUNT(jel.LineID)
                 FROM qb.JournalEntries je
                 LEFT JOIN qb.JournalEntryLines jel ON jel.JournalEntryID = je.JournalEntryID
                 WHERE je.CompanyID = :cid AND je.TxnDate BETWEEN :s AND :e"""),
        {"cid": company_id, "s": period.start, "e": period.end},
    ).fetchone()
    return int(row[0]), int(row[1])


def _log_run(conn, company_id, period, source_file, status, report):
    conn.execute(
        text("""INSERT INTO qb.LoadRuns
                    (CompanyID, FiscalYearEnd, PeriodStart, PeriodEnd, SourceFile, Status,
                     EntriesDeleted, LinesDeleted, EntriesWritten, LinesWritten,
                     SectionsLoaded, SectionsSkipped, RowsOutsidePeriod, Notes)
                 VALUES (:cid, :fy, :s, :e, :src, :status,
                         :ed, :ld, :ew, :lw, :sl, :ss, :rop, :notes)"""),
        {"cid": company_id, "fy": period.fy_end_year, "s": period.start, "e": period.end,
         "src": source_file, "status": status,
         "ed": report.get("entries_deleted", 0), "ld": report.get("lines_deleted", 0),
         "ew": report.get("entries_written", 0), "lw": report.get("lines_written", 0),
         "sl": report.get("loaded_sections", 0), "ss": report.get("skipped_sections", 0),
         "rop": report.get("rows_outside_period", 0), "notes": report.get("notes")},
    )


# =====================================================================
# Orchestrator
# =====================================================================

def load_company(engine, company_code, account_list_path, transaction_detail_path,
                 fy_end_year, legal_name=None, source_file_label=None,
                 allow_partial=False, dry_run=False):
    """
    Replace one company's ledger for one fiscal year with the contents
    of the given exports.

    allow_partial=False (default): if ANY account section fails its
        running-balance check, nothing is changed and the old load stays.
    allow_partial=True: reload anyway, leaving failed accounts with NO
        rows for that year (they're listed in the report and LoadRuns).
    dry_run=True: parse, validate and report what would happen; no writes.
    """
    source_file = source_file_label or os.path.basename(transaction_detail_path)

    company_id, start_month = get_company(engine, company_code)
    period = fiscal_period(fy_end_year, start_month)
    print(f"📅 {company_code}: {period}")

    coa_rows = parse_account_list(account_list_path)
    sections = parse_transaction_detail(transaction_detail_path)
    plan = build_plan(sections, period)

    report = {
        "loaded_sections": len(plan.sections_ok),
        "skipped_sections": len(plan.failures),
        "rows_outside_period": plan.rows_outside_period,
        "failures": plan.failures,
    }

    print(f"   Parsed {len(coa_rows)} accounts, {len(sections)} account sections")
    print(f"   Rows in FY: {plan.rows_in_period:,}   outside FY (ignored): {plan.rows_outside_period:,}"
          + (f"   unreadable date: {plan.rows_bad_date:,}" if plan.rows_bad_date else ""))
    for f in plan.failures:
        print(f"   ⚠️ FAILED CHECK {f['account']}: computed {f['computed']} vs stated {f['stated']} "
              f"(first mismatch around row {f['row']})")

    # --- Safety stops (nothing has been written yet) ------------------
    abort_reason = None
    if plan.failures and not allow_partial:
        abort_reason = (f"{len(plan.failures)} account section(s) failed the balance check - "
                        f"nothing changed. Fix the export or rerun with --allow-partial.")
    elif plan.rows_in_period == 0:
        abort_reason = (f"no transactions in the export fall inside {period} - "
                        f"wrong file or wrong --fy-end?")

    if dry_run:
        if company_id is not None:
            with engine.connect() as conn:
                e, l = _count_existing(conn, company_id, period)
            print(f"   [dry run] would delete {e:,} entries / {l:,} lines currently in SQL for this FY")
        else:
            print("   [dry run] company not in SQL yet - nothing would be deleted")
        print(f"   [dry run] {'WOULD ABORT: ' + abort_reason if abort_reason else 'would load OK'}")
        report["status"] = "DryRun"
        return company_id, report

    company_id = get_or_create_company(engine, company_code, legal_name)

    if abort_reason:
        print(f"🛑 {company_code}: aborted - {abort_reason}")
        report["status"] = "Aborted"
        report["notes"] = abort_reason[:400]
        with engine.begin() as conn:
            _log_run(conn, company_id, period, source_file, "Aborted", report)
        return company_id, report

    # --- Dimensions (committed separately; safe to keep even on rollback)
    coa_cache = load_chart_of_accounts(engine, company_id, coa_rows)
    class_ids = resolve_classes(engine, company_id,
                                (r.class_name for _, rows in plan.sections_ok for r in rows))
    account_ids = {id(sec): _resolve_chart_account_id(engine, company_id, sec.account_label, coa_cache)
                   for sec, _ in plan.sections_ok}

    # --- The reload: delete the FY and re-insert, all-or-nothing -------
    with engine.begin() as conn:
        report["entries_deleted"], report["lines_deleted"] = _count_existing(conn, company_id, period)
        conn.execute(
            text("""DELETE FROM qb.JournalEntries
                     WHERE CompanyID = :cid AND TxnDate BETWEEN :s AND :e"""),
            {"cid": company_id, "s": period.start, "e": period.end},
        )

        ref_entries = {}   # (TxnType, TxnDate, RefNum) -> JournalEntryID, for this load only
        lines = []
        entries_written = 0

        for section, rows in plan.sections_ok:
            chart_account_id = account_ids[id(section)]
            for r in rows:
                key = (r.txn_type, r.txn_date, r.ref_num)
                je_id = ref_entries.get(key) if r.ref_num else None
                if je_id is None:
                    je_id = conn.execute(
                        text("""INSERT INTO qb.JournalEntries
                                    (CompanyID, TxnType, TxnDate, RefNum, Name, Memo, OccurrenceRank, SourceFile)
                                 OUTPUT INSERTED.JournalEntryID
                                 VALUES (:cid, :ttype, :tdate, :ref, :name, :memo, :rank, :src)"""),
                        {"cid": company_id, "ttype": r.txn_type, "tdate": r.txn_date, "ref": r.ref_num,
                         "name": r.name, "memo": r.memo, "rank": r.occurrence_rank, "src": source_file},
                    ).fetchone()[0]
                    entries_written += 1
                    if r.ref_num:
                        ref_entries[key] = je_id
                lines.append({"je": je_id, "acct": chart_account_id,
                              "cls": class_ids.get(r.class_name) if r.class_name else None,
                              "amt": round(r.amount, 2), "memo": r.memo})

        if lines:
            conn.execute(
                text("""INSERT INTO qb.JournalEntryLines (JournalEntryID, ChartAccountID, ClassID, Amount, LineMemo)
                         VALUES (:je, :acct, :cls, :amt, :memo)"""),
                lines,
            )

        report["entries_written"] = entries_written
        report["lines_written"] = len(lines)
        status = "Loaded (partial)" if plan.failures else "Loaded"
        report["status"] = status
        if plan.failures:
            report["notes"] = ("Skipped: " + "; ".join(f["account"] for f in plan.failures))[:400]
        _log_run(conn, company_id, period, source_file, status, report)

    print(f"✅ {company_code}: replaced FY{period.fy_end_year} - removed {report['entries_deleted']:,} entries / "
          f"{report['lines_deleted']:,} lines, wrote {report['entries_written']:,} entries / "
          f"{report['lines_written']:,} lines from {report['loaded_sections']} account sections"
          + (f" ({report['skipped_sections']} skipped)" if plan.failures else ""))
    return company_id, report
