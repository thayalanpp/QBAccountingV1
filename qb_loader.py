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

Auto-apply rule (per the project's own decision): a section only gets
loaded if its own running balance re-derives cleanly from the parsed
rows. If it doesn't, that section is skipped and reported - nothing
partial gets written for it. New Company / ChartOfAccounts / Class rows
are created automatically as they're encountered (that's just normal
dimension growth, not a structural schema change).

JournalEntry grouping (tested against all three companies' real files,
see _find_or_create_journal_entry): this report shows every transaction
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
auto-apply gate - is unaffected.
"""

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

_engine = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine

    server = os.getenv("DB_SERVER")
    database = os.getenv("DB_NAME")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    trusted = os.getenv("DB_TRUSTED", "yes").lower() == "yes"

    if not server or not database:
        raise RuntimeError("DB_SERVER and DB_NAME must be set before qb_loader can connect.")

    if trusted:
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};Trusted_Connection=yes;TrustServerCertificate=yes;"
    else:
        user, password = os.getenv("DB_USER"), os.getenv("DB_PASSWORD")
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={user};PWD={password};TrustServerCertificate=yes;"

    _engine = create_engine(f"mssql+pyodbc:///?odbc_connect={odbc_str}", fast_executemany=True)
    return _engine


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
# Dimension get-or-create (auto-applied - these are just new data,
# not structural schema changes)
# =====================================================================

def get_or_create_company(engine, company_code, legal_name=None):
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"),
            {"c": company_code},
        ).fetchone()
        if existing:
            return existing[0]
        result = conn.execute(
            text("""INSERT INTO qb.Companies (CompanyCode, LegalName)
                     OUTPUT INSERTED.CompanyID
                     VALUES (:code, :name)"""),
            {"code": company_code, "name": legal_name or company_code},
        ).fetchone()
        return result[0]


def get_or_create_class(engine, company_id, class_name):
    if not class_name:
        return None
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT ClassID FROM qb.Classes WHERE CompanyID = :cid AND ClassName = :name"),
            {"cid": company_id, "name": class_name},
        ).fetchone()
        if existing:
            return existing[0]
        result = conn.execute(
            text("""INSERT INTO qb.Classes (CompanyID, ClassName)
                     OUTPUT INSERTED.ClassID
                     VALUES (:cid, :name)"""),
            {"cid": company_id, "name": class_name},
        ).fetchone()
        return result[0]


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


def _resolve_chart_account_id(engine, company_id, account_label, cache):
    """
    account_label looks like "1010 · Bank - Scotia A/C 26492 0068810".
    Pull the leading account number and look it up (via the cache built
    by load_chart_of_accounts, falling back to a live query for accounts
    that only ever appear in the detail export, not the Account Listing).
    """
    m = re.match(r"^([\w.]+)\s*·", account_label)
    number = m.group(1) if m else account_label
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


def _find_or_create_journal_entry(conn, company_id, txn_type, txn_date, ref_num, name, memo,
                                   occurrence_rank, source_file):
    """
    Groups rows into one JournalEntry only when there's a signal reliable
    enough to trust: a QuickBooks-assigned RefNum (cheque #, journal entry
    #). RefNum is the only field this export keeps consistent across every
    account a transaction touches. Name/Memo/Class are NOT reliably
    consistent across accounts for the same real-world transaction here -
    testing against all three companies' real files found bank deposits
    with a generic "Deposit" memo on the bank side but itemized per-payment
    memos on the income side, and payroll cheques whose liability/expense
    legs can post multiple separate lines to the very same account. Text
    heuristics over those fields produced both false merges (unrelated
    transactions collapsed together) and false splits (one transaction's
    own lines scattered across separate "entries") in testing. So: RefNum
    present -> reuse/merge into the existing entry for that
    (company, type, date, ref). RefNum absent -> always insert a new,
    one-line entry; never guess a grouping. Either way every row's dollar
    amount lands under the correct ChartAccountID - validate_section()'s
    per-account running-balance check is what actually gates loading, and
    is unaffected by this. occurrence_rank is stored for reference only.
    """
    if ref_num:
        existing = conn.execute(
            text("""SELECT JournalEntryID FROM qb.JournalEntries
                     WHERE CompanyID = :cid AND TxnType = :ttype AND TxnDate = :tdate
                       AND RefNum = :ref"""),
            {"cid": company_id, "ttype": txn_type, "tdate": txn_date, "ref": ref_num},
        ).fetchone()
        if existing:
            return existing[0]

    result = conn.execute(
        text("""INSERT INTO qb.JournalEntries (CompanyID, TxnType, TxnDate, RefNum, Name, Memo, OccurrenceRank, SourceFile)
                 OUTPUT INSERTED.JournalEntryID
                 VALUES (:cid, :ttype, :tdate, :ref, :name, :memo, :rank, :src)"""),
        {"cid": company_id, "ttype": txn_type, "tdate": txn_date,
         "ref": ref_num, "name": name, "memo": memo, "rank": occurrence_rank, "src": source_file},
    ).fetchone()
    return result[0]


def load_transactions(engine, company_id, sections: list[Section], source_file, coa_cache):
    """
    The auto-apply gate: validates every section BEFORE writing anything.
    Sections that fail validation are skipped and reported; valid ones
    are loaded in full. Returns a report dict.
    """
    report = {"loaded_sections": 0, "skipped_sections": 0, "lines_written": 0, "failures": []}

    for section in sections:
        result = validate_section(section)
        if not result.is_valid:
            report["skipped_sections"] += 1
            report["failures"].append({
                "account": section.account_label,
                "row": result.first_mismatch_row,
                "computed": result.computed_ending,
                "stated": result.stated_ending,
            })
            continue

        chart_account_id = _resolve_chart_account_id(engine, company_id, section.account_label, coa_cache)

        with engine.begin() as conn:
            for r in section.rows:
                class_id = get_or_create_class(engine, company_id, r.class_name) if r.class_name else None
                je_id = _find_or_create_journal_entry(
                    conn, company_id, r.txn_type, r.txn_date, r.ref_num, r.name, r.memo,
                    r.occurrence_rank, source_file
                )
                conn.execute(
                    text("""INSERT INTO qb.JournalEntryLines (JournalEntryID, ChartAccountID, ClassID, Amount, LineMemo)
                             VALUES (:je, :acct, :cls, :amt, :memo)"""),
                    {"je": je_id, "acct": chart_account_id, "cls": class_id, "amt": r.amount, "memo": r.memo},
                )
                report["lines_written"] += 1

        report["loaded_sections"] += 1

    return report


# =====================================================================
# Orchestrator
# =====================================================================

def load_company(engine, company_code, account_list_path, transaction_detail_path,
                  legal_name=None, source_file_label=None):
    company_id = get_or_create_company(engine, company_code, legal_name)

    coa_rows = parse_account_list(account_list_path)
    coa_cache = load_chart_of_accounts(engine, company_id, coa_rows)
    print(f"✅ {company_code}: {len(coa_rows)} chart-of-accounts rows loaded")

    sections = parse_transaction_detail(transaction_detail_path)
    report = load_transactions(
        engine, company_id, sections,
        source_file=source_file_label or os.path.basename(transaction_detail_path),
        coa_cache=coa_cache,
    )
    print(f"✅ {company_code}: {report['loaded_sections']} account sections loaded "
          f"({report['lines_written']} lines), {report['skipped_sections']} skipped")
    for f in report["failures"]:
        print(f"   ⚠️ SKIPPED {f['account']}: computed {f['computed']} vs stated {f['stated']} "
              f"(first mismatch around row {f['row']})")

    return company_id, report
