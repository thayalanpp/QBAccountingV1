"""
load_yearend.py - loads the accountant's (M&Co) year-end files into SQL:
    fin.YearEndAJE   adjusting journal entries (never posted to QuickBooks)
    fin.YearEndTB    closing trial balance: Prelim + Adj + Reclass = Final

    python load_yearend.py --company dntl --fy 2025 --dry-run
    python load_yearend.py --company dntl --fy 2025
    python load_yearend.py --all                     # every company x year whose TB file exists
    python load_yearend.py --company dntl --fy 2025 --tb "<path>" --aje "<path>"

Inputs are CSVs prepared from the accountant's PDFs (this script does not read PDFs):
    C:\\NM\\<FY>yearend\\<co>\\accountant\\YearEndTB_<co>_<FY>.csv    (required)
    C:\\NM\\<FY>yearend\\<co>\\accountant\\YearEndAJE_<co>_<FY>.csv   (optional - a year may have no AJEs)
Signs: TB amounts debit +, credit -. AJE has separate Debit / Credit columns, both positive.

Each run REPLACES that company x year in both tables, in one transaction, and saves nothing if a
hard check fails:
  1. AJE: total debits = total credits, and each AJENumber balances.
  2. TB: Prelim, Adj, Reclass and Final each sum to 0.00.
  3. TB: every row Final = Prelim + Adj + Reclass.
  4. TB Adj per account = that account's AJE net (debit - credit); accounts with no AJE have Adj 0,
     and an AJE account missing from the TB must net to 0.
Soft checks (reported, never block):
  5. PriorYearFinal = the previous year's Final (and the next year's PriorYearFinal = this Final), when loaded.
  6. Prelim vs QuickBooks at the same year end (fin.vw_YearEndTB_vs_QB logic): balance sheet = qb.AccountBalances
     closing; income/expense = sum of qb.JournalEntryLines in the fiscal year. QuickBooks keeps being edited,
     so differences are expected.
  7. Accounts not in the company's QuickBooks chart (M&Co-only accounts such as 5000, 22201, 61500, 90000).

Views: fin.vw_YearEndTB_vs_QB, fin.vw_YearEndCarryForward, fin.vw_YearEndFSLines.
Nothing here writes to QuickBooks; an IIF of the AJEs is on hold until it is decided whether to record them.
"""

import argparse
import os
import sys
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

BASE = r"C:\NM"
COMPANIES = ("dntl", "hyg", "mgmt")
AJE_COLS = ["Company", "FiscalYearEnd", "AJENumber", "EntryDate", "AccountNo", "AccountName",
            "Debit", "Credit", "Description", "IsRollForward", "SourceDoc"]
TB_COLS = ["Company", "FiscalYearEnd", "AccountNo", "AccountName", "FSLine", "Prelim", "Adj", "Reclass",
           "Final", "PriorYearFinal", "Annotation", "SourceDoc"]
TB_AMOUNTS = ["Prelim", "Adj", "Reclass", "Final", "PriorYearFinal"]
TOL = 0.005


def default_path(kind: str, company: str, fy: int, base: str = BASE) -> str:
    return os.path.join(base, f"{fy}yearend", company, "accountant", f"YearEnd{kind}_{company}_{fy}.csv")


