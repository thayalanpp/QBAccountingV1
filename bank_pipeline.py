"""
bank_pipeline.py  -  main entry point for bank transaction exports

For each bank account in BANK_ACCOUNTS, finds its export in the fiscal
year's bank folder and runs it through a LangGraph pipeline:

    read_export ──ok──▶ validate ──pass──▶ save_to_sql ──▶ reconcile_with_qb ──▶ END
         │                  │
         └──error───────────┴──fail──▶ needs_review ──▶ END

    read_export        bank_readers.py  - Scotiabank / TD exports, plain Python
    validate           bank_validator.py - running balances, statement balances,
                       full-year coverage, debit/credit signs, your allocations
    save_to_sql        bank_sql.py - one fin.Statements row per month; rerunning
                       replaces the account's year (never duplicates)
    reconcile_with_qb  qb_matcher.py per month against the QB bank account(s):
                       cheques by number, everything else by amount + date
    needs_review       STUB - records nothing yet; the reason is printed

Only exports that pass validation are saved.

Usage:
    python bank_pipeline.py                       # every company, FY in FISCAL_YEAR_END
    python bank_pipeline.py --only hyg            # one company
    python bank_pipeline.py --only dntl,hyg       # several
    python bank_pipeline.py --allow-partial       # accept an export that stops short of the year end
    python bank_pipeline.py --file "C:\\...\\export.xlsx" --only dntl
    python bank_pipeline.py --show-graph

Folders - one per company, kept separate:
    C:\\NM\\2025yearend\\dntl\\     DNTL export(s), Excel or CSV, and its monthly PDF statements
    ...same for hyg and mgmt.
Every export in a company's bank folder is loaded, so keep only the files you
mean to use there (older copies elsewhere). Statement PDFs are matched to the
account by the account number printed on them.
"""

import argparse
import glob
import os
import sys
from datetime import date, timedelta
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy import text

import bank_sql
import visa_sql
from bank_models import BankExport
from bank_readers import BankReadError, merge_exports, read_bank_export, read_scotia_statement_balances
from bank_validator import BankValidation, validate_bank_export
from qb_loader import fiscal_period
from qb_matcher import MAX_DAYS_APART, REF_MAX_DAYS, match_statement
from sql_helper import ensure_schema, get_engine

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
FISCAL_YEAR_END = 2025
# One folder per company: C:\NM\2025yearend\dntl\ holds the bank export(s) (Excel or CSV)
# and the monthly PDF statements. Set BANK_SUBFOLDER / STATEMENTS_SUBFOLDER to use subfolders instead.
YEAR_ROOT = r"C:\NM\{year}yearend"
BANK_SUBFOLDER = ""             # "" = files directly in the company folder (C:\NM\2025yearend\dntl\)
STATEMENTS_SUBFOLDER = ""       # "" = statement PDFs in the same folder as the export
EXPORT_PATTERNS = ["*.xlsx", "*.xls", "*.csv"]

# file: pattern(s) for the export(s) in the company's bank folder. Several files are joined
#       (e.g. a tagged export + a top-up export for missing days) as long as their dates don't
#       overlap - so keep only the files you mean to load in that folder.
# anchors: (date, balance at the END of that day) typed in from real statements. Scotiabank
#       monthly PDFs in bank\statements are read automatically, so anchors are only needed
#       for statements you don't have as PDFs (e.g. TD).
BANK_ACCOUNTS = [
    {"key": "dntl", "company": "dntl", "name": "Scotiabank Business 26492-00281-18", "institution": "Scotiabank",
     "last_four": "8118", "reader": "scotia", "file": EXPORT_PATTERNS, "qb_accounts": ["10000"],
     "anchors": []},                                      # Scotiabank PDFs in bank\statements are read automatically
    {"key": "hyg", "company": "hyg", "name": "Scotiabank Business 26492-00688-10", "institution": "Scotiabank",
     "last_four": "8810", "reader": "scotia", "file": EXPORT_PATTERNS, "qb_accounts": ["1010", "1010.25"],
     "anchors": []},
    {"key": "mgmt", "company": "mgmt", "name": "TD Business Chequing 0561-5208812", "institution": "TD",
     "last_four": "8812", "reader": "td", "file": EXPORT_PATTERNS, "qb_accounts": ["1010"],
     "anchors": [(date(2025, 7, 31), 23453.48), (date(2025, 8, 29), 24875.74)]},   # Aug 2025 statement
]

QB_SIGN_BANK = +1        # a bank account is an asset: a deposit is + on both sides
ENGINE = None
FY = None                # FiscalPeriod, set in main()
ALLOW_PARTIAL = False


class BankState(TypedDict):
    account: dict
    paths: list[str]
    statements_folder: str
    export: Optional[BankExport]
    validation: Optional[BankValidation]
    account_id: Optional[int]
    saved: list                     # [(StatementID, BankPeriod)]
    recon: list                     # per-month dicts for the summary
    error: Optional[str]
    status: str
    log: list[str]


def _log(state: BankState, msg: str) -> list[str]:
    print(f"   {msg}")
    return state["log"] + [msg]


