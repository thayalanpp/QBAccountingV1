"""
proposed_entries.py - the clean-up entries proposed for QuickBooks, held in fin.ProposedEntries / fin.ProposedEntryLines.

    python proposed_entries.py seed                       # write JE-1..JE-7 + TM-1/TM-1R (safe to rerun)
    python proposed_entries.py list                       # fin.vw_ProposedEntries
    python proposed_entries.py show JE-2                  # one entry with its lines
    python proposed_entries.py set-account JE-2 --line 2 --account 68100
    python proposed_entries.py approve JE-1 JE-4 --by owner
    python proposed_entries.py reject JE-7 --by owner --note "wait for the vendor"
    python proposed_entries.py mark-posted JE-1 --by owner        # after the IIF import into the backup copy
    python proposed_entries.py proforma [--check]          # fin.vw_ProForma22200 (and compare with the report)

Source of the entries: "Dental Visa (22200) Reconciliation - Issues and Proposed Adjustments to FY2025"
(Oct 3 2026). Amounts are debit + / credit -, in Dental's (dntl) own QuickBooks account numbers.

Rules
- Rerunning `seed` rewrites entries that are still Draft (keeping any account set with `set-account`);
  Approved / Posted / Rejected entries are never changed - a difference is reported instead.
- A leg whose account is not decided has AccountNumber NULL and an AccountHint. `seed` fills it only when
  the hint's name pattern matches exactly one Dental account. Such an entry cannot be approved.
- The database proposes; people post. Nothing here writes to QuickBooks (see iif_export.py).
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

COMPANY = "dntl"
CARD_ACCOUNT = "22200"

# Accounts the entries use that do not exist in QuickBooks yet: number -> (name, IIF account type, description).
NEW_ACCOUNTS = {
    "39900": ("Suspense - pre-FY2024 differences", "EQUITY",
              "Pre-FY2024 22200 difference parked by JE-1 (Aug 31 2023); to clear after FY2021-FY2023 are investigated"),
}


@dataclass
class Line:
    amount: float                      # debit +, credit -
    account: Optional[str] = None      # company QB account number; None = not decided
    hint: Optional[str] = None         # what account is meant
    like: Optional[str] = None         # SQL LIKE pattern on AccountName to auto-fill `account` (unique match only)
    memo: Optional[str] = None
    name: Optional[str] = None


@dataclass
class Entry:
    key: str
    entry_date: date
    title: str
    lines: List[Line]
    memo: str = ""
    explanation: str = ""
    txn_type: str = "General Journal"
    kind: str = "Correction"
    name: Optional[str] = None
    date_basis: Optional[str] = None
    reverses: Optional[str] = None     # key of the entry this reverses
    evidence: Optional[str] = None

    @property
    def seed_key(self) -> str:
        return f"pe-{COMPANY}-{self.key}"

    @property
    def fiscal_year_end(self) -> int:
        return self.entry_date.year + (1 if self.entry_date.month >= 9 else 0)

    def net_cents(self) -> int:
        return sum(_c(l.amount) for l in self.lines)

    def card_effect(self) -> float:
        return round(sum(l.amount for l in self.lines if l.account == CARD_ACCOUNT), 2)


def _c(x) -> int:
    return int(round(float(x) * 100))


PHONE = dict(hint="Telephone / internet expense", like="%phone%")
REPORT = "Reconciliation report (Oct 3 2026), 'Proposed adjustments'"

ENTRIES: List[Entry] = [
    Entry("JE-1", date(2023, 8, 31), "Park the pre-FY2024 22200 difference in suspense",
          memo="JE-1 Pre-FY2024 22200 difference parked in suspense",
          explanation="At Aug 31 2023 QB showed 22200 at 9,502.48 Dr while the card owed 6,270.35: a 15,772.83 difference "
                      "that predates FY2024 and is not investigated yet. Parked in new equity account 39900 so FY2024 and "
                      "FY2025 can be fixed from a clean start. Posting into FY2023 needs the closing-date password.",
          lines=[Line(15772.83, "39900", hint="Suspense - pre-FY2024 differences (new, Equity)"),
                 Line(-15772.83, CARD_ACCOUNT)],
          evidence=REPORT + "; 'Pre-FY2024 difference parked in suspense'"),

    Entry("JE-2", date(2024, 8, 31), "FY2024 Bell and Google charges entered at the wrong amounts",
          memo="JE-2 Correct FY2024 Bell/Google card charges to the statement amounts",
          explanation="Bell 237.64 entered as 327.64 (QB 90.00 too high); Bell 238.92 entered as 238.49 (0.43 too low); "
                      "Google 9.35 entered as 9.23 (0.12 too low). Net Dr 22200 89.45. Better still: correct the original "
                      "charges on their card dates in the backup copy.",
          date_basis="card dates in FY2024 (Aug 31 2024 used as stand-in)",
          lines=[Line(90.00, CARD_ACCOUNT, memo="Bell 237.64 entered as 327.64"),
                 Line(-90.00, memo="Bell 237.64 entered as 327.64", **PHONE),
                 Line(0.43, memo="Bell 238.92 entered as 238.49", **PHONE),
                 Line(-0.43, CARD_ACCOUNT, memo="Bell 238.92 entered as 238.49"),
                 Line(0.12, memo="Google 9.35 entered as 9.23", **PHONE),
                 Line(-0.12, CARD_ACCOUNT, memo="Google 9.35 entered as 9.23")],
          evidence=REPORT + "; Issue 3 FY2024 table"),

    Entry("JE-3", date(2024, 8, 31), "Two FY2024 card charges never entered",
          memo="JE-3 Two FY2024 card charges not entered in QuickBooks",
          explanation="302.45 of FY2024 card charges never entered (FY2024 split review, roll-forward item 8). "
                      "Vendors and expense accounts still to identify; enter as Credit Card Charges on the card dates.",
          date_basis="card dates in FY2024 (Aug 31 2024 used as stand-in)",
          lines=[Line(302.45, hint="Expense of the two FY2024 card charges (to identify; roll-forward item 8)"),
                 Line(-302.45, CARD_ACCOUNT)],
          evidence="split_review_FY2024.xlsx roll-forward item 8"),

    Entry("JE-4", date(2024, 8, 31), "Rounding on FY2024 split payments",
          memo="JE-4 Rounding on FY2024 split payments",
          explanation="2.02 of rounding between the split cheques/hygiene journals and the card payments in FY2024.",
          lines=[Line(2.02, CARD_ACCOUNT), Line(-2.02, "64400", hint="Dental Supplies")],
          evidence=REPORT),

    Entry("TM-1", date(2024, 8, 31), "Timing: 87.30 recorded in FY2024 that the card posted in FY2025",
          kind="Timing", memo="TM-1 Card item recorded in FY2024, posted by TD in FY2025 (reverses Sep 1 2024)",
          explanation="QuickBooks has an 87.30 card item in FY2024 that TD posted in FY2025. Moving it to FY2025 makes "
                      "Aug 31 2024 agree with the card to the cent (3,430.48). Reversed by TM-1R on Sep 1 2024, so it has "
                      "no effect at Aug 31 2025.",
          lines=[Line(87.30, CARD_ACCOUNT),
                 Line(-87.30, hint="Expense account of the 87.30 card item (to identify)")],
          evidence="split_review_FY2024/FY2025.xlsx roll-forward 'Timing' line; reconciliation report 'Proof'"),

    Entry("TM-1R", date(2024, 9, 1), "Reverse TM-1 at the start of FY2025",
          kind="Reversal", reverses="TM-1", memo="TM-1R Reverse TM-1",
          explanation="Reverses TM-1: the 87.30 belongs to FY2025.",
          lines=[Line(87.30, hint="Expense account of the 87.30 card item (to identify)"),
                 Line(-87.30, CARD_ACCOUNT)]),

    Entry("JE-5", date(2025, 8, 31), "Reverse FY2025 split payments booked but never made to TD",
          memo="JE-5 Reverse FY2025 split payments booked on 22200 but not paid to TD",
          explanation="The split cheques' visa portions and the hygiene-supplies journals recorded 17,677.35 of card payments "
                      "TD never received (Issue 1: past balance split again 12,860.97; Issue 2: items that should not have "
                      "been split 4,816.38). Dental's 80% (14,141.87) went to Hygiene in cash, so Hygiene owes it back; "
                      "Hygiene's 20% (3,535.48) had been credited to 64400 through the journals, so that credit is reversed. "
                      "Assumes the 1,936.41 July 2025 credits were Hygiene's payments. Hygiene needs the matching entry.",
          lines=[Line(14141.87, "22100", hint="Due from Hygiene (NM Hygiene interco)",
                      memo="Hygiene owes Dental: 80% of split payments not made (10,288.77 + 3,853.10)"),
                 Line(3535.48, "64400", hint="Dental Supplies",
                      memo="Reverse Hygiene's 20% credited via hygiene supplies journals (2,572.20 + 963.28)"),
                 Line(-17677.35, CARD_ACCOUNT, memo="Split payments booked but not made to TD")],
          evidence=REPORT + "; Issues 1 and 2"),

    Entry("JE-6", date(2025, 2, 3), "Align Technology charge never entered",
          txn_type="Credit Card Charge", name="Align Technology",
          memo="JE-6 Align Technology card charge Feb 3 2025 (never entered)",
          explanation="Align 3,534.52 (Feb 3 2025) was split and paid (100% Dental as a lab fee, cheque 54) but never "
                      "entered, so nothing increased 22200 for the charge itself.",
          lines=[Line(3534.52, hint="Lab fees", like="%lab%", memo="Align Technology"),
                 Line(-3534.52, CARD_ACCOUNT, memo="Align Technology")],
          evidence=REPORT + "; Issue 3 FY2025 table"),

    Entry("JE-7", date(2025, 8, 31), "FY2025 card charges never entered (74.58 Feb, 0.66 Aug)",
          memo="JE-7 FY2025 card charges not entered in QuickBooks",
          explanation="74.58 in the Feb 2025 cycle (Jan 7 - Feb 6) and 0.66 in the Aug 2025 cycle (Jul 8 - Aug 6) were "
                      "never entered. Still to identify; enter as Credit Card Charges on the card dates.",
          date_basis="card dates in FY2025 (Aug 31 2025 used as stand-in)",
          lines=[Line(74.58, hint="Expense of the Feb 2025-cycle charges (to identify)"),
                 Line(0.66, hint="Expense of the Aug 2025-cycle difference (to identify)"),
                 Line(-75.24, CARD_ACCOUNT)],
          evidence=REPORT + "; Issue 3 FY2025 table"),
]

# What the report says 22200 should be after the entries (fin.vw_ProForma22200).
EXPECTED = {
    2023: dict(pro_forma_owed=6270.35, card_owed=6270.35, over_card=0.00),
    2024: dict(pro_forma_owed=3430.48, card_owed=3430.48, over_card=0.00),
    2025: dict(pro_forma_owed=2009.95, card_owed=408.99, over_card=1600.96),
}


# ---------------------------------------------------------------------
# Pure checks (no database)
# ---------------------------------------------------------------------
def check_entries(entries=ENTRIES) -> List[str]:
    """Problems with the entry definitions themselves (empty list = fine)."""
    problems, keys = [], set()
    for e in entries:
        if e.key in keys:
            problems.append(f"{e.key}: duplicate key")
        keys.add(e.key)
        if e.net_cents() != 0:
            problems.append(f"{e.key}: does not balance ({e.net_cents() / 100:+.2f})")
        if any(_c(l.amount) == 0 for l in e.lines):
            problems.append(f"{e.key}: zero line")
        if any(l.account is None and not l.hint for l in e.lines):
            problems.append(f"{e.key}: line with neither account nor hint")
        if e.txn_type == "Credit Card Charge" and sum(1 for l in e.lines if l.account == CARD_ACCOUNT) != 1:
            problems.append(f"{e.key}: a Credit Card Charge needs exactly one {CARD_ACCOUNT} line")
        if e.reverses and e.reverses not in keys:
            problems.append(f"{e.key}: reverses {e.reverses}, which must come first")
    return problems


def pro_forma(qb_closing: dict, card_owed: dict, entries=ENTRIES) -> dict:
    """Python twin of fin.vw_ProForma22200: {fy_end: dict(pro_forma_owed, card_owed, over_card)}."""
    out = {}
    for fy, qb in qb_closing.items():
        as_of = date(fy, 8, 31)
        adj = sum(e.card_effect() for e in entries if e.entry_date <= as_of)
        owed = round(-(qb + adj), 2)
        out[fy] = dict(pro_forma_owed=owed, card_owed=card_owed[fy], over_card=round(owed - card_owed[fy], 2))
    return out


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------
def _company_id(conn, code=COMPANY):
    from sqlalchemy import text
    cid = conn.execute(text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"), {"c": code}).scalar()
    if cid is None:
        raise SystemExit(f"Company '{code}' is not in qb.Companies - load its QuickBooks data first.")
    return cid


def _resolve_like(conn, cid, pattern):
    from sqlalchemy import text
    rows = conn.execute(text("""SELECT AccountNumber FROM qb.ChartOfAccounts
                                WHERE CompanyID = :cid AND AccountName LIKE :p"""), {"cid": cid, "p": pattern}).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _entry_row(conn, cid, key):
    from sqlalchemy import text
    return conn.execute(text("""SELECT EntryID, Status FROM fin.ProposedEntries
                                WHERE CompanyID = :cid AND EntryKey = :k"""), {"cid": cid, "k": key}).fetchone()


def seed(engine):
    from sqlalchemy import text
    problems = check_entries()
    if problems:
        raise SystemExit("Entry definitions are wrong, nothing saved:\n  " + "\n  ".join(problems))
    report = []
    with engine.begin() as conn:
        cid = _company_id(conn)
        msg_id = conn.execute(text("SELECT MessageID FROM agent.Messages WHERE SeedKey = 'dnt-prop-je-set'")).scalar()
        ids = {}
        for e in ENTRIES:
            lines = []
            for i, l in enumerate(e.lines, 1):
                acct = l.account or (_resolve_like(conn, cid, l.like) if l.like else None)
                lines.append(dict(n=i, acct=acct, hint=l.hint, amt=round(l.amount, 2), name=l.name, memo=l.memo))
            hdr = dict(cid=cid, k=e.key, sk=e.seed_key, d=e.entry_date, fy=e.fiscal_year_end, tt=e.txn_type, kind=e.kind,
                       nm=e.name, ti=e.title, me=e.memo, ex=e.explanation, db=e.date_basis,
                       rev=ids.get(e.reverses), msg=msg_id, ev=e.evidence)
            row = _entry_row(conn, cid, e.key)
            if row and row.Status != "Draft":
                same = _lines_match(conn, row.EntryID, lines)
                report.append(f"{e.key}: {row.Status} - left as is" + ("" if same else "  ⚠ differs from the seed definition"))
                ids[e.key] = row.EntryID
                continue
            if row:
                kept = {r.LineNum: r.AccountNumber for r in conn.execute(text(
                    "SELECT LineNum, AccountNumber FROM fin.ProposedEntryLines WHERE EntryID = :id"), {"id": row.EntryID})}
                for ln in lines:      # keep an account someone set by hand on a leg the seed leaves undecided
                    if ln["acct"] is None and kept.get(ln["n"]):
                        ln["acct"] = kept[ln["n"]]
                conn.execute(text("""
                    UPDATE fin.ProposedEntries SET SeedKey = :sk, EntryDate = :d, FiscalYearEnd = :fy, QBTxnType = :tt,
                           Kind = :kind, Name = :nm, Title = :ti, Memo = :me, Explanation = :ex, DateBasis = :db,
                           ReversesEntryID = :rev, MessageID = :msg, Evidence = :ev, UpdatedAt = SYSUTCDATETIME()
                    WHERE EntryID = :id"""), dict(hdr, id=row.EntryID))
                conn.execute(text("DELETE FROM fin.ProposedEntryLines WHERE EntryID = :id"), {"id": row.EntryID})
                eid, verb = row.EntryID, "updated"
            else:
                eid = conn.execute(text("""
                    INSERT INTO fin.ProposedEntries (EntryKey, SeedKey, CompanyID, EntryDate, FiscalYearEnd, QBTxnType, Kind,
                                                     Name, Title, Memo, Explanation, DateBasis, ReversesEntryID, MessageID, Evidence)
                    OUTPUT INSERTED.EntryID
                    VALUES (:k, :sk, :cid, :d, :fy, :tt, :kind, :nm, :ti, :me, :ex, :db, :rev, :msg, :ev)"""), hdr).scalar()
                verb = "new"
            for ln in lines:
                conn.execute(text("""
                    INSERT INTO fin.ProposedEntryLines (EntryID, LineNum, AccountNumber, AccountHint, Amount, Name, LineMemo)
                    VALUES (:eid, :n, :acct, :hint, :amt, :name, :memo)"""), dict(ln, eid=eid))
            ids[e.key] = eid
            open_legs = sum(1 for ln in lines if ln["acct"] is None)
            report.append(f"{e.key}: {verb}" + (f"  ({open_legs} leg(s) need an account)" if open_legs else ""))
    print(f"✅ proposed entries seeded ({COMPANY}):")
    for r in report:
        print("   " + r)


def _lines_match(conn, entry_id, lines) -> bool:
    from sqlalchemy import text
    have = conn.execute(text("SELECT LineNum, Amount FROM fin.ProposedEntryLines WHERE EntryID = :id ORDER BY LineNum"),
                        {"id": entry_id}).fetchall()
    return [(r.LineNum, _c(r.Amount)) for r in have] == [(ln["n"], _c(ln["amt"])) for ln in lines]


def list_entries(engine, company=COMPANY):
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT EntryKey, EntryDate, QBTxnType, Kind, Status, TotalDebit, IsBalanced, UnresolvedLines, Effect22200, Title
            FROM fin.vw_ProposedEntries WHERE CompanyCode = :c ORDER BY EntryDate, EntryKey"""), {"c": company}).fetchall()
    print(f"{'Key':6} {'Date':10} {'Status':9} {'Debits':>11} {'22200':>11}  Ready  Title")
    for r in rows:
        ready = "yes" if r.IsBalanced and not r.UnresolvedLines else f"{r.UnresolvedLines} acct"
        print(f"{r.EntryKey:6} {r.EntryDate!s:10} {r.Status:9} {float(r.TotalDebit):>11,.2f} {float(r.Effect22200):>+11,.2f}"
              f"  {ready:5}  {r.Title}")