def _acct(v) -> str:
    """Account numbers as text, as QuickBooks stores them: 22100, 1010.25 (never '22100.0')."""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def _text(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s or None


def _num(v) -> float:
    return 0.0 if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "" else round(float(v), 2)


def read_files(tb_path: str, aje_path: Optional[str]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    tb = pd.read_csv(tb_path, dtype={"AccountNo": str})
    missing = [c for c in TB_COLS if c not in tb.columns]
    if missing:
        raise SystemExit(f"{tb_path}: missing columns {missing}")
    if aje_path and os.path.exists(aje_path):
        aje = pd.read_csv(aje_path, dtype={"AccountNo": str})
        missing = [c for c in AJE_COLS if c not in aje.columns]
        if missing:
            raise SystemExit(f"{aje_path}: missing columns {missing}")
    else:
        aje = pd.DataFrame(columns=AJE_COLS)
    return normalise(tb, aje)


def normalise(tb: pd.DataFrame, aje: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    tb = tb[TB_COLS].copy()
    tb["AccountNo"] = tb["AccountNo"].map(_acct)
    for c in TB_AMOUNTS:
        tb[c] = tb[c].map(_num)
    for c in ("Company", "AccountName", "FSLine", "Annotation", "SourceDoc"):
        tb[c] = tb[c].map(_text)
    aje = aje[AJE_COLS].copy()
    if len(aje):
        aje["AccountNo"] = aje["AccountNo"].map(_acct)
        aje["Debit"] = aje["Debit"].map(_num)
        aje["Credit"] = aje["Credit"].map(_num)
        aje["AJENumber"] = aje["AJENumber"].astype(int)
        aje["IsRollForward"] = aje["IsRollForward"].map(lambda v: 1 if str(v).strip().lower() in ("1", "true", "yes") else 0)
        aje["EntryDate"] = pd.to_datetime(aje["EntryDate"]).dt.date
        for c in ("Company", "AccountName", "Description", "SourceDoc"):
            aje[c] = aje[c].map(_text)
    return tb, aje


# ---------------------------------------------------------------------
# Hard checks (pure)
# ---------------------------------------------------------------------
def hard_checks(tb: pd.DataFrame, aje: pd.DataFrame, company: str, fy: int) -> List[str]:
    p = []
    for name, df in (("TB", tb), ("AJE", aje)):
        if not len(df):
            continue
        bad_co = sorted(set(df["Company"].dropna()) - {company})
        bad_fy = sorted(set(int(x) for x in df["FiscalYearEnd"].dropna()) - {fy})
        if bad_co or bad_fy:
            p.append(f"{name}: file is for company {bad_co or company} / FY {bad_fy or fy}, not {company} FY{fy}")
    if not len(tb):
        p.append("TB: no rows")
    dup = tb["AccountNo"][tb["AccountNo"].duplicated()].tolist()
    if dup:
        p.append(f"TB: account(s) listed twice: {dup}")

    # 1. AJE balances overall and per entry
    if len(aje):
        if (aje["Debit"] < 0).any() or (aje["Credit"] < 0).any():
            p.append("AJE: negative Debit or Credit (both must be positive)")
        d, c = round(aje["Debit"].sum(), 2), round(aje["Credit"].sum(), 2)
        if abs(d - c) > TOL:
            p.append(f"AJE: debits {d:,.2f} != credits {c:,.2f}")
        per = aje.groupby("AJENumber").apply(lambda g: round(g["Debit"].sum() - g["Credit"].sum(), 2), include_groups=False)
        for n, v in per.items():
            if abs(v) > TOL:
                p.append(f"AJE {n}: does not balance ({v:+,.2f})")

    # 2. TB columns sum to zero
    for col in ("Prelim", "Adj", "Reclass", "Final"):
        s = round(tb[col].sum(), 2)
        if abs(s) > TOL:
            p.append(f"TB: {col} column sums to {s:+,.2f}, not 0.00")

    # 3. Final = Prelim + Adj + Reclass per row
    for _, r in tb.iterrows():
        diff = round(r.Prelim + r.Adj + r.Reclass - r.Final, 2)
        if abs(diff) > TOL:
            p.append(f"TB {r.AccountNo}: Prelim + Adj + Reclass differs from Final by {diff:+,.2f}")

    # 4. TB Adj = AJE net per account
    aje_net = (aje.assign(n=aje["Debit"] - aje["Credit"]).groupby("AccountNo")["n"].sum().round(2)
               if len(aje) else pd.Series(dtype=float))
    tb_adj = tb.set_index("AccountNo")["Adj"]
    for acct in sorted(set(aje_net.index) | set(tb_adj.index)):
        a, t = float(aje_net.get(acct, 0.0)), float(tb_adj.get(acct, 0.0))
        if abs(round(a - t, 2)) > TOL:
            where = "" if acct in tb_adj.index else " (account not in the TB)"
            p.append(f"Account {acct}: TB Adj {t:+,.2f} but AJE net {a:+,.2f}{where}")
    return p


def summary(tb: pd.DataFrame, aje: pd.DataFrame) -> List[str]:
    is_line = tb["FSLine"].map(is_income_statement)
    out = [f"TB {len(tb)} rows; AJE {len(aje)} lines in {aje['AJENumber'].nunique() if len(aje) else 0} entries"
           + (f", {aje['Debit'].sum():,.2f} each side" if len(aje) else "")]
    out.append(f"Net income (Final) {-tb.loc[is_line, 'Final'].sum():,.2f}; prior year {-tb.loc[is_line, 'PriorYearFinal'].sum():,.2f}")
    if len(aje):
        for n, g in aje.groupby("AJENumber"):
            rf = " [roll-forward]" if g["IsRollForward"].max() else ""
            desc = (g["Description"].dropna().iloc[0] if g["Description"].notna().any() else "")[:70]
            out.append(f"  AJE {n:>2} {g['EntryDate'].iloc[0]}  {g['Debit'].sum():>12,.2f}  {desc}{rf}")
    return out


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------
def _fy_bounds(fy: int, start_month: int = 9) -> Tuple[date, date]:
    """FY2025 with a September start -> (2024-09-01, 2025-08-31)."""
    return date(fy - 1, start_month, 1), date(fy, start_month, 1) - timedelta(days=1)


IS_TYPES = ("Income", "Other Income", "Expense", "Other Expense", "Cost of Goods Sold")


def is_income_statement(fsline: Optional[str], qb_type: Optional[str] = None) -> bool:
    """M&Co's income-statement lines start with a number (20 Sales, 40.5 Supplies); balance-sheet lines with
    letters (A Cash, CC Advances...). With no FSLine, fall back on the QuickBooks account type."""
    if fsline and fsline.strip():
        return fsline.strip()[0].isdigit()
    return qb_type in IS_TYPES


def soft_checks(conn, tb: pd.DataFrame, company: str, fy: int) -> List[str]:
    from sqlalchemy import text
    notes = []
    # 5. prior / next year continuity
    prev = dict(conn.execute(text("SELECT AccountNo, Final FROM fin.YearEndTB WHERE Company = :c AND FiscalYearEnd = :y"),
                             {"c": company, "y": fy - 1}).fetchall())
    if prev:
        for _, r in tb.iterrows():
            pf = float(prev.get(r.AccountNo, 0) or 0)
            if abs(round(pf - r.PriorYearFinal, 2)) > TOL:
                notes.append(f"5 {r.AccountNo}: PriorYearFinal {r.PriorYearFinal:,.2f} but FY{fy - 1} Final {pf:,.2f}")
        for acct, v in prev.items():
            if acct not in set(tb["AccountNo"]) and abs(float(v or 0)) > TOL:
                notes.append(f"5 {acct}: FY{fy - 1} Final {float(v):,.2f} but the account is not in this year's TB")
    else:
        notes.append(f"5: FY{fy - 1} not loaded - PriorYearFinal not checked")
    nxt = dict(conn.execute(text("SELECT AccountNo, PriorYearFinal FROM fin.YearEndTB WHERE Company = :c AND FiscalYearEnd = :y"),
                            {"c": company, "y": fy + 1}).fetchall())
    for _, r in tb.iterrows():
        if r.AccountNo in nxt and abs(round(float(nxt[r.AccountNo] or 0) - r.Final, 2)) > TOL:
            notes.append(f"5 {r.AccountNo}: FY{fy + 1} PriorYearFinal {float(nxt[r.AccountNo] or 0):,.2f} but this Final {r.Final:,.2f}")

    # 6 / 7. QuickBooks at the same year end
    co = conn.execute(text("SELECT CompanyID, FiscalYearStartMonth FROM qb.Companies WHERE CompanyCode = :c"),
                      {"c": company}).fetchone()
    if not co:
        notes.append(f"6: company {company} has no QuickBooks data - Prelim not compared")
        return notes
    start, end = _fy_bounds(fy, co.FiscalYearStartMonth or 9)
    chart = {r.AccountNumber: r for r in conn.execute(text(
        "SELECT ChartAccountID, AccountNumber, AccountType FROM qb.ChartOfAccounts WHERE CompanyID = :id"), {"id": co.CompanyID})}
    loaded = conn.execute(text("SELECT COUNT(*) FROM qb.AccountBalances WHERE CompanyID = :id AND FiscalYearEnd = :y"),
                          {"id": co.CompanyID, "y": fy}).scalar()
    not_in_qb = [a for a in tb["AccountNo"] if a not in chart]
    if not_in_qb:
        notes.append(f"7: not in {company}'s QuickBooks chart: {', '.join(not_in_qb)}")
    if not loaded:
        notes.append(f"6: QuickBooks FY{fy} not loaded for {company} - Prelim not compared")
        return notes
    bal = {r.ChartAccountID: float(r.ClosingBalance) for r in conn.execute(text(
        "SELECT ChartAccountID, ClosingBalance FROM qb.AccountBalances WHERE CompanyID = :id AND FiscalYearEnd = :y"),
        {"id": co.CompanyID, "y": fy})}
    act = {r.ChartAccountID: float(r.Amt) for r in conn.execute(text("""
        SELECT l.ChartAccountID, SUM(l.Amount) AS Amt FROM qb.JournalEntryLines l
        JOIN qb.JournalEntries e ON e.JournalEntryID = l.JournalEntryID
        WHERE e.CompanyID = :id AND e.TxnDate BETWEEN :s AND :e GROUP BY l.ChartAccountID"""),
        {"id": co.CompanyID, "s": start, "e": end})}
    n_ok = 0
    for _, r in tb.iterrows():
        a = chart.get(r.AccountNo)
        if not a:
            continue
        is_side = is_income_statement(r.FSLine, a.AccountType)
        qbv = round(act.get(a.ChartAccountID, 0.0) if is_side else bal.get(a.ChartAccountID, 0.0), 2)
        if abs(round(r.Prelim - qbv, 2)) > TOL:
            notes.append(f"6 {r.AccountNo} {r.AccountName or ''}: Prelim {r.Prelim:,.2f} vs QuickBooks {qbv:,.2f} "
                         f"({'FY activity' if is_side else 'Aug 31 balance'}) -> {r.Prelim - qbv:+,.2f}")
        else:
            n_ok += 1
    notes.append(f"6: {n_ok} of {len(tb) - len(not_in_qb)} QuickBooks accounts agree with Prelim to the cent")
    return notes


def save(conn, tb: pd.DataFrame, aje: pd.DataFrame, company: str, fy: int) -> Tuple[int, int, int, int]:
    from sqlalchemy import text
    k = {"c": company, "y": fy}
    d_aje = conn.execute(text("DELETE FROM fin.YearEndAJE WHERE Company = :c AND FiscalYearEnd = :y"), k).rowcount
    d_tb = conn.execute(text("DELETE FROM fin.YearEndTB WHERE Company = :c AND FiscalYearEnd = :y"), k).rowcount
    if len(aje):
        conn.execute(text("""
            INSERT INTO fin.YearEndAJE (Company, FiscalYearEnd, AJENumber, EntryDate, AccountNo, AccountName, Debit, Credit,
                                        Description, IsRollForward, SourceDoc)
            VALUES (:Company, :FiscalYearEnd, :AJENumber, :EntryDate, :AccountNo, :AccountName, :Debit, :Credit,
                    :Description, :IsRollForward, :SourceDoc)"""),
            [dict(r, Company=company, FiscalYearEnd=fy) for r in aje.to_dict("records")])
    conn.execute(text("""
        INSERT INTO fin.YearEndTB (Company, FiscalYearEnd, AccountNo, AccountName, FSLine, Prelim, Adj, Reclass, Final,
                                   PriorYearFinal, Annotation, SourceDoc)
        VALUES (:Company, :FiscalYearEnd, :AccountNo, :AccountName, :FSLine, :Prelim, :Adj, :Reclass, :Final,
                :PriorYearFinal, :Annotation, :SourceDoc)"""),
        [dict(r, Company=company, FiscalYearEnd=fy) for r in tb.to_dict("records")])
    return d_tb, d_aje, len(tb), len(aje)


def run_one(engine, company: str, fy: int, tb_path: str, aje_path: Optional[str], dry_run: bool) -> str:
    print(f"\n=== {company} FY{fy} ===")
    print(f"  TB  {tb_path}")
    print(f"  AJE {aje_path if aje_path and os.path.exists(aje_path) else '(none - no AJEs this year)'}")
    tb, aje = read_files(tb_path, aje_path)
    for line in summary(tb, aje):
        print("  " + line)
    problems = hard_checks(tb, aje, company, fy)
    if problems:
        print("  ❌ NOT saved - hard checks failed:")
        for p in problems:
            print("     " + p)
        return "Failed"
    print("  ✅ hard checks pass (AJEs balance; TB columns sum to 0; Final = Prelim + Adj + Reclass; Adj = AJE net)")
    with engine.begin() as conn:
        for n in soft_checks(conn, tb, company, fy):
            print("  · " + n)
        if dry_run:
            print("  [dry run] nothing written")
            conn.rollback()
            return "DryRun"
        d_tb, d_aje, n_tb, n_aje = save(conn, tb, aje, company, fy)
    print(f"  ✅ saved: TB {n_tb} rows (replaced {d_tb}), AJE {n_aje} lines (replaced {d_aje})")
    return "Loaded"


def main():
    ap = argparse.ArgumentParser(description="Load the accountant's year-end AJEs and trial balance")
    ap.add_argument("--company", choices=COMPANIES)
    ap.add_argument("--fy", type=int, help="fiscal year END, e.g. 2025 = Sep 1 2024 - Aug 31 2025")
    ap.add_argument("--all", action="store_true", help="every company x year whose TB file exists")
    ap.add_argument("--tb", help="TB CSV (default: C:\\NM\\<FY>yearend\\<co>\\accountant\\YearEndTB_<co>_<FY>.csv)")
    ap.add_argument("--aje", help="AJE CSV (default: same folder, YearEndAJE_<co>_<FY>.csv)")
    ap.add_argument("--base", default=BASE, help="root folder holding the <FY>yearend folders")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        jobs = [(co, fy) for fy in range(2018, date.today().year + 2) for co in COMPANIES
                if os.path.exists(default_path("TB", co, fy, args.base))]
        if not jobs:
            raise SystemExit(f"No YearEndTB_<co>_<FY>.csv found under {args.base}\\<FY>yearend\\<co>\\accountant")
    elif args.company and args.fy:
        jobs = [(args.company, args.fy)]
    else:
        ap.error("give --company and --fy, or --all")

    from sql_helper import ensure_schema, get_engine
    eng = get_engine()
    ensure_schema(eng)
    # load in year order so each year's PriorYearFinal check can see the year before
    results = []
    for co, fy in sorted(jobs, key=lambda j: (j[1], j[0])):
        tb = args.tb if (args.tb and not args.all) else default_path("TB", co, fy, args.base)
        aje = args.aje if (args.aje and not args.all) else default_path("AJE", co, fy, args.base)
        if not os.path.exists(tb):
            print(f"\n=== {co} FY{fy} ===\n  ❌ TB file not found: {tb}")
            results.append((co, fy, "NoFile"))
            continue
        results.append((co, fy, run_one(eng, co, fy, tb, aje, args.dry_run)))
    print("\nSummary:")
    for co, fy, st in results:
        print(f"   {co:5} FY{fy}  {st}")
    sys.exit(1 if any(st in ("Failed", "NoFile") for _, _, st in results) else 0)


if __name__ == "__main__":
    main()