# ---------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------
def read_export(state: BankState) -> dict:
    acct = state["account"]
    try:
        window = (FY.start, FY.end)          # used only if rows must be put back in the bank's order
        exp = merge_exports([read_bank_export(p, acct["reader"], sheet=acct["key"], window=window)
                             for p in state["paths"]])
    except (BankReadError, Exception) as e:          # an unreadable file never stops the other accounts
        return {"error": f"read: {e}", "log": _log(state, f"📄 read_export: ❌ {e}")}

    # Known balances from monthly Scotiabank PDF statements for this account
    n_pdf = 0
    for pdf in sorted(glob.glob(os.path.join(state["statements_folder"], "*.pdf"))):
        info = read_scotia_statement_balances(pdf)
        if info and info["account"].endswith(acct["last_four"]):
            exp.anchors += info["anchors"]
            n_pdf += 1

    msg = (f"📄 read_export: {len(exp.transactions)} lines {exp.first_date}..{exp.last_date} from "
           f"{len(state['paths'])} file(s)" + ("" if exp.has_bank_balances else
                                         f", {sum(t.balance is None for t in exp.transactions)} without a bank balance")
           + (f"; {n_pdf} statement PDF(s) -> {len(exp.anchors)} known balances" if n_pdf else ""))
    log = _log(state, msg)
    for n in exp.notes:
        log = _log({**state, "log": log}, f"   note: {n}")
    return {"export": exp, "error": None, "log": log}


def validate(state: BankState) -> dict:
    acct, exp = state["account"], state["export"]
    v = validate_bank_export(exp, FY.start, FY.end, acct.get("anchors", []), ALLOW_PARTIAL)
    for c in v.result.checks:
        print(f"      {c}")
    msg = (f"🔢 validate: {'✅' if v.result.passed else '❌'} {v.result.short()}"
           + (f" | FY opening {v.fy_opening:,.2f} -> closing {v.fy_closing:,.2f}" if v.fy_opening is not None else ""))
    return {"validation": v, "error": None if v.result.passed else f"validation: {v.result.short()}",
            "log": _log(state, msg)}


def needs_review(state: BankState) -> dict:
    """STUB: will record the account, file and reason in SQL for a person to review."""
    return {"status": "NeedsReview", "log": _log(state, f"🙋 needs_review: [STUB] {state['error']}")}


def save_to_sql(state: BankState) -> dict:
    acct, exp, v = state["account"], state["export"], state["validation"]
    account_id = visa_sql.get_or_create_account(ENGINE, acct["name"], acct["institution"], acct["last_four"],
                                                account_type="Bank")
    log = state["log"]
    for w in bank_sql.ensure_qb_links(ENGINE, account_id, acct["company"], acct["qb_accounts"]):
        log = _log({**state, "log": log}, f"   ⚠️ {w}")
    saved = bank_sql.save_bank_year(ENGINE, account_id, exp, v, FY.start, FY.end)
    msg = f"💾 save_to_sql: {len(saved)} monthly statements, {v.in_year} transactions" + (
        f" ({v.outside_year} outside FY{FY.fy_end_year} ignored)" if v.outside_year else "")
    return {"account_id": account_id, "saved": saved, "log": _log({**state, "log": log}, msg)}


def reconcile_with_qb(state: BankState) -> dict:
    links = visa_sql.get_qb_links(ENGINE, state["account_id"])
    if not links:
        return {"status": "Saved · no QB link", "log": _log(state, "🔗 reconcile_with_qb: no QB account linked - skipped")}
    ids = [l["chart_account_id"] for l in links]
    rows = []
    for sid, p in state["saved"]:
        ws, we = p.period_start - timedelta(days=REF_MAX_DAYS), p.period_end + timedelta(days=MAX_DAYS_APART)
        stmt_lines = visa_sql.load_statement_lines(ENGINE, sid)
        qb_lines = visa_sql.load_qb_candidates(ENGINE, ids, ws, we, sid)
        r = match_statement(stmt_lines, qb_lines, p.period_start, p.period_end, qb_sign=QB_SIGN_BANK)
        visa_sql.save_qb_recon(ENGINE, sid, links, r, ws, we)
        rows.append({"month": p.period_end.strftime("%Y-%m"), "txns": len(stmt_lines), "opening": p.opening,
                     "closing": p.closing, "matched": len(r.matched), "bank_only": len(r.statement_only),
                     "qb_only": len(r.qb_only), "variance": r.variance})
    tot = {k: sum(x[k] for x in rows) for k in ("txns", "matched", "bank_only", "qb_only")}
    msg = (f"🔗 reconcile_with_qb ({', '.join(l['label'] for l in links)}): {tot['matched']} of {tot['txns']} "
           f"bank lines matched, {tot['bank_only']} bank only, {tot['qb_only']} QB only")
    status = "Partial · reconciled" if state["validation"].partial else "Balanced · reconciled"
    return {"recon": rows, "status": status, "log": _log(state, msg)}


# ---------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------
def after_read(state: BankState) -> str:
    return "needs_review" if state["error"] else "validate"


def after_validate(state: BankState) -> str:
    return "save_to_sql" if state["validation"].result.passed else "needs_review"


