"""
load_gl_balances.py

Takes balance-sheet opening/closing balances from the MULTI-YEAR QuickBooks
General Ledger and stores them in qb.AccountBalances.

Why: the one-year Transaction Detail export starts every balance-sheet
account's running balance at 0 (no balance brought forward), so the
opening that run_qb_load.py writes to qb.AccountBalances is wrong for
those accounts. The multi-year GL carries the true running balance.
(Its income/expense rows are incomplete - QuickBooks keeps them only for
the last year - so only balance-sheet accounts are taken from it.)

How:
  1. Parse the GL with qb_loader.parse_transaction_detail (same grouped
     layout as the Transaction Detail report).
  2. Every account section must re-derive its own running balance
     (qb_loader.validate_section) - otherwise nothing is saved.
  3. Balance at a date = the Balance column of the last row dated on or
     before it (or the section's brought-forward balance if none).
  4. Stored with the export's own sign (debit +, credit -), the same as
     run_qb_load.py writes and rollforward.py reads (QB_SIGN turns 22200
     into "owed"). Account types are used only to pick balance-sheet accounts.
  5. Checks - otherwise nothing is saved:
       - known balances (board: "Dental's key accounts") agree to the cent;
       - for every year run_qb_load.py already loaded, the GL's movement
         (closing - opening) equals the one-year export's movement.
  6. One transaction: upsert one row per (account, fiscal year) into
     qb.AccountBalances, SourceFile = the GL.

Run order: AFTER run_qb_load.py for the company (a QB reload deletes and
rewrites that year's qb.AccountBalances with a 0 opening), BEFORE
split_pipeline.py.

    python load_gl_balances.py --company dntl --dry-run
    python load_gl_balances.py --company dntl
"""

import argparse
import sys
from datetime import date

from sqlalchemy import text

from qb_loader import (
    get_engine, parse_transaction_detail, validate_section, parse_account_list,
    fiscal_period, _account_number_from_label, _to_date,
)

GL_FILES = {
    "dntl": r"C:\NM\quickbooks\dntl\qb sv gl 2020-2026.xlsx",
}

# Known balances, debit + / credit - as the export shows them (board: "Dental's key accounts", multi-year GL).
CHECKPOINTS = {
    "dntl": {
        "10000": {2023: 13263.95, 2024: 65468.68, 2025: 13103.87},
        "22200": {2023: 9502.48, 2024: 12466.03, 2025: 35260.97},
        "22100": {2023: -7646.88, 2024: -7646.88, 2025: -7646.88},
        "22150": {2023: 12500.00, 2024: 12500.00, 2025: 12500.00},
    },
}

BALANCE_SHEET = {"bank", "accounts receivable", "other current asset", "fixed asset", "other asset",
                 "accounts payable", "credit card", "other current liability", "long term liability",
                 "equity"}



def balance_at(section, opening, as_of: date) -> float:
    bal = opening
    for r in section.rows:
        d = _to_date(r.txn_date)
        if d is None:
            continue
        if d > as_of:
            break
        bal = r.balance
    return round(bal, 2)