def show(engine, key, company=COMPANY):
    from sqlalchemy import text
    with engine.connect() as conn:
        cid = _company_id(conn, company)
        e = conn.execute(text("SELECT * FROM fin.ProposedEntries WHERE CompanyID = :cid AND EntryKey = :k"),
                         {"cid": cid, "k": key}).fetchone()
        if not e:
            raise SystemExit(f"{key}: no such entry")
        print(f"{e.EntryKey}  {e.EntryDate}  {e.QBTxnType}  [{e.Status}]  {e.Title}")
        if e.DateBasis:
            print(f"  date: {e.DateBasis}")
        print(f"  {e.Explanation}")
        for l in conn.execute(text("""SELECT LineNum, AccountNumber, AccountHint, Amount, LineMemo
                                      FROM fin.ProposedEntryLines WHERE EntryID = :id ORDER BY LineNum"""), {"id": e.EntryID}):
            acct = l.AccountNumber or "??? " + (l.AccountHint or "")
            print(f"  {l.LineNum:>2}  {acct:45} {float(l.Amount):>12,.2f}  {l.LineMemo or ''}")


def set_account(engine, key, line_num, account, company=COMPANY):
    from sqlalchemy import text
    with engine.begin() as conn:
        cid = _company_id(conn, company)
        row = _entry_row(conn, cid, key)
        if not row:
            raise SystemExit(f"{key}: no such entry")
        if row.Status != "Draft":
            raise SystemExit(f"{key} is {row.Status}; only Draft entries can be changed")
        exists = conn.execute(text("SELECT 1 FROM qb.ChartOfAccounts WHERE CompanyID = :cid AND AccountNumber = :a"),
                              {"cid": cid, "a": account}).scalar()
        if not exists and account not in NEW_ACCOUNTS:
            raise SystemExit(f"Account {account} is not in {company}'s chart of accounts (qb.ChartOfAccounts)")
        n = conn.execute(text("""UPDATE fin.ProposedEntryLines SET AccountNumber = :a
                                 WHERE EntryID = :id AND LineNum = :n"""), {"a": account, "id": row.EntryID, "n": line_num}).rowcount
        if not n:
            raise SystemExit(f"{key} has no line {line_num}")
        conn.execute(text("UPDATE fin.ProposedEntries SET UpdatedAt = SYSUTCDATETIME() WHERE EntryID = :id"), {"id": row.EntryID})
    print(f"✅ {key} line {line_num} -> {account}")


