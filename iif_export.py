"""
iif_export.py - writes the APPROVED proposed entries (fin.ProposedEntries) as a QuickBooks Desktop IIF file,
for File > Utilities > Import > IIF Files in a BACKUP COPY of the company file.

    python iif_export.py --company dntl                       # Approved, not yet exported
    python iif_export.py --company dntl --entries JE-1,JE-5   # only these (must still be Approved)
    python iif_export.py --company dntl --include-exported    # regenerate entries already exported
    python iif_export.py --company dntl --date-format dmy     # if QuickBooks reads dates day-first
    python iif_export.py --company dntl --dry-run             # print, write nothing, stamp nothing

Only Status = 'Approved' is ever exported - Draft, Rejected, Superseded and Posted are not.
Refuses the whole file if any selected entry does not balance, has a leg without an account, or uses an
account that is neither in qb.ChartOfAccounts nor in proposed_entries.NEW_ACCOUNTS (those get an !ACCNT row
so QuickBooks creates them, e.g. 39900 Suspense). Accounts are written by full QuickBooks name
("Parent:Child"), which is what IIF matches on.

General Journal entries -> TRNSTYPE GENERAL JOURNAL. Credit Card Charge entries (JE-6) -> TRNSTYPE CREDIT CARD,
card leg first, if the card account is a Credit Card account in QuickBooks (otherwise written as a journal).
After writing, each entry gets IIFFile / IIFExportedAt; mark them Posted with proposed_entries.py once imported.
"""

import argparse
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from proposed_entries import NEW_ACCOUNTS

DATE_FORMATS = {"mdy": "%m/%d/%Y", "dmy": "%d/%m/%Y", "ymd": "%Y-%m-%d"}
TXN_TYPES = {"General Journal": "GENERAL JOURNAL", "Credit Card Charge": "CREDIT CARD"}
NEEDS_NAME = {"accounts receivable", "accounts payable"}


@dataclass
class IIFLine:
    account: Optional[str]          # company account number
    amount: float                   # debit +, credit -
    memo: Optional[str] = None
    name: Optional[str] = None
    hint: Optional[str] = None


@dataclass
class IIFEntry:
    key: str
    entry_date: date
    txn_type: str                   # 'General Journal' | 'Credit Card Charge'
    memo: Optional[str]
    name: Optional[str]
    lines: List[IIFLine]
    status: str = "Approved"


class IIFError(Exception):
    pass


def _clean(s) -> str:
    """IIF fields are tab-separated, one record per line: no tabs, newlines or double quotes inside a field."""
    return "" if s is None else str(s).replace("\t", " ").replace("\r", " ").replace("\n", " ").replace('"', "'").strip()


def _amt(x) -> str:
    return f"{float(x):.2f}"


def validate(entries: List[IIFEntry], accounts: Dict[str, Tuple[str, str]], new_accounts=NEW_ACCOUNTS) -> List[str]:
    """accounts: number -> (full QuickBooks name, QuickBooks account type). Returns problems (empty = fine)."""
    problems = []
    for e in entries:
        if e.status != "Approved":
            problems.append(f"{e.key}: status {e.status} - only Approved entries are exported")
        if sum(int(round(l.amount * 100)) for l in e.lines) != 0:
            problems.append(f"{e.key}: does not balance")
        if e.txn_type not in TXN_TYPES:
            problems.append(f"{e.key}: unknown transaction type {e.txn_type}")
        for i, l in enumerate(e.lines, 1):
            if not l.account:
                problems.append(f"{e.key} line {i}: no account yet ({l.hint or 'no hint'})")
            elif l.account not in accounts and l.account not in new_accounts:
                problems.append(f"{e.key} line {i}: account {l.account} is not in QuickBooks or NEW_ACCOUNTS")
            elif l.account in accounts and accounts[l.account][1].lower() in NEEDS_NAME and not l.name:
                problems.append(f"{e.key} line {i}: {accounts[l.account][0]} needs a customer/vendor name")
    return problems


