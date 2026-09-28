"""
bank_readers.py

Turns a bank's transaction export into a BankExport. Plain Python, no AI.

Both readers accept Excel (.xlsx / .xls) or CSV.

Scotiabank export
    Filter | Date | Description | Sub-description | Type of Transaction | Amount | Balance
    - Newest first; the running balance only chains in the bank's own row
      order, so rows are reversed (never re-sorted by date).
    - Row 1 of 'Filter' holds the export's date range.
    - "cheque  79" -> description "cheque", cheque number 79.
    - Any EXTRA columns you add are kept: text columns become the line's
      tag (e.g. "hyg", "split"), number columns become allocations
      (e.g. salary / rent / lease / bookkeeping / visa for a split cheque).
    - If the Balance column is missing, balances can only come from a
      known statement balance (an "anchor") - see bank_validator.py.

TD account-activity export
    date | description | amount | amount | balance
    - Oldest first. The two amount columns aren't reliably labelled (in
      the MGMT file "credit" holds withdrawals), so direction is decided
      by which reading makes the running balance chain - not by headers.

Raises BankReadError when the file isn't a layout this module knows.
"""

import os
import re
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from bank_models import BankExport, BankTxn


class BankReadError(Exception):
    pass


def _norm(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def _to_date(v) -> Optional[date]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _num(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_table(path: str, header, sheet: Optional[str] = None) -> pd.DataFrame:
    """Excel (.xlsx/.xls) or CSV - banks offer both. In a workbook with one sheet per company
    (e.g. sheets 'dntl' and 'hyg'), the sheet named after the company is used."""
    if path.lower().endswith(".csv"):
        return pd.read_csv(path, header=header, dtype=object, encoding_errors="replace", skip_blank_lines=True)
    name = 0
    if sheet:
        names = pd.ExcelFile(path).sheet_names
        name = next((n for n in names if n.strip().lower() == sheet.lower()), 0)
    return pd.read_excel(path, sheet_name=name, header=header, dtype=object)


# ---------------------------------------------------------------------
# Scotiabank
# ---------------------------------------------------------------------
SCOTIA_COLUMNS = {"filter", "date", "description", "sub-description", "type of transaction", "amount", "balance"}


def read_scotia_export(path: str, sheet: Optional[str] = None, window=None) -> BankExport:
    df = _read_table(path, header=0, sheet=sheet)
    cols = {str(c).strip().lower(): c for c in df.columns}
    for needed in ("date", "description", "amount"):
        if needed not in cols:
            raise BankReadError(f"not a Scotiabank export - no '{needed}' column")
    c_date, c_desc, c_amt = cols["date"], cols["description"], cols["amount"]
    c_sub, c_type = cols.get("sub-description"), cols.get("type of transaction")
    c_bal, c_filter = cols.get("balance"), cols.get("filter")

    # Extra (user) columns: numeric -> allocation, text -> tag
    extra = [c for c in df.columns if str(c).strip().lower() not in SCOTIA_COLUMNS]
    split_cols, tag_cols = [], []
    for c in extra:
        vals = df[c].dropna()
        vals = vals[vals.astype(str).str.strip() != ""]
        if vals.empty:
            continue
        (split_cols if all(_num(v) is not None for v in vals) else tag_cols).append(c)

    filter_from = filter_to = None
    if c_filter is not None:
        text = " ".join(str(v) for v in df[c_filter].dropna())
        m1, m2 = re.search(r"From date=(\d{4}-\d{2}-\d{2})", text), re.search(r"To date=(\d{4}-\d{2}-\d{2})", text)
        filter_from = _to_date(m1.group(1)) if m1 else None
        filter_to = _to_date(m2.group(1)) if m2 else None

    # Some exports give every amount as positive and the direction only in "Type of Transaction"
    unsigned = False
    if c_type is not None:
        amts = [a for a in (_num(v) for v in df[c_amt]) if a is not None]
        types = df[c_type].astype(str).str.strip().str.lower()
        unsigned = bool(amts) and min(amts) >= 0 and (types == "debit").any()

    rows, mismatches = [], []
    for i, r in df.iterrows():
        d = _to_date(r[c_date])
        amt = _num(r[c_amt])
        if d is None or amt is None:
            continue
        if unsigned and _norm(r[c_type]).lower() == "debit":
            amt = -amt
        desc = _norm(r[c_desc])
        cheque = None
        m = re.match(r"^cheque\s+(\d+)$", desc, re.IGNORECASE)
        if m:
            desc, cheque = "cheque", m.group(1)
        typ = _norm(r[c_type]).lower() if c_type is not None else ""
        if (typ == "debit" and amt > 0) or (typ == "credit" and amt < 0):
            mismatches.append(i + 2)
        tag = "; ".join(_norm(r[c]) for c in tag_cols if _norm(r[c])) or None
        splits = {}
        for c in split_cols:
            v = _num(r[c])
            if v:
                splits[_norm(c).lower()] = round(v, 2)
        rows.append(dict(txn_date=d, description=desc, sub_description=(_norm(r[c_sub]) or None) if c_sub is not None else None,
                         amount=round(amt, 2), balance=round(_num(r[c_bal]), 2) if c_bal is not None and _num(r[c_bal]) is not None else None,
                         cheque_number=cheque, tag=tag, splits=splits, source_row=i + 2))

    if not rows:
        raise BankReadError("no transactions found")
    if rows[0]["txn_date"] > rows[-1]["txn_date"]:      # newest first -> oldest first, keeping the bank's order
        rows.reverse()

    notes = []
    if unsigned:
        notes.append("amounts were all positive - direction taken from Debit / Credit")
    if c_bal is not None and all(r["balance"] is not None for r in rows):
        breaks = sum(1 for a, b in zip(rows, rows[1:]) if _cents(a["balance"]) + _cents(b["amount"]) != _cents(b["balance"]))
        if breaks:
            # Rebuild over the year plus a margin: the bank can post a line dated just after the
            # year-end BEFORE one dated just before it, so the chain must be allowed to cross it.
            lo = hi = None
            if window:
                lo, hi = window
                margin = timedelta(days=14)
                rows = [r for r in rows if lo - margin <= r["txn_date"] <= hi + margin]
            ordered, strays = _rebuild_order(rows)
            notes.append(f"rows weren't in the bank's order ({breaks} balance breaks) - order rebuilt from the balances"
                         + (f" for {lo}..{hi} (+/- 14 days)" if window else ""))
            inside = [r for r in strays if not window or lo <= r["txn_date"] <= hi]
            outside = [r for r in strays if r not in inside]
            for r in inside:
                notes.append(f"row {r['source_row']} ({r['txn_date']} {r['description']} {r['amount']:,.2f}, balance "
                             f"{r['balance']:,.2f}) doesn't fit the bank's running balances - duplicate or edited?")
            if outside:
                notes.append(f"{len(outside)} row(s) dated outside the year don't fit the balances either - left out "
                             f"(they belong to the neighbouring year's load): rows "
                             + ", ".join(str(r['source_row']) for r in outside[:10]) + ("..." if len(outside) > 10 else ""))
            rows = ordered + inside
    txns = [BankTxn(seq=n, source_file=os.path.basename(path), **r) for n, r in enumerate(rows, start=1)]

    if c_bal is None:
        notes.append("export has no Balance column")
    if tag_cols or split_cols:
        notes.append(f"kept your extra columns: tags {[_norm(c) for c in tag_cols]}, allocations {[_norm(c) for c in split_cols]}")
    return BankExport(source_file=os.path.basename(path), reader="scotia", filter_from=filter_from, filter_to=filter_to,
                      has_bank_balances=c_bal is not None and all(t.balance is not None for t in txns),
                      transactions=txns, type_mismatches=mismatches, notes=notes)


def _cents(x) -> int:
    return int(round(float(x) * 100))


def _rebuild_order(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Rebuild the bank's order when a file was re-sorted: each row's balance BEFORE it
    (balance - amount) must be the balance AFTER the row before it. Starting from the
    opening (a 'before' balance no row ends at), follow the links. Zero-sum groups
    (e.g. an e-transfer and its return) that leave the balance where it was are put
    back where that balance occurs. Rows that fit nowhere are returned as strays.
    """
    from collections import defaultdict
    by_before = defaultdict(list)
    for r in rows:
        by_before[_cents(r["balance"]) - _cents(r["amount"])].append(r)
    afters = {_cents(r["balance"]) for r in rows}

    def walk(start, pool_ids):
        chain, cur = [], start
        while True:
            nxt = [r for r in by_before.get(cur, []) if id(r) in pool_ids]
            if not nxt:
                return chain
            r = min(nxt, key=lambda r: (r["txn_date"], r["source_row"]))
            pool_ids.discard(id(r)); chain.append(r); cur = _cents(r["balance"])

    best = []
    for start in [b for b in by_before if b not in afters] or list(by_before)[:1]:
        chain = walk(start, {id(r) for r in rows})
        if len(chain) > len(best):
            best = chain
    used = {id(r) for r in best}
    left = [r for r in rows if id(r) not in used]

    # re-insert zero-sum loops where their balance occurs in the chain
    changed = True
    while left and changed:
        changed = False
        for r in sorted(left, key=lambda r: (r["txn_date"], r["source_row"])):
            start = _cents(r["balance"]) - _cents(r["amount"])
            pool = {id(x) for x in left}
            loop = walk(start, pool)
            if loop and _cents(loop[-1]["balance"]) == start:
                spots = [i for i, x in enumerate(best) if _cents(x["balance"]) == start and x["txn_date"] <= loop[0]["txn_date"]]
                opening = _cents(best[0]["balance"]) - _cents(best[0]["amount"]) if best else None
                pos = spots[-1] + 1 if spots else (0 if start == opening else None)
                if pos is not None:
                    best[pos:pos] = loop
                    ids = {id(x) for x in loop}
                    left = [x for x in left if id(x) not in ids]
                    changed = True
                    break
    return best, sorted(left, key=lambda r: (r["txn_date"], r["source_row"]))


# ---------------------------------------------------------------------
# TD
# ---------------------------------------------------------------------
def read_td_export(path: str) -> BankExport:
    df = _read_table(path, header=None)
    if df.shape[1] < 5:
        raise BankReadError("not a TD export - expected date, description, two amount columns and balance")
    raw = []
    for i, r in df.iterrows():
        d = _to_date(r[0])
        bal = _num(r[4])
        if d is None or bal is None:
            continue                    # header or blank row
        raw.append(dict(txn_date=d, description=_norm(r[1]), a=_num(r[2]) or 0.0, b=_num(r[3]) or 0.0,
                        balance=round(bal, 2), source_row=i + 1))
    if not raw:
        raise BankReadError("no transactions found")
    if raw[0]["txn_date"] > raw[-1]["txn_date"]:
        raw.reverse()

    # Which amount column is the withdrawal? Pick the reading that makes the balance chain.
    def chain_hits(sign_a):
        hits = 0
        for prev, cur in zip(raw, raw[1:]):
            amount = sign_a * cur["a"] - sign_a * cur["b"]
            hits += round(cur["balance"] - prev["balance"], 2) == round(amount, 2)
        return hits
    a_is_deposit = chain_hits(+1) > chain_hits(-1)
    sign_a = 1 if a_is_deposit else -1

    txns = [BankTxn(seq=n, txn_date=r["txn_date"], description=r["description"], sub_description=None,
                    amount=round(sign_a * r["a"] - sign_a * r["b"], 2), balance=r["balance"], source_row=r["source_row"],
                    source_file=os.path.basename(path))
            for n, r in enumerate(raw, start=1)]
    notes = [f"direction taken from the balance chain: column 3 = {'deposits' if a_is_deposit else 'withdrawals'}, "
             f"column 4 = {'withdrawals' if a_is_deposit else 'deposits'} (headers ignored)"]
    return BankExport(source_file=os.path.basename(path), reader="td", has_bank_balances=True,
                      transactions=txns, notes=notes)


READERS = {"scotia": read_scotia_export, "td": read_td_export}


# ---------------------------------------------------------------------
# Several exports for one account (e.g. your tagged file + a top-up export)
# ---------------------------------------------------------------------
def merge_exports(exports: list[BankExport]) -> BankExport:
    """
    Joins exports that cover consecutive, NON-overlapping date ranges into one.
    Overlapping files are refused - two copies of the same day can't be told
    apart from two real identical payments.
    Each file that carries the bank's running balance also contributes known
    balances (its opening and closing) so a file without balances can be checked.
    """
    if len(exports) == 1:
        exp = exports[0]
    else:
        exports = sorted(exports, key=lambda e: e.first_date)
        for a, b in zip(exports, exports[1:]):
            if b.first_date <= a.last_date:
                raise BankReadError(f"{a.source_file} ({a.first_date}..{a.last_date}) and {b.source_file} "
                                    f"({b.first_date}..{b.last_date}) overlap - export the second one starting "
                                    f"the day after {a.last_date}")
        txns, notes, mism = [], [], []
        for e in exports:
            for t in e.transactions:
                txns.append(t.model_copy(update={"seq": len(txns) + 1}))
            notes += [f"{e.source_file}: {n}" for n in e.notes]
            mism += e.type_mismatches
        exp = BankExport(source_file=" + ".join(e.source_file for e in exports), reader=exports[0].reader,
                         filter_from=exports[0].filter_from, filter_to=exports[-1].filter_to,
                         has_bank_balances=all(e.has_bank_balances for e in exports),
                         transactions=txns, type_mismatches=mism, notes=notes)
        exp.anchors = [a for e in exports for a in e.anchors]
    if not exp.has_bank_balances:
        by_file = {}
        for t in exp.transactions:
            by_file.setdefault(t.source_file, []).append(t)
        for name, ts in by_file.items():
            with_bal = [t for t in ts if t.balance is not None]
            if not with_bal:
                continue
            first, last = with_bal[0], with_bal[-1]
            # opening = the balance at the end of the previous day - valid only if no line
            # WITHOUT a bank balance shares that first day (e.g. a combined file whose older
            # lines came from a file without balances)
            if not any(t.balance is None and t.txn_date == first.txn_date for t in ts):
                exp.anchors.append((first.txn_date - timedelta(days=1), round(first.balance - first.amount, 2),
                                    f"{name} bank balance before {first.txn_date}"))
            if not any(t.balance is None and t.txn_date == last.txn_date for t in ts):
                exp.anchors.append((last.txn_date, last.balance, f"{name} bank balance {last.txn_date}"))
    return exp


# ---------------------------------------------------------------------
# Known balances from Scotiabank monthly PDF statements
# ---------------------------------------------------------------------
def read_scotia_statement_balances(pdf_path: str) -> Optional[dict]:
    """
    Reads only the header of a Scotiabank business e-statement:
    account number, From / To dates, BALANCE FORWARD and the period totals.
    Returns {"account": digits, "anchors": [(from_date, opening, label), (to_date, closing, label)]}
    or None if it isn't that layout. Transactions are NOT read - the export has those.
    """
    import pdfplumber
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first = pdf.pages[0].extract_text(x_tolerance=1.5) or ""
            last = pdf.pages[-1].extract_text(x_tolerance=1.5) or ""
            every = "\n".join((p.extract_text(x_tolerance=1.5) or "") for p in pdf.pages)
    except Exception:
        return None
    head = re.search(r"Business Account\s+(\d[\d ]+\d)\s+([A-Z][a-z]{2} \d{1,2} \d{4})\s+([A-Z][a-z]{2} \d{1,2} \d{4})", first)
    fwd = re.search(r"BALANCE FORWARD\s+([\d,]+\.?\d*)", first)
    tot = re.search(r"Total Amount - Credits\s*\n\s*\d+\s+\$([\d,]+\.\d{2})\s+\d+\s+\$([\d,]+\.\d{2})", first)
    if not (head and fwd and tot):
        return None
    d_from = datetime.strptime(head.group(2), "%b %d %Y").date()
    d_to = datetime.strptime(head.group(3), "%b %d %Y").date()
    opening = _num(fwd.group(1))
    closing = round(opening - _num(tot.group(1)) + _num(tot.group(2)), 2)
    # Cross-check: the last balance printed on the statement should equal opening - debits + credits
    balances = re.findall(r"^\d{2}/\d{2}/\d{4}\s.*?\s([\d,]+\.?\d*)\s*$", every, re.MULTILINE)
    name = os.path.basename(pdf_path)
    anchors = [(d_from, opening, f"{name} balance forward")]
    if balances and round(_num(balances[-1]), 2) == closing:
        anchors.append((d_to, closing, f"{name} closing"))
    return {"account": re.sub(r"\D", "", head.group(1)), "anchors": anchors}


def read_bank_export(path: str, reader: str, sheet: Optional[str] = None, window=None) -> BankExport:
    """sheet: in a workbook with one sheet per company, the company's sheet (e.g. 'dntl').
    window: (from, to) - if the rows have to be put back in the bank's order, only this
    date range is used (e.g. the fiscal year), so a damaged part outside it can't interfere."""
    if reader not in READERS:
        raise BankReadError(f"unknown reader '{reader}' - use one of {sorted(READERS)}")
    if reader == "scotia":
        return read_scotia_export(path, sheet=sheet, window=window)
    return READERS[reader](path)