def account_types_from_db(engine, company_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT AccountNumber, AccountType, ChartAccountID FROM qb.ChartOfAccounts WHERE CompanyID = :c"),
            {"c": company_id},
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def account_types_from_file(path):
    return {r.account_number: (r.account_type, None) for r in parse_account_list(path)}


def build_balances(gl_path, types, first_fy=None, last_fy=None):
    """Returns (balances {acct: {fy: debit+ closing}}, problems [str], meta)."""
    sections = parse_transaction_detail(gl_path)
    problems, balances, skipped_unknown = [], {}, []

    dates = [d for s in sections for r in s.rows if (d := _to_date(r.txn_date))]
    if not dates:
        raise ValueError("No dated rows found in the GL")
    first_date, last_date = min(dates), max(dates)
    # Every FY end covered by the file: the first FY whose end is on/after the
    # first row, through the last FY that ended on/before the last row.
    fy_lo = first_fy or (first_date.year if first_date <= date(first_date.year, 8, 31) else first_date.year + 1)
    fy_hi = last_fy or (last_date.year if last_date >= date(last_date.year, 8, 31) else last_date.year - 1)
    fys = list(range(fy_lo - 1, fy_hi + 1))   # fy_lo-1 = opening of the first year

    for s in sections:
        if not s.rows:
            continue
        num = _account_number_from_label(s.account_label)
        acct_type = (types.get(num) or ("Unknown", None))[0]
        t = acct_type.strip().lower()
        if t == "unknown":
            skipped_unknown.append(num)
            continue
        if t not in BALANCE_SHEET:
            continue    # income / expense / non-posting: not taken from the multi-year GL
        ds = [d for r in s.rows if (d := _to_date(r.txn_date))]
        if any(b < a for a, b in zip(ds, ds[1:])):
            problems.append(f"{s.account_label}: rows not in date order - balance at a date is ambiguous")
            continue
        v = validate_section(s)
        if not v.is_valid:
            problems.append(f"{s.account_label}: running balance breaks at sheet row {v.first_mismatch_row + 1} "
                            f"(computed {v.computed_ending:,.2f} vs shown {v.stated_ending:,.2f})")
            continue
        balances[num] = {fy: round(balance_at(s, v.opening_balance, fiscal_period(fy).end), 2) + 0.0 for fy in fys}

    return balances, problems, {"first_date": first_date, "last_date": last_date, "fys": fys,
                                "unknown": skipped_unknown}


def check_points(company, balances):
    problems = []
    for num, by_fy in CHECKPOINTS.get(company, {}).items():
        for fy, expected in by_fy.items():
            got = balances.get(num, {}).get(fy)
            if got is None:
                problems.append(f"checkpoint {num} Aug 31 {fy}: no balance computed (expected {expected:,.2f})")
            elif abs(got - expected) > 0.005:
                problems.append(f"checkpoint {num} Aug 31 {fy}: {got:,.2f} vs expected {expected:,.2f}")
    return problems


def check_movements(engine, company_id, balances, types, fys):
    """GL movement for a year must equal the one-year export's movement already in qb.AccountBalances."""
    with engine.connect() as conn:
        rows = conn.execute(text("""SELECT ChartAccountID, FiscalYearEnd, OpeningBalance, ClosingBalance
                                    FROM qb.AccountBalances WHERE CompanyID = :c"""), {"c": company_id}).fetchall()
    loaded = {(r[0], int(r[1])): float(r[3]) - float(r[2]) for r in rows}
    problems, compared = [], 0
    for num, by_fy in balances.items():
        aid = types[num][1]
        for fy in fys[1:]:
            if (aid, fy) not in loaded:
                continue
            compared += 1
            gl_move = round(by_fy[fy] - by_fy[fy - 1], 2)
            if abs(gl_move - loaded[(aid, fy)]) > 0.005:
                problems.append(f"{num} FY{fy}: GL movement {gl_move:,.2f} vs one-year export "
                                f"{loaded[(aid, fy)]:,.2f} - re-export / reload that year first")
    return problems, compared


def save(engine, company_id, balances, types, fys, gl_path):
    sql = text("""
        MERGE qb.AccountBalances AS t
        USING (SELECT :cid AS cid, :aid AS aid, :fy AS fy) AS s
           ON t.CompanyID = s.cid AND t.ChartAccountID = s.aid AND t.FiscalYearEnd = s.fy
        WHEN MATCHED THEN UPDATE SET OpeningBalance = :op, ClosingBalance = :cl,
                                     SourceFile = :src, LoadedAt = SYSUTCDATETIME()
        WHEN NOT MATCHED THEN INSERT (CompanyID, ChartAccountID, FiscalYearEnd, OpeningBalance, ClosingBalance, SourceFile)
             VALUES (:cid, :aid, :fy, :op, :cl, :src);""")
    params = [{"cid": company_id, "aid": types[num][1], "fy": fy, "op": by_fy[fy - 1], "cl": by_fy[fy],
               "src": gl_path} for num, by_fy in balances.items() for fy in fys[1:]]
    with engine.begin() as conn:
        for p in params:
            conn.execute(sql, p)
    return len(params)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--company", default="dntl")
    ap.add_argument("--gl", help="multi-year GL export (default: GL_FILES[company])")
    ap.add_argument("--account-list", help="Account Listing export - account types without the DB (dry run)")
    ap.add_argument("--first-fy", type=int)
    ap.add_argument("--last-fy", type=int)
    ap.add_argument("--dry-run", action="store_true", help="parse, check and print; no writes")
    args = ap.parse_args()

    gl = args.gl or GL_FILES.get(args.company)
    if not gl:
        sys.exit(f"No GL file for {args.company} - pass --gl")

    engine = company_id = None
    if args.account_list:
        types = account_types_from_file(args.account_list)
    else:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"),
                               {"c": args.company}).fetchone()
        if not row:
            sys.exit(f"Company {args.company} not in qb.Companies - run run_qb_load.py first")
        company_id = row[0]
        types = account_types_from_db(engine, company_id)

    balances, problems, meta = build_balances(gl, types, args.first_fy, args.last_fy)
    problems += check_points(args.company, balances)
    fys = meta["fys"]

    print(f"GL {gl}\n  rows dated {meta['first_date']} .. {meta['last_date']}; "
          f"FY{fys[1]}..FY{fys[-1]} (debit +, credit -)")
    print(f"  {'Account':<10}" + "".join(f"{'Aug 31 ' + str(y):>16}" for y in fys))
    for num in sorted(balances):
        print(f"  {num:<10}" + "".join(f"{balances[num][y]:>16,.2f}" for y in fys))
    if meta["unknown"]:
        print(f"  skipped (account type 'Unknown' - not in the Account Listing): {', '.join(meta['unknown'])}")

    if engine is not None:
        moves, compared = check_movements(engine, company_id, balances, types, fys)
        problems += moves
        print(f"  movement check: {compared} account-years compared with the one-year loads")

    if problems:
        print("\nNOT SAVED:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("\nAll sections re-derive; all checkpoints agree.")

    if args.dry_run or engine is None:
        print("Dry run - nothing written.")
        return
    n = save(engine, company_id, balances, types, fys, gl)
    print(f"Saved {n} rows to qb.AccountBalances ({len(balances)} accounts x {len(fys) - 1} years).")


if __name__ == "__main__":
    main()