def render(entries: List[IIFEntry], accounts: Dict[str, Tuple[str, str]], date_format="mdy",
           new_accounts=NEW_ACCOUNTS, card_account="22200") -> Tuple[str, List[str]]:
    """Returns (IIF text, notes). Raises IIFError if anything would not import cleanly."""
    problems = validate(entries, accounts, new_accounts)
    if problems:
        raise IIFError("\n".join(problems))
    fmt = DATE_FORMATS[date_format]
    notes, out = [], []

    def acct_name(num):
        return accounts[num][0] if num in accounts else new_accounts[num][0]

    used_new = sorted({l.account for e in entries for l in e.lines if l.account not in accounts})
    if used_new:
        out.append("!ACCNT\tNAME\tACCNTTYPE\tACCNUM\tDESC")
        for num in used_new:
            nm, typ, desc = new_accounts[num]
            out.append("\t".join(["ACCNT", _clean(nm), typ, num, _clean(desc)]))
            notes.append(f"creates account {num} {nm} ({typ})")

    out += ["!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO",
            "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO",
            "!ENDTRNS"]
    for e in entries:
        ttype, lines = TXN_TYPES[e.txn_type], list(e.lines)
        if e.txn_type == "Credit Card Charge":
            card_type = accounts.get(card_account, ("", ""))[1].lower()
            card_legs = [l for l in lines if l.account == card_account]
            if card_type == "credit card" and len(card_legs) == 1:
                lines = card_legs + [l for l in lines if l is not card_legs[0]]
            else:
                ttype = "GENERAL JOURNAL"
                notes.append(f"{e.key}: written as a General Journal ({card_account} is not a Credit Card account "
                             f"in QuickBooks, or the entry has more than one card leg)")
        d = e.entry_date.strftime(fmt)
        for i, l in enumerate(lines):
            name = l.name or (e.name if ttype == "CREDIT CARD" else None)
            memo = l.memo or e.memo
            out.append("\t".join(["TRNS" if i == 0 else "SPL", "", ttype, d, _clean(acct_name(l.account)),
                                  _clean(name), _amt(l.amount), _clean(e.key), _clean(memo)]))
        out.append("ENDTRNS")
    return "\r\n".join(out) + "\r\n", notes


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------
def load(engine, company, keys=None, include_exported=False):
    from sqlalchemy import text
    with engine.connect() as conn:
        cid = conn.execute(text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"), {"c": company}).scalar()
        if cid is None:
            raise SystemExit(f"Company '{company}' is not in qb.Companies")
        chart = conn.execute(text("""SELECT ChartAccountID, AccountNumber, AccountName, AccountType, ParentChartAccountID
                                     FROM qb.ChartOfAccounts WHERE CompanyID = :cid"""), {"cid": cid}).fetchall()
        sql = """SELECT EntryID, EntryKey, EntryDate, QBTxnType, Memo, Name, Status, IIFExportedAt
                 FROM fin.ProposedEntries WHERE CompanyID = :cid AND Status = 'Approved'"""
        hdrs = conn.execute(text(sql + " ORDER BY EntryDate, EntryKey"), {"cid": cid}).fetchall()
        if keys:
            missing = set(keys) - {h.EntryKey for h in hdrs}
            if missing:
                raise SystemExit(f"Not Approved (or not found): {', '.join(sorted(missing))} - nothing written")
            hdrs = [h for h in hdrs if h.EntryKey in keys]
        elif not include_exported:
            hdrs = [h for h in hdrs if h.IIFExportedAt is None]
        entries = []
        for h in hdrs:
            ls = conn.execute(text("""SELECT AccountNumber, Amount, LineMemo, Name, AccountHint FROM fin.ProposedEntryLines
                                      WHERE EntryID = :id ORDER BY LineNum"""), {"id": h.EntryID}).fetchall()
            entries.append((h.EntryID, IIFEntry(h.EntryKey, h.EntryDate, h.QBTxnType, h.Memo, h.Name,
                                                [IIFLine(l.AccountNumber, float(l.Amount), l.LineMemo, l.Name, l.AccountHint)
                                                 for l in ls], h.Status)))
    by_id = {r.ChartAccountID: r for r in chart}

    def full_name(r):
        parts, seen = [r.AccountName], {r.ChartAccountID}
        while r.ParentChartAccountID and r.ParentChartAccountID in by_id and r.ParentChartAccountID not in seen:
            r = by_id[r.ParentChartAccountID]
            seen.add(r.ChartAccountID)
            parts.insert(0, r.AccountName)
        return ":".join(parts)

    accounts = {r.AccountNumber: (full_name(r), r.AccountType or "") for r in chart}
    return entries, accounts


def stamp(engine, entry_ids, path):
    from sqlalchemy import text
    with engine.begin() as conn:
        for eid in entry_ids:
            conn.execute(text("""UPDATE fin.ProposedEntries SET IIFFile = :f, IIFExportedAt = SYSUTCDATETIME(),
                                        UpdatedAt = SYSUTCDATETIME() WHERE EntryID = :id AND Status = 'Approved'"""),
                         {"f": path, "id": eid})


def main():
    ap = argparse.ArgumentParser(description="IIF file of the Approved proposed entries (backup copy only)")
    ap.add_argument("--company", default="dntl")
    ap.add_argument("--entries", help="comma-separated entry keys, e.g. JE-1,JE-5 (must be Approved)")
    ap.add_argument("--include-exported", action="store_true", help="also entries already written to an IIF")
    ap.add_argument("--date-format", choices=sorted(DATE_FORMATS), default="mdy")
    ap.add_argument("--out", help="file to write (default proposed_entries_<company>_<timestamp>.iif)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sql_helper import ensure_schema, get_engine
    eng = get_engine()
    ensure_schema(eng)
    keys = [k.strip() for k in args.entries.split(",")] if args.entries else None
    rows, accounts = load(eng, args.company, keys, args.include_exported)
    if not rows:
        print("Nothing to export: no Approved entries" + ("" if args.include_exported else " that are not yet exported")
              + ". Approve with: python proposed_entries.py approve JE-n --by owner")
        return
    entries = [e for _, e in rows]
    try:
        body, notes = render(entries, accounts, args.date_format)
    except IIFError as ex:
        raise SystemExit("❌ IIF not written:\n  " + str(ex).replace("\n", "\n  "))

    for e in entries:
        card = sum(l.amount for l in e.lines if l.account == "22200")
        debits = sum(l.amount for l in e.lines if l.amount > 0)
        print(f"  {e.key:6} {e.entry_date}  {e.txn_type:18} debits {debits:>11,.2f}   22200 {card:>+11,.2f}")
    for n in notes:
        print(f"  note: {n}")
    if args.dry_run:
        print("\n" + body.replace("\r\n", "\n"))
        return
    path = os.path.abspath(args.out or f"proposed_entries_{args.company}_{datetime.now():%Y%m%d_%H%M}.iif")
    with open(path, "w", encoding="cp1252", errors="replace", newline="") as f:
        f.write(body)
    stamp(eng, [eid for eid, _ in rows], path)
    print(f"✅ {path}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}. Import into a BACKUP copy "
          f"(File > Utilities > Import > IIF Files); entries dated before the closing date need its password. "
          f"Check one entry's date in the register, then: python proposed_entries.py mark-posted <keys>")


if __name__ == "__main__":
    main()