ALLOWED = {"approve": ({"Draft"}, "Approved"), "reject": ({"Draft", "Approved"}, "Rejected"),
           "mark-posted": ({"Approved"}, "Posted")}


def set_status(engine, action, keys, by, note=None, company=COMPANY):
    from sqlalchemy import text
    allowed_from, new = ALLOWED[action]
    with engine.begin() as conn:
        cid = _company_id(conn, company)
        for key in keys:
            r = conn.execute(text("""SELECT EntryID, Status, IsBalanced, UnresolvedLines FROM fin.vw_ProposedEntries
                                     WHERE CompanyCode = :c AND EntryKey = :k"""), {"c": company, "k": key}).fetchone()
            if not r:
                print(f"❌ {key}: no such entry")
                continue
            if r.Status not in allowed_from:
                print(f"❌ {key}: is {r.Status}; {action} needs {' or '.join(sorted(allowed_from))}")
                continue
            if new == "Approved" and (not r.IsBalanced or r.UnresolvedLines):
                print(f"❌ {key}: not ready - " + ("unbalanced" if not r.IsBalanced else f"{r.UnresolvedLines} leg(s) without an account"))
                continue
            conn.execute(text("""UPDATE fin.ProposedEntries SET Status = :s, StatusBy = :by, StatusAt = SYSUTCDATETIME(),
                                        StatusNote = :note, UpdatedAt = SYSUTCDATETIME() WHERE EntryID = :id"""),
                         {"s": new, "by": by, "note": note, "id": r.EntryID})
            print(f"✅ {key}: {r.Status} -> {new}")


