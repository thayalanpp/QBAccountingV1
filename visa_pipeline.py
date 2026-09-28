"""
visa_pipeline.py  -  main entry point for credit card statements

Loops over every PDF in the fiscal year's Visa folder and runs each one
through a LangGraph pipeline:

    read_pdf ──ok──▶ validate ──pass──▶ save_to_sql ──▶ reconcile_with_qb ──▶ END
       │                 │
       └──error──▶ ai_fallback ◀──fail──┘   (only if the Python parser was used)
                         │
                         └──▶ needs_review ──▶ END

Status today:
    read_pdf           REAL  - td_visa_parser.py (Python, no AI)
    validate           REAL  - statement_validator.py (10 arithmetic/date checks)
    save_to_sql        REAL  - visa_sql.py -> fin.Statements / fin.Transactions (rerun-safe)
    reconcile_with_qb  REAL  - qb_matcher.py: statement vs the QB account(s) in fin.AccountLink
    ai_fallback        STUB  - OpenAI / Gemini, once API keys are in .env
    needs_review       STUB  - will record the problem for a person to look at

Only statements that pass validation are saved. Statements are processed
oldest first so each one can be chained to the one before it.

Usage:
    python visa_pipeline.py                      # all PDFs for FISCAL_YEAR_END
    python visa_pipeline.py --fy-end 2025
    python visa_pipeline.py --file "C:\\NM\\2025yearend\\visa\\TD_..._Apr_07-2025.pdf"
    python visa_pipeline.py --show-graph         # print the graph as Mermaid text
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

import visa_sql
from qb_matcher import MAX_DAYS_APART, match_statement
from sql_helper import ensure_schema, get_engine
from statement_validator import ValidationResult, validate_statement
from td_visa_parser import TDVisaParseError, parse_td_visa
from visa_models import CardStatement

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
FISCAL_YEAR_END = 2025
VISA_FOLDER = r"C:\NM\{year}yearend\visa"          # {year} filled from FISCAL_YEAR_END / --fy-end
PARSED_OUTPUT_FOLDER = os.path.join("output_reports", "visa")   # parsed JSON, for inspection (gitignored)

# Which fin.Accounts row each card (by last four digits) belongs to. The name must
# match the one used in fin.AccountLink (TD Visa -> dntl 22200).
CARD_ACCOUNTS = {
    "6761": {"name": "TD Aeroplan Visa Infinite Privilege", "institution": "TD"},
}

ENGINE = None   # set once in main(); the steps below share it


# ---------------------------------------------------------------------
# State: what flows between the steps for ONE statement
# ---------------------------------------------------------------------
class VisaState(TypedDict):
    pdf_path: str
    statement: Optional[CardStatement]
    extractor: Optional[str]        # which extractor produced `statement`
    error: Optional[str]            # why the last step failed, if it did
    validation_passed: Optional[bool]
    validation: Optional[ValidationResult]
    account_id: Optional[int]       # fin.Accounts
    statement_id: Optional[int]     # fin.Statements, once saved
    recon: Optional[dict]           # QB reconciliation counts, for the summary
    status: str                     # final outcome shown in the summary
    log: list[str]                  # one line per step, for the run summary


def _log(state: VisaState, message: str) -> list[str]:
    print(f"   {message}")
    return state["log"] + [message]


# ---------------------------------------------------------------------
# Steps (graph nodes). Each returns only the fields it changes.
# ---------------------------------------------------------------------
def read_pdf(state: VisaState) -> dict:
    """REAL: read the PDF with the Python TD parser."""
    try:
        stmt = parse_td_visa(state["pdf_path"])
    except TDVisaParseError as e:
        return {"error": f"parser: {e}", "log": _log(state, f"📄 read_pdf: could not parse - {e}")}

    s = stmt.summary
    msg = (f"📄 read_pdf: {stmt.statement_date}  period {stmt.period_start}..{stmt.period_end}  "
           f"{len(stmt.transactions)} txns  opening {s.previous_balance:,.2f}  new balance {s.new_balance:,.2f}")

    # Save what was read so it can be inspected (output_reports/ is gitignored).
    os.makedirs(PARSED_OUTPUT_FOLDER, exist_ok=True)
    out_path = os.path.join(PARSED_OUTPUT_FOLDER, os.path.splitext(stmt.source_file)[0] + ".json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(stmt.model_dump_json(indent=2))

    log = _log(state, msg)
    for n in stmt.notes:
        log = _log({**state, "log": log}, f"   note: {n}")
    return {"statement": stmt, "extractor": stmt.extractor, "error": None, "log": log}


def _card_account(stmt: CardStatement) -> Optional[dict]:
    return CARD_ACCOUNTS.get(stmt.card_last_four or "")


def validate(state: VisaState) -> dict:
    """REAL: arithmetic and date checks, plus chaining to the previous statement already in SQL."""
    stmt = state["statement"]
    card = _card_account(stmt)
    if card is None:
        return {"validation_passed": False, "error": f"card ending {stmt.card_last_four} not in CARD_ACCOUNTS",
                "log": _log(state, f"🔢 validate: ❌ card ending {stmt.card_last_four} is not set up in CARD_ACCOUNTS")}

    account_id = visa_sql.find_account_id(ENGINE, card["name"])
    prev_balance = visa_sql.get_statement_new_balance(ENGINE, account_id, stmt.previous_statement_date)
    result = validate_statement(stmt, prev_balance, f"{stmt.previous_statement_date} statement")

    for check in result.checks:
        if not check.passed or check.skipped:
            print(f"      {check}")
    log = _log(state, f"🔢 validate: {'✅' if result.passed else '❌'} {result.short()}")
    return {"validation_passed": result.passed, "validation": result, "account_id": account_id,
            "error": None if result.passed else f"validation: {result.short()}", "log": log}


def ai_fallback(state: VisaState) -> dict:
    """STUB: will send the PDF to OpenAI or Gemini and return a CardStatement."""
    return {"extractor": "ai (not built)",
            "log": _log(state, f"🤖 ai_fallback: [STUB] would re-read with OpenAI/Gemini because: {state['error']}")}


def needs_review(state: VisaState) -> dict:
    """STUB: will record the statement and the reason in SQL for a person to review."""
    return {"status": "NeedsReview",
            "log": _log(state, "🙋 needs_review: [STUB] would record this for manual review")}


def save_to_sql(state: VisaState) -> dict:
    """REAL: write the statement and its lines to fin.* (replaces a previous run of the same statement)."""
    stmt = state["statement"]
    card = _card_account(stmt)
    account_id = visa_sql.get_or_create_account(ENGINE, card["name"], card["institution"], stmt.card_last_four)
    statement_id = visa_sql.save_statement(ENGINE, account_id, stmt, state["validation"])
    return {"account_id": account_id, "statement_id": statement_id,
            "log": _log(state, f"💾 save_to_sql: StatementID {statement_id}, {len(stmt.transactions)} transactions")}


def reconcile_with_qb(state: VisaState) -> dict:
    """REAL: match statement lines to the linked QuickBooks account(s) and itemize every difference."""
    stmt, statement_id = state["statement"], state["statement_id"]
    links = visa_sql.get_qb_links(ENGINE, state["account_id"])
    if not links:
        return {"status": "Balanced · no QB link",
                "log": _log(state, "🔗 reconcile_with_qb: no fin.AccountLink row for this card - skipped")}

    window_start = stmt.period_start - timedelta(days=MAX_DAYS_APART)
    window_end = stmt.period_end + timedelta(days=MAX_DAYS_APART)
    stmt_lines = visa_sql.load_statement_lines(ENGINE, statement_id)
    qb_lines = visa_sql.load_qb_candidates(ENGINE, [l["chart_account_id"] for l in links],
                                           window_start, window_end, statement_id)
    result = match_statement(stmt_lines, qb_lines, stmt.period_start, stmt.period_end)
    visa_sql.save_qb_recon(ENGINE, statement_id, links, result, window_start, window_end)

    for s_line in result.statement_only:
        print(f"      card only : {s_line.posting_date}  {s_line.amount:>11,.2f}  {s_line.description[:45]}")
    for q in result.qb_only:
        label = " ".join(str(x) for x in (q.txn_type, q.ref_num, q.name or q.memo) if x)
        print(f"      QB only   : {q.txn_date}  {q.amount:>11,.2f}  {label[:45]}")

    recon = {"matched": len(result.matched), "card_only": len(result.statement_only),
             "qb_only": len(result.qb_only), "variance": result.variance}
    msg = (f"🔗 reconcile_with_qb ({', '.join(l['label'] for l in links)}): "
           f"{recon['matched']} matched, {recon['card_only']} card only, {recon['qb_only']} QB only, "
           f"variance {result.variance:,.2f}")
    return {"recon": recon, "status": "Balanced · reconciled", "log": _log(state, msg)}


# ---------------------------------------------------------------------
# Routing: the decisions between steps
# ---------------------------------------------------------------------
def after_read(state: VisaState) -> str:
    return "validate" if state["statement"] is not None and state["error"] is None else "ai_fallback"


def after_validate(state: VisaState) -> str:
    if state["validation_passed"]:
        return "save_to_sql"
    # A parser result that fails validation gets one AI attempt; an AI result that fails goes to a person.
    return "ai_fallback" if state["extractor"] == "td_visa_parser" else "needs_review"


def after_ai(state: VisaState) -> str:
    # Until ai_fallback is built it never produces a statement, so this always goes to review.
    return "validate" if state["extractor"] in ("openai", "gemini") and state["statement"] else "needs_review"


def build_graph():
    g = StateGraph(VisaState)
    for name, fn in [("read_pdf", read_pdf), ("validate", validate), ("ai_fallback", ai_fallback),
                     ("needs_review", needs_review), ("save_to_sql", save_to_sql),
                     ("reconcile_with_qb", reconcile_with_qb)]:
        g.add_node(name, fn)

    g.set_entry_point("read_pdf")
    g.add_conditional_edges("read_pdf", after_read, {"validate": "validate", "ai_fallback": "ai_fallback"})
    g.add_conditional_edges("validate", after_validate,
                            {"save_to_sql": "save_to_sql", "ai_fallback": "ai_fallback", "needs_review": "needs_review"})
    g.add_conditional_edges("ai_fallback", after_ai, {"validate": "validate", "needs_review": "needs_review"})
    g.add_edge("save_to_sql", "reconcile_with_qb")
    g.add_edge("reconcile_with_qb", END)
    g.add_edge("needs_review", END)
    return g.compile()


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------
def _statement_date_from_name(path: str):
    """TD file names end in e.g. '_Apr_07-2025.pdf' - used to process oldest first."""
    m = re.search(r"_([A-Za-z]{3})_(\d{2})-(\d{4})\.pdf$", os.path.basename(path))
    if m:
        try:
            return datetime.strptime(f"{m[1]} {m[2]} {m[3]}", "%b %d %Y")
        except ValueError:
            pass
    return datetime.max   # unknown names go last


def main():
    parser = argparse.ArgumentParser(description="Process a fiscal year's credit card statement PDFs.")
    parser.add_argument("--fy-end", type=int, default=FISCAL_YEAR_END,
                        help=f"Fiscal year, named by the year it ends (default {FISCAL_YEAR_END})")
    parser.add_argument("--folder", help="Override the PDF folder (default: VISA_FOLDER for the year)")
    parser.add_argument("--file", help="Process just this one PDF")
    parser.add_argument("--show-graph", action="store_true", help="Print the pipeline graph as Mermaid text and exit")
    args = parser.parse_args()

    app = build_graph()
    if args.show_graph:
        print(app.get_graph().draw_mermaid())
        return

    if args.file:
        pdfs = [args.file]
    else:
        folder = args.folder or VISA_FOLDER.format(year=args.fy_end)
        if not os.path.isdir(folder):
            print(f"❌ Folder not found: {folder}")
            sys.exit(1)
        pdfs = sorted(glob.glob(os.path.join(folder, "*.pdf")), key=_statement_date_from_name)
        print(f"📂 {folder}: {len(pdfs)} PDF(s), FY{args.fy_end}")

    if not pdfs:
        print("Nothing to process.")
        sys.exit(1)

    global ENGINE
    ENGINE = get_engine()
    ensure_schema(ENGINE)

    results = []
    for i, pdf_path in enumerate(pdfs, start=1):
        print(f"\n=== [{i}/{len(pdfs)}] {os.path.basename(pdf_path)} ===")
        initial: VisaState = {"pdf_path": pdf_path, "statement": None, "extractor": None, "error": None,
                              "validation_passed": None, "validation": None, "account_id": None,
                              "statement_id": None, "recon": None, "status": "Started", "log": []}
        try:
            final = app.invoke(initial)
        except Exception as e:   # one bad file never stops the rest of the loop
            print(f"   ❌ unexpected error: {e}")
            final = {**initial, "status": "Error"}
        stmt, recon = final.get("statement"), final.get("recon")
        results.append((stmt.statement_date if stmt else None, len(stmt.transactions) if stmt else 0,
                        final["status"], recon, os.path.basename(pdf_path)))

    print("\nSummary:")
    print(f"   {'Statement':11} {'Txns':>4}  {'Status':24} {'Matched':>7} {'CardOnly':>8} {'QBOnly':>6} {'Variance':>11}  File")
    for sdate, count, status, recon, name in results:
        r = recon or {}
        var = f"{r['variance']:,.2f}" if recon else "-"
        print(f"   {str(sdate or '-'):11} {count:>4}  {status:24} {r.get('matched', '-'):>7} "
              f"{r.get('card_only', '-'):>8} {r.get('qb_only', '-'):>6} {var:>11}  {name}")
    print("\nDetail: SELECT * FROM fin.vw_QBReconDetail WHERE StatementDate = '<date>' ORDER BY SortOrder, LineDate;")


if __name__ == "__main__":
    main()
