"""
run_qb_load.py

Command-line entry point for qb_loader.py - this is the file to run
(from PyCharm, VS Code, or a plain terminal) to load one company's
QuickBooks exports into SQL Server.

Usage (from a terminal, with your .env filled in - see .env.example):

    python run_qb_load.py --company dntl \
        --account-list "C:\\NM\\2026yearend\\quickbooks\\dntl\\qb sv all transaction 2025 - account list.xlsx" \
        --transaction-detail "C:\\NM\\2026yearend\\quickbooks\\dntl\\qb sv all transaction 2025 - transaction detail.xlsx" \
        --legal-name "S. Viswanathan Dentistry Professional Corporation"

In PyCharm: open this file, edit the "Run/Debug Configuration" for it to
pass the same arguments under "Parameters", and click Run - or just
edit the COMPANIES list below and run this file with no arguments to
load all of them in one go.
"""

import argparse
import sys

from qb_loader import get_engine, load_company
from sql_helper import ensure_schema

# Edit this if you'd rather just hit "Run" with no arguments than type
# out the --company/--account-list/--transaction-detail flags each time.
# Leave paths as "" for a company you haven't gotten to yet - it's
# skipped automatically.
COMPANIES = [
    {
        "company": "dntl",
        "legal_name": "S. Viswanathan Dentistry Professional Corporation",
        "account_list": r"C:\NM\2026yearend\quickbooks\dntl\qb sv all transaction 2025 - account list.xlsx",
        "transaction_detail": r"C:\NM\2026yearend\quickbooks\dntl\qb sv all transaction 2025 - transaction detail.xlsx",
    },
    {
        "company": "hyg",
        "legal_name": "DHM Dental Hygiene Technical Services Corporation",
        "account_list": r"C:\NM\2026yearend\quickbooks\hyg\qb hyg all transaction 2025 - account list.xlsx",
        "transaction_detail": r"C:\NM\2026yearend\quickbooks\hyg\qb hyg all transaction 2025 - transaction detail.xlsx",
    },
    {
        "company": "mgmt",
        "legal_name": "",
        "account_list": r"C:\NM\2026yearend\quickbooks\mgmt\qb mgmt all transaction 2025 - account list.xlsx",
        "transaction_detail": r"C:\NM\2026yearend\quickbooks\mgmt\qb mgmt all transaction 2025 - transaction detail.xlsx",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Load one company's QuickBooks exports into SQL Server.")
    parser.add_argument("--company", help="Company code, e.g. dntl / hyg / mgmt")
    parser.add_argument("--account-list", help="Path to the 'Account Listing' export (.xlsx)")
    parser.add_argument("--transaction-detail", help="Path to the 'Transaction Detail by Account' export (.xlsx)")
    parser.add_argument("--legal-name", default=None, help="Optional full legal name for this company")
    args = parser.parse_args()

    engine = get_engine()

    # Creates fin.* and qb.* (schemas, tables, views) if they don't exist yet.
    # Every statement in sql/schema.sql is guarded (IF OBJECT_ID(...) IS NULL),
    # so this is safe to run on every startup, not just the first one.
    print("Ensuring database schema is up to date...")
    ensure_schema(engine)

    if args.company:
        # Single-company mode via CLI flags.
        jobs = [{
            "company": args.company,
            "account_list": args.account_list,
            "transaction_detail": args.transaction_detail,
            "legal_name": args.legal_name,
        }]
    else:
        # No flags given - run everything listed in COMPANIES above.
        jobs = COMPANIES

    ran_any = False
    for job in jobs:
        if not job.get("account_list") or not job.get("transaction_detail"):
            print(f"⏭️  {job['company']}: no file paths set yet, skipping")
            continue
        ran_any = True
        print(f"\n=== Loading {job['company']} ===")
        load_company(
            engine,
            company_code=job["company"],
            account_list_path=job["account_list"],
            transaction_detail_path=job["transaction_detail"],
            legal_name=job.get("legal_name") or None,
        )

    if not ran_any:
        print("Nothing to load - fill in COMPANIES at the top of this file, or pass --company/--account-list/--transaction-detail.")
        sys.exit(1)


if __name__ == "__main__":
    main()