def build_graph():
    g = StateGraph(BankState)
    for name, fn in [("read_export", read_export), ("validate", validate), ("needs_review", needs_review),
                     ("save_to_sql", save_to_sql), ("reconcile_with_qb", reconcile_with_qb)]:
        g.add_node(name, fn)
    g.set_entry_point("read_export")
    g.add_conditional_edges("read_export", after_read, {"validate": "validate", "needs_review": "needs_review"})
    g.add_conditional_edges("validate", after_validate, {"save_to_sql": "save_to_sql", "needs_review": "needs_review"})
    g.add_edge("save_to_sql", "reconcile_with_qb")
    g.add_edge("reconcile_with_qb", END)
    g.add_edge("needs_review", END)
    return g.compile()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def _find_files(folder: str, patterns) -> list[str]:
    if isinstance(patterns, str):
        patterns = [patterns]
    found = {f for p in patterns for f in glob.glob(os.path.join(folder, p))}
    return sorted(f for f in found if not os.path.basename(f).startswith("~$"))   # skip Excel lock files


def main():
    global ENGINE, FY, ALLOW_PARTIAL
    ap = argparse.ArgumentParser(description="Load and reconcile a fiscal year of bank transaction exports.")
    ap.add_argument("--fy-end", type=int, default=FISCAL_YEAR_END, help="Fiscal year, named by the year it ends")
    ap.add_argument("--root", help="Override the year folder (default: C:\\NM\\<year>yearend)")
    ap.add_argument("--only", help="Just these companies, comma-separated: " + ", ".join(a["key"] for a in BANK_ACCOUNTS))
    ap.add_argument("--file", action="append", help="Use this export file (needs --only); repeat for several")
    ap.add_argument("--allow-partial", action="store_true", help="Accept an export that doesn't cover the whole year")
    ap.add_argument("--show-graph", action="store_true", help="Print the pipeline graph as Mermaid text and exit")
    args = ap.parse_args()

    app = build_graph()
    if args.show_graph:
        print(app.get_graph().draw_mermaid())
        return
    only = [k.strip().lower() for k in args.only.split(",")] if args.only else None
    if args.file and (not only or len(only) != 1):
        ap.error("--file needs --only <one company>")

    FY = fiscal_period(args.fy_end)
    ALLOW_PARTIAL = args.allow_partial
    root = args.root or YEAR_ROOT.format(year=args.fy_end)
    accounts = [a for a in BANK_ACCOUNTS if not only or a["key"] in only]
    unknown = sorted(set(only or []) - {a["key"] for a in BANK_ACCOUNTS})
    if unknown or not accounts:
        ap.error(f"--only {args.only}: unknown company {', '.join(unknown)} (use {', '.join(a['key'] for a in BANK_ACCOUNTS)})")

    ENGINE = get_engine()
    ensure_schema(ENGINE)
    print(f"🏦 FY{FY.fy_end_year}: {FY.start} .. {FY.end}   {root}\\<company>\\{BANK_SUBFOLDER}")

    results = []
    for acct in accounts:
        folder = os.path.join(root, acct["company"], BANK_SUBFOLDER)
        paths = args.file or _find_files(folder, acct["file"])
        print(f"\n=== {acct['key'].upper()} · {acct['name']} ===")
        missing = [p for p in paths if not os.path.exists(p)]
        if not paths or missing:
            print(f"   ⏭️ no export found ({', '.join(missing) or 'nothing in ' + folder})")
            results.append((acct, "No file", []))
            continue
        for p in paths:
            print(f"   file: {os.path.basename(p)}")
        state: BankState = {"account": acct, "paths": paths, "statements_folder": os.path.join(folder, STATEMENTS_SUBFOLDER),
                            "export": None, "validation": None, "account_id": None,
                            "saved": [], "recon": [], "error": None, "status": "Started", "log": []}
        try:
            final = app.invoke(state)
        except Exception as e:
            print(f"   ❌ unexpected error: {e}")
            final = {**state, "status": "Error"}
        results.append((acct, final["status"], final.get("recon") or []))

    print("\nSummary:")
    for acct, status, rows in results:
        print(f"\n   {acct['key'].upper():5} {status}")
        if rows:
            print(f"   {'Month':8} {'Lines':>5} {'Opening':>12} {'Closing':>12} {'Matched':>7} {'BankOnly':>8} {'QBOnly':>6} {'Variance':>12}")
            for r in rows:
                print(f"   {r['month']:8} {r['txns']:>5} {r['opening']:>12,.2f} {r['closing']:>12,.2f} {r['matched']:>7} "
                      f"{r['bank_only']:>8} {r['qb_only']:>6} {r['variance']:>12,.2f}")

    with ENGINE.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM fin.vw_AccountTransfers")).scalar()
    print(f"\n🔁 {n} candidate transfers between the group's accounts: SELECT * FROM fin.vw_AccountTransfers ORDER BY FromDate;")
    print("Detail per month: SELECT * FROM fin.vw_QBReconDetail WHERE StatementDate = '<month end>' ORDER BY SortOrder, LineDate;")


if __name__ == "__main__":
    main()
