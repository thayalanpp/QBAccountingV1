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

    python load_gl_balances.py --company dntl --fy 2026 --dry-run
    python load_gl_balances.py --company dntl --fy 2026
    python load_gl_balances.py --company hyg  --fy 2026

The GL file is found from the company and year (see GL_FILE below):
    C:\\NM\\quickbooks\\dntl\\qb dntl gl 2026.xlsx
    C:\\NM\\quickbooks\\hyg\\qb hyg gl 2026.xlsx
--gl "<path>" still overrides it for a one-off file.

One-year GL files also work (QuickBooks can drop rows in multi-year exports):
run once per year file; each run saves that year's opening and closing.
Checkpoints are checked only for the Aug 31 dates the file covers.
A balance-sheet account with no rows in the file (no activity) takes the balance
QuickBooks shows on its header line. Only an account missing from the file
altogether keeps the previous year's closing from an earlier GL load.

"""

import argparse
import os
import sys
from datetime import date

from sqlalchemy import text

import pandas as pd

from qb_loader import (
    get_engine, parse_transaction_detail, validate_section, parse_account_list,
    fiscal_period, _account_number_from_label, _to_date, _find_header_row, _cell,
)

# The fiscal year to load (named by the year it ends) - same as FISCAL_YEAR_END in run_qb_load.py.
FISCAL_YEAR_END = 2026

# One GL export per company per year: C:\NM\quickbooks\<company>\qb <company> gl <year>.xlsx
GL_FILE = r"C:\NM\quickbooks\{company}\qb {company} gl {year}.xlsx"

# Known balances, debit + / credit - as the export shows them (board: "Dental's key accounts", multi-year GL).
CHECKPOINTS = {
    "dntl": {
        "10000": {2023: 13263.95, 2024: 65468.68, 2025: 13103.87},
        # 2023-2025: after M&Co's year-end entries, as posted by the bookkeeper (Oct 2026) -
        # equal to M&Co's final trial balances (FY2023 = PriorYearFinal on the FY2024 TB).
        "22200": {2023: 5556.90, 2024: 8520.45, 2025: -408.99},
        "22100": {2023: -19376.26, 2024: -19376.26, 2025: 12348.12},
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


def header_balances(gl_path):
    """Balance shown on each account's header line in the GL - QuickBooks puts the
    balance brought forward there. For an account with no rows in the file (no
    activity), that is its balance for the whole period."""
    df = pd.read_excel(gl_path, sheet_name="Sheet1", header=None)
    header_row, col_map = _find_header_row(df)
    type_col = col_map["Type"]
    out = {}
    for i in range(header_row + 1, len(df)):
        row = df.iloc[i]
        if _cell(row, col_map, "Type") is not None:
            continue
        label = None
        for c in range(0, type_col):
            v = row[c] if c in row.index else None
            if pd.notna(v):
                label = str(v).strip()
        if not label or label.startswith("Total "):
            continue
        bal = _cell(row, col_map, "Balance")
        if bal is not None and not pd.isna(bal):
            out[_account_number_from_label(label)] = round(float(bal), 2)
    return out


def account_types_from_db(engine, company_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT AccountNumber, AccountType, ChartAccountID FROM qb.ChartOfAccounts WHERE CompanyID = :c"),
            {"c": company_id},
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def account_types_from_file(path):
    return {r.account_number: (r.account_type, None) for r in parse_account_list(path)}


def build_balances(gl_path, types, first_fy=None, last_fy=None, start_month=9):
    """Returns (balances {acct: {fy: debit+ closing}}, problems [str], meta)."""
    sections = parse_transaction_detail(gl_path)
    headers = header_balances(gl_path)
    problems, balances, skipped_unknown, from_header = [], {}, [], []

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
        num = _account_number_from_label(s.account_label)
        acct_type = (types.get(num) or ("Unknown", None))[0]
        t = acct_type.strip().lower()
        if t not in BALANCE_SHEET and t != "unknown":
            continue    # income / expense / non-posting: not taken from the multi-year GL
        if not s.rows:
            # No activity in the file: the header line's brought-forward balance holds all period.
            if num in headers and t != "unknown":
                balances[num] = {fy: headers[num] + 0.0 for fy in fys}
                from_header.append(num)
            continue
        if t == "unknown":
            skipped_unknown.append(num)
            continue
        ds = [d for r in s.rows if (d := _to_date(r.txn_date))]
        if any(b < a for a, b in zip(ds, ds[1:])):
            problems.append(f"{s.account_label}: rows not in date order - balance at a date is ambiguous")
            continue
        v = validate_section(s)
        if not v.is_valid:
            problems.append(f"{s.account_label}: running balance breaks at sheet row {v.first_mismatch_row + 1} "
                            f"(computed {v.computed_ending:,.2f} vs shown {v.stated_ending:,.2f})")
            continue
        if num in headers and abs(headers[num] - v.opening_balance) > 0.005:
            problems.append(f"{s.account_label}: header balance {headers[num]:,.2f} vs opening re-derived from rows "
                            f"{v.opening_balance:,.2f}")
            continue
        balances[num] = {fy: round(balance_at(s, v.opening_balance, fiscal_period(fy, start_month).end), 2) + 0.0
                         for fy in fys}

    return balances, problems, {"first_date": first_date, "last_date": last_date, "fys": fys,
                                "unknown": skipped_unknown, "from_header": from_header}


def check_points(company, balances, fys):
    """Known balances must agree - only for the Aug 31 dates this GL file covers
    (a one-year GL covers its opening and closing Aug 31 only)."""
    problems = []
    for num, by_fy in CHECKPOINTS.get(company, {}).items():
        for fy, expected in by_fy.items():
            if fy not in fys:
                continue
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


def carry_forward_dormant(engine, company_id, balances, types, fys):
    """One-year GL: a balance-sheet account with no rows in the file had no activity that
    year, so its balance is unchanged - take the opening from the previous year's closing
    saved by an earlier GL load (SourceFile is a GL file). Run yearly files oldest first."""
    if len(fys) != 2:
        return []
    prev_fy = fys[0]
    with engine.connect() as conn:
        rows = conn.execute(text("""SELECT ChartAccountID, ClosingBalance FROM qb.AccountBalances
                                    WHERE CompanyID = :c AND FiscalYearEnd = :fy
                                      AND LOWER(SourceFile) LIKE '%gl%'"""),
                            {"c": company_id, "fy": prev_fy}).fetchall()
    prev_close = {r[0]: round(float(r[1]), 2) for r in rows}
    carried = []
    for num, (acct_type, aid) in types.items():
        if num in balances or aid not in prev_close:
            continue
        if (acct_type or "").strip().lower() not in BALANCE_SHEET:
            continue
        balances[num] = {fy: prev_close[aid] for fy in fys}
        carried.append(num)
    return carried


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
    ap.add_argument("--fy", type=int, default=FISCAL_YEAR_END,
                    help=f"fiscal year of the GL file, named by the year it ends (default {FISCAL_YEAR_END})")
    ap.add_argument("--gl", help="GL export to use instead of the default file for --company and --fy "
                                 "(a multi-year file: also pass --first-fy/--last-fy, or leave both out to take every year)")
    ap.add_argument("--account-list", help="Account Listing export - account types without the DB (dry run)")
    ap.add_argument("--first-fy", type=int)
    ap.add_argument("--last-fy", type=int)
    ap.add_argument("--dry-run", action="store_true", help="parse, check and print; no writes")
    args = ap.parse_args()

    if args.gl:
        gl = args.gl
    else:
        gl = GL_FILE.format(company=args.company, year=args.fy)
        # The default file holds one fiscal year - take exactly that year, even if
        # the last row is dated before Aug 31.
        args.first_fy = args.first_fy or args.fy
        args.last_fy = args.last_fy or args.fy
    if not os.path.exists(gl):
        sys.exit(f"GL file not found: {gl}")

    engine = company_id = None
    start_month = 9
    if args.account_list:
        types = account_types_from_file(args.account_list)
    else:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("SELECT CompanyID, FiscalYearStartMonth FROM qb.Companies WHERE CompanyCode = :c"),
                               {"c": args.company}).fetchone()
        if not row:
            sys.exit(f"Company {args.company} not in qb.Companies - run run_qb_load.py first")
        company_id = row[0]
        start_month = int(row[1] or 9)
        types = account_types_from_db(engine, company_id)

    balances, problems, meta = build_balances(gl, types, args.first_fy, args.last_fy, start_month)
    fys = meta["fys"]
    carried = carry_forward_dormant(engine, company_id, balances, types, fys) if engine is not None else []
    problems += check_points(args.company, balances, fys)

    print(f"GL {gl}\n  rows dated {meta['first_date']} .. {meta['last_date']}; "
          f"FY{fys[1]}..FY{fys[-1]} (debit +, credit -)")
    print(f"  {'Account':<10}" + "".join(f"{'Aug 31 ' + str(y):>16}" for y in fys))
    for num in sorted(balances):
        print(f"  {num:<10}" + "".join(f"{balances[num][y]:>16,.2f}" for y in fys))
    if meta["from_header"]:
        print(f"  no activity in the file - balance from the GL header line: {', '.join(sorted(meta['from_header']))}")
    if carried:
        print(f"  not in the file - balance carried from FY{fys[0]}'s GL load: {', '.join(sorted(carried))}")
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
