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
    validate           STUB  - next step to build (statement_validator.py)
    ai_fallback        STUB  - OpenAI / Gemini, once API keys are in .env
    save_to_sql        STUB  - fin.* tables, rerun-safe
    reconcile_with_qb  STUB  - statement period vs QB account 22200
    needs_review       STUB  - will record the problem for a person to look at

Each stub prints what it would do and passes the statement along, so the
whole flow runs end to end now and each step can be filled in on its own.

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
from datetime import datetime
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from td_visa_parser import TDVisaParseError, parse_td_visa
from visa_models import CardStatement, TxnType

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
FISCAL_YEAR_END = 2025
VISA_FOLDER = r"C:\NM\{year}yearend\visa"          # {year} filled from FISCAL_YEAR_END / --fy-end
PARSED_OUTPUT_FOLDER = os.path.join("output_reports", "visa")   # parsed JSON, for inspection (gitignored)


# ---------------------------------------------------------------------
# State: what flows between the steps for ONE statement
# ---------------------------------------------------------------------
class VisaState(TypedDict):
    pdf_path: str
    statement: Optional[CardStatement]
    extractor: Optional[str]        # which extractor produced `statement`
    error: Optional[str]            # why the last step failed, if it did
    validation_passed: Optional[bool]
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

    return {"statement": stmt, "extractor": stmt.extractor, "error": None, "log": _log(state, msg)}


def validate(state: VisaState) -> dict:
    """STUB: will check summary math, totals by type, posting dates, and chaining to the previous statement."""
    stmt = state["statement"]
    # Preview only (not the real validator): the totals the validator will compare.
    preview = (f"purchases {stmt.total_by_type(TxnType.PURCHASE):,.2f} vs printed {stmt.summary.purchases:,.2f}, "
               f"payments+refunds {-(stmt.total_by_type(TxnType.PAYMENT) + stmt.total_by_type(TxnType.REFUND)):,.2f} "
               f"vs printed {stmt.summary.payments_and_credits:,.2f}")
    return {"validation_passed": True,
            "log": _log(state, f"🔢 validate: [STUB - passes everything] {preview}")}


def ai_fallback(state: VisaState) -> dict:
    """STUB: will send the PDF to OpenAI or Gemini and return a CardStatement."""
    return {"extractor": "ai (not built)",
            "log": _log(state, f"🤖 ai_fallback: [STUB] would re-read with OpenAI/Gemini because: {state['error']}")}


def needs_review(state: VisaState) -> dict:
    """STUB: will record the statement and the reason in SQL for a person to review."""
    return {"status": "NeedsReview",
            "log": _log(state, "🙋 needs_review: [STUB] would record this for manual review")}


def save_to_sql(state: VisaState) -> dict:
    """STUB: will write fin.Statements / fin.Transactions (replace-on-rerun) and log the run."""
    return {"log": _log(state, "💾 save_to_sql: [STUB] would save statement + transactions")}


def reconcile_with_qb(state: VisaState) -> dict:
    """STUB: will compare this statement period against the linked QB account (dntl 22200)."""
    return {"status": "Read OK (later steps are stubs)",
            "log": _log(state, "🔗 reconcile_with_qb: [STUB] would compare with QuickBooks")}


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

    results = []
    for i, pdf_path in enumerate(pdfs, start=1):
        print(f"\n=== [{i}/{len(pdfs)}] {os.path.basename(pdf_path)} ===")
        initial: VisaState = {"pdf_path": pdf_path, "statement": None, "extractor": None, "error": None,
                              "validation_passed": None, "status": "Started", "log": []}
        try:
            final = app.invoke(initial)
        except Exception as e:   # one bad file never stops the rest of the loop
            print(f"   ❌ unexpected error: {e}")
            final = {**initial, "status": "Error"}
        stmt = final.get("statement")
        results.append((os.path.basename(pdf_path), stmt.statement_date if stmt else None,
                        len(stmt.transactions) if stmt else 0, final["status"]))

    print("\nSummary:")
    print(f"   {'Statement':12} {'Txns':>5}  {'Status':34} File")
    for name, sdate, count, status in results:
        print(f"   {str(sdate or '-'):12} {count:>5}  {status:34} {name}")


if __name__ == "__main__":
    main()
