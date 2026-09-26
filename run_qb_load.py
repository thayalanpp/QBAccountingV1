"""
run_qb_load.py

Command-line entry point for qb_loader.py - loads QuickBooks exports
into SQL Server, one company and one fiscal year at a time.

Every run REPLACES that company's fiscal year in SQL (delete + reload
in a single transaction), so rerun it as often as the bookkeeper sends
updated exports - you always end up with exactly one copy of the year.

Fiscal years are named by the year they END in. With the September
start stored in qb.Companies, --fy-end 2025 means 2024-09-01 .. 2025-08-31.

Usage (from a terminal, with .env filled in):

    # Reload every company listed in COMPANIES below for FISCAL_YEAR_END
    python run_qb_load.py

    # Just one company from COMPANIES
    python run_qb_load.py --only dntl

    # Check first: parse, validate, show what would be deleted - no writes
    python run_qb_load.py --dry-run

    # One-off files via flags
    python run_qb_load.py --company dntl --fy-end 2025 \\
        --account-list "C:\\NM\\...\\account list.xlsx" \\
        --transaction-detail "C:\\NM\\...\\transaction detail.xlsx"

By default a reload is refused (nothing changes) if any account section
in the export fails its running-balance check. --allow-partial reloads
anyway, leaving the failed accounts with no rows for that year.

File paths are built from the year: C:\\NM\\2025yearend\\quickbooks\\... for
FY2025, C:\\NM\\2026yearend\\... for FY2026 (see YEAR_END_ROOT below).

In PyCharm: set FISCAL_YEAR_END below and click Run, or
put the flags under Run/Debug Configuration -> Parameters.
"""

import argparse
import os
import sys

from qb_loader import get_engine, load_company
from sql_helper import ensure_schema

# The fiscal year the bookkeeper is currently working on.
FISCAL_YEAR_END = 2025

# Each year's exports live in their own folder: C:\NM\<year>yearend\quickbooks\...
# {year} below is filled in from FISCAL_YEAR_END (or --fy-end), so changing
# the year also switches the folder and file names - they can't get out of sync.
YEAR_END_ROOT = r"C:\NM\{year}yearend\quickbooks"

# Leave paths as "" for a company you haven't gotten to yet - it's skipped.
COMPANIES = [
    {
        "company": "dntl",
        "legal_name": "S. Viswanathan Dentistry Professional Corporation",
        "account_list": YEAR_END_ROOT + r"\dntl\qb sv all transaction {year} - account list.xlsx",
        "transaction_detail": YEAR_END_ROOT + r"\dntl\qb sv all transaction {year} - transaction detail.xlsx",
    },
    {
        "company": "hyg",
        "legal_name": "DHM Dental Hygiene Technical Services Corporation",
        "account_list": YEAR_END_ROOT + r"\hyg\qb hyg all transaction {year} - account list.xlsx",
        "transaction_detail": YEAR_END_ROOT + r"\hyg\qb hyg all transaction {year} - transaction detail.xlsx",
    },
    {
        "company": "mgmt",
        "legal_name": "",
        "account_list": YEAR_END_ROOT + r"\mgmt\qb mgmt all transaction {year} - account list.xlsx",
        "transaction_detail": YEAR_END_ROOT + r"\mgmt\qb mgmt all transaction {year} - transaction detail.xlsx",
    },
]


def _paths_for_year(job, year):
    """Fill {year} into a COMPANIES entry's file paths."""
    job = dict(job)
    for key in ("account_list", "transaction_detail"):
        if job.get(key):
            job[key] = job[key].format(year=year)
    return job


def main():
    parser = argparse.ArgumentParser(
        description="Replace one fiscal year of QuickBooks data in SQL Server with the latest exports.")
    parser.add_argument("--fy-end", type=int, default=FISCAL_YEAR_END,
                        help=f"Fiscal year, named by the year it ends (default {FISCAL_YEAR_END})")
    parser.add_argument("--only", help="Run just this company code from COMPANIES (e.g. dntl)")
    parser.add_argument("--company", help="Company code for a one-off load via the file flags below")
    parser.add_argument("--account-list", help="Path to the 'Account Listing' export (.xlsx)")
    parser.add_argument("--transaction-detail", help="Path to the 'Transaction Detail by Account' export (.xlsx)")
    parser.add_argument("--legal-name", default=None, help="Optional full legal name for --company")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Reload even if some account sections fail the balance check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and validate only; report what would be deleted/loaded")
    args = parser.parse_args()

    if args.company:
        if not (args.account_list and args.transaction_detail):
            parser.error("--company needs --account-list and --transaction-detail")
        jobs = [{
            "company": args.company,
            "account_list": args.account_list,
            "transaction_detail": args.transaction_detail,
            "legal_name": args.legal_name,
        }]
    else:
        jobs = [_paths_for_year(j, args.fy_end) for j in COMPANIES]
        if args.only:
            jobs = [j for j in jobs if j["company"] == args.only]
            if not jobs:
                parser.error(f"--only {args.only}: not in COMPANIES")

    engine = get_engine()

    # Creates fin.* and qb.* objects that don't exist yet (every statement
    # in sql/schema.sql is guarded, so this is safe on every run).
    print("Ensuring database schema is up to date...")
    ensure_schema(engine)

    results = []
    for job in jobs:
        if not job.get("account_list") or not job.get("transaction_detail"):
            print(f"⏭️  {job['company']}: no file paths set yet, skipping")
            continue
        print(f"\n=== {job['company']} - FY{args.fy_end}{' (dry run)' if args.dry_run else ''} ===")
        missing = [p for p in (job["account_list"], job["transaction_detail"]) if not os.path.exists(p)]
        if missing:
            for p in missing:
                print(f"❌ file not found: {p}")
            results.append((job["company"], "Error"))
            continue
        try:
            _, report = load_company(
                engine,
                company_code=job["company"],
                account_list_path=job["account_list"],
                transaction_detail_path=job["transaction_detail"],
                fy_end_year=args.fy_end,
                legal_name=job.get("legal_name") or None,
                allow_partial=args.allow_partial,
                dry_run=args.dry_run,
            )
            results.append((job["company"], report["status"]))
        except Exception as e:
            # The reload transaction rolled back - the previous load is intact.
            print(f"❌ {job['company']}: failed, previous data left unchanged: {e}")
            results.append((job["company"], "Error"))

    if not results:
        print("Nothing to load - fill in COMPANIES at the top of this file, or pass --company and the file flags.")
        sys.exit(1)

    print("\nSummary:")
    for company, status in results:
        print(f"   {company:6} {status}")
    if any(s in ("Aborted", "Error") for _, s in results):
        sys.exit(2)


if __name__ == "__main__":
    main()