def proforma(engine, check=False) -> bool:
    from sqlalchemy import text
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM fin.vw_ProForma22200 ORDER BY FiscalYearEnd")).fetchall()
    print(f"{'Aug 31':10} {'QB 22200':>12} {'Proposed':>12} {'Pro-forma':>12} {'Card owed':>11} {'Over card':>10} {'(approved)':>11}")
    ok = True
    for r in rows:
        card = None if r.CardOwed is None else float(r.CardOwed)
        print(f"{r.AsOfDate!s:10} {float(r.QBBalance):>+12,.2f} {float(r.AdjAll):>+12,.2f} {float(r.ProFormaBalance):>+12,.2f}"
              f" {card if card is not None else float('nan'):>11,.2f} {float(r.ProFormaOverCard or 0):>10,.2f}"
              f" {float(r.ApprovedOverCard or 0):>11,.2f}")
        exp = EXPECTED.get(r.FiscalYearEnd)
        if check and exp:
            got = dict(pro_forma_owed=float(r.ProFormaOwed), card_owed=card, over_card=float(r.ProFormaOverCard or 0))
            bad = [k for k, v in exp.items() if got[k] is None or _c(got[k]) != _c(v)]
            if bad:
                ok = False
                print("   ❌ expected " + ", ".join(f"{k} {exp[k]:,.2f} (got {got[k]})" for k in bad))
            else:
                print("   ✅ as in the report")
    if check:
        missing = sorted(set(EXPECTED) - {r.FiscalYearEnd for r in rows})
        if missing:
            ok = False
            print(f"   ❌ no row for FY {missing} - run load_gl_balances.py and the visa pipeline")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Proposed QuickBooks clean-up entries (fin.ProposedEntries)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    sub.add_parser("list")
    sub.add_parser("check", help="check the entry definitions and the report figures without a database")
    p = sub.add_parser("show"); p.add_argument("key")
    p = sub.add_parser("set-account"); p.add_argument("key"); p.add_argument("--line", type=int, required=True)
    p.add_argument("--account", required=True)
    for a in ALLOWED:
        p = sub.add_parser(a); p.add_argument("keys", nargs="+"); p.add_argument("--by", default="owner")
        p.add_argument("--note")
    p = sub.add_parser("proforma"); p.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.cmd == "check":
        problems = check_entries()
        pf = pro_forma({2023: 9502.48, 2024: 12466.03, 2025: 35260.97}, {2023: 6270.35, 2024: 3430.48, 2025: 408.99})
        for fy, v in pf.items():
            print(f"Aug 31 {fy}: books {v['pro_forma_owed']:,.2f} owed vs card {v['card_owed']:,.2f} -> {v['over_card']:,.2f}")
            if any(_c(v[k]) != _c(EXPECTED[fy][k]) for k in v):
                problems.append(f"FY{fy} pro-forma differs from the report")
        print("\n".join(["❌ " + x for x in problems]) or "✅ entries balance and land on the report's figures")
        sys.exit(1 if problems else 0)

    from sql_helper import ensure_schema, get_engine
    eng = get_engine()
    ensure_schema(eng)
    if args.cmd == "seed":
        seed(eng)
    elif args.cmd == "list":
        list_entries(eng)
    elif args.cmd == "show":
        show(eng, args.key)
    elif args.cmd == "set-account":
        set_account(eng, args.key, args.line, args.account)
    elif args.cmd in ALLOWED:
        set_status(eng, args.cmd, args.keys, args.by, args.note)
    elif args.cmd == "proforma":
        sys.exit(0 if proforma(eng, args.check) or not args.check else 1)


if __name__ == "__main__":
    main()
