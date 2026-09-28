"""
split_pipeline.py  -  checks the monthly Dental / Hygiene split workbook

    read_workbook ─▶ load_evidence ─▶ assess ─▶ save_to_sql ─▶ write_report ─▶ END

    read_workbook   split_reader.py - NM_<year>.xlsx, as is (the year is closed)
    load_evidence   card statements (visa_pipeline), Dental + Hygiene banks
                    (bank_pipeline), Dental QuickBooks (run_qb_load)
    assess          split_assess.py - per month: workbook, links, cash test,
                    expense test, QuickBooks postings
    save_to_sql     fin.SplitMonths / SplitLines / SplitLabItems + fin.Findings
    write_report    <year>yearend\\split_review_FY<year>.xlsx - one line per month,
                    proposed adjustments, lab fees, QB postings, findings

Run it after the other pipelines have loaded the year:
    python split_pipeline.py
    python split_pipeline.py --workbook "C:\\NM\\2025yearend\\NM_2025.xlsx"

Nothing in QuickBooks or the workbook is changed.
"""

import argparse
import os
import sys
from datetime import timedelta
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import split_sql
from qb_loader import fiscal_period
from split_assess import Evidence, MonthResult, assess_month
from split_reader import read_split_workbook
from sql_helper import ensure_schema, get_engine

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
FISCAL_YEAR_END = 2025
YEAR_ROOT = r"C:\NM\{year}yearend"
WORKBOOK = r"NM_{year}.xlsx"                       # in YEAR_ROOT
REPORT = r"split_review_FY{year}.xlsx"             # written to YEAR_ROOT

CARD_ACCOUNT = "TD Aeroplan Visa Infinite Privilege"          # fin.Accounts (visa_pipeline)
DNTL_BANK = "Scotiabank Business 26492-00281-18"              # fin.Accounts (bank_pipeline)
HYG_BANK = "Scotiabank Business 26492-00688-10"
QB_COMPANY = "dntl"
DNTL_BANK_QB_ACCOUNT = "10000"
CARD_QB_ACCOUNT = "22200"
VISA_PAYMENT_PATTERN = "td visa"                  # Hygiene's payments to TD look like "Pc-Td Visa 38099194"

ENGINE = None


class SplitState(TypedDict):
    workbook: str
    report: str
    fy: object
    months: list
    evidence: Optional[Evidence]
    results: list
    findings: list
    error: Optional[str]


def read_workbook(state: SplitState) -> dict:
    try:
        months = read_split_workbook(state["workbook"])
    except Exception as e:
        return {"error": f"can't read {state['workbook']}: {e}"}
    months = [m for m in months if state["fy"].start <= m.month <= state["fy"].end]
    print(f"📘 read_workbook: {len(months)} month sheets in FY{state['fy'].fy_end_year}: "
          + ", ".join(m.sheet for m in months))
    return {"months": months, "error": None if months else "no month sheets inside the fiscal year"}


def load_evidence(state: SplitState) -> dict:
    fy = state["fy"]
    ev = Evidence(cycles=split_sql.load_card_cycles(ENGINE, CARD_ACCOUNT),
                  dntl_bank=split_sql.load_bank_lines(ENGINE, DNTL_BANK),
                  hyg_bank=split_sql.load_bank_lines(ENGINE, HYG_BANK),
                  all_withdrawals=split_sql.load_all_bank_withdrawals(ENGINE),
                  qb_dntl=split_sql.load_qb_lines(ENGINE, QB_COMPANY, fy.start - timedelta(days=60),
                                                  fy.end + timedelta(days=90)),
                  dntl_bank_qb_account=DNTL_BANK_QB_ACCOUNT, card_qb_account=CARD_QB_ACCOUNT,
                  visa_payment_pattern=VISA_PAYMENT_PATTERN)
    print(f"🔎 load_evidence: {len(ev.cycles)} card statements, {len(ev.dntl_bank)} Dental bank lines, "
          f"{len(ev.hyg_bank)} Hygiene bank lines, {len(ev.qb_dntl)} Dental QB lines")
    missing = [n for n, v in [("card statements", ev.cycles), ("Dental bank", ev.dntl_bank),
                              ("Hygiene bank", ev.hyg_bank), ("Dental QuickBooks", ev.qb_dntl)] if not v]
    if missing:
        print(f"   ⚠️ not loaded yet: {', '.join(missing)} - those checks will be skipped")
    if not ev.hyg_bank:
        print("   ⚠️ the TD Visa is paid from Hygiene's bank - without it the cash and expense tests can't run. "
              "Load it first:  python bank_pipeline.py --only hyg")
    return {"evidence": ev}


def assess(state: SplitState) -> dict:
    results, lab_claims, prev = [], {}, None
    for m in state["months"]:
        r = assess_month(m, prev, state["evidence"], lab_claims)
        results.append(r)
        prev = m
        issues = [f for f in r.findings if f["severity"] == "Issue"]
        print(f"   {m.sheet:10} cheque {r.cheque_number or '-':>4}  B6 {m.visa_due:>10,.2f}  "
              f"paid {r.bank_paid if r.bank_paid is not None else '-':>10}  base "
              f"{r.expense_base if r.expense_base is not None else '-':>10}  -> {r.status}")
        for f in issues:
            print(f"      ❗ {f['area']}: {f['message']}")
    findings = [f for r in results for f in r.findings]
    return {"results": results, "findings": findings}


def save_to_sql(state: SplitState) -> dict:
    split_sql.save_results(ENGINE, state["fy"].fy_end_year, os.path.basename(state["workbook"]),
                           state["results"], state["findings"])
    print(f"💾 save_to_sql: {len(state['results'])} months, {len(state['findings'])} findings (fin.Findings, Source='split')")
    return {}


# ---------------------------------------------------------------------
# Excel review report
# ---------------------------------------------------------------------
F, FB = Font(name="Arial"), Font(name="Arial", bold=True)
HDR = Font(name="Arial", bold=True, color="FFFFFF")
HFILL = PatternFill("solid", fgColor="1F4E78")
ISSUE = PatternFill("solid", fgColor="FCE4D6")
NUM = '#,##0.00;(#,##0.00);"-"'


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    for i, (h, w) in enumerate(zip(headers, widths), 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font, c.fill = HDR, HFILL
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 45
    ws.freeze_panes = "B2"
    return ws


def write_report(state: SplitState) -> dict:
    results: list[MonthResult] = state["results"]
    wb = Workbook()
    readme = wb.active
    readme.title = "Read me"
    lines = [
        (f"Monthly split review - FY{state['fy'].fy_end_year} ({state['fy'].start} to {state['fy'].end})", True),
        (f"Source: {os.path.basename(state['workbook'])}, read as is. Nothing in QuickBooks or the workbook was changed.", False),
        ("", False),
        ("Cash test - B6 ('visa payment due') is booked in QB as PAID on 22200 (cheque visa portion C6 + "
         "'hygiene supplies' journal B6 - C6). 'Hygiene paid' is what actually left Hygiene's bank to TD after that "
         "statement and before the next. Cash diff = recorded as paid but not paid.", False),
        ("Expense test - the split should apply to the card cycle's own charges: new balance - previous balance + "
         "real payments. Lab fees count at 100% only when the charge is on that statement. "
         "DPC diff = correct Dental visa portion - booked (C6); + means Dental was under-charged.", False),
        ("", False),
        ("Proposed adjustments are for your accountant to review - not entries made by this tool.", False),
    ]
    for i, (t, b) in enumerate(lines, 1):
        c = readme.cell(row=i, column=1, value=t)
        c.font, c.alignment = (FB if b else F), Alignment(wrap_text=True)
    readme.column_dimensions["A"].width = 140

    cols = ["Sheet", "Cheque #", "Cheque date", "Split cheque (C8)", "Hygiene deposit", "Card statement",
            "Visa due (B6)", "Hygiene paid TD", "Cash diff", "Cycle charges", "Lab on card", "Booked DPC (C6)",
            "Correct DPC", "DPC diff", "QB cheque", "QB journal", "Status"]
    ws = _sheet(wb, "Summary", cols, [11, 9, 12, 13, 12, 12, 13, 13, 12, 13, 12, 13, 13, 12, 9, 9, 14])
    for i, r in enumerate(results, 2):
        m = r.month
        row = [m.sheet, r.cheque_number, r.cheque_date, round(m.cheque_total, 2), r.deposit_date, r.statement_date,
               m.visa_due, r.bank_paid, r.cash_diff, r.expense_base, r.lab_on_card, round(m.visa_dpc, 2),
               r.correct_dpc, r.dpc_diff,
               None if r.qb_cheque_found is None else ("yes" if r.qb_cheque_found else "NO"),
               None if r.qb_journal_found is None else ("yes" if r.qb_journal_found else "NO"), r.status]
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.font = F
            if 4 <= j <= 14 and j not in (5, 6):
                c.number_format = NUM
            if j in (3, 5, 6):
                c.number_format = "yyyy-mm-dd"
        if r.status not in ("OK",):
            ws.cell(row=i, column=17).fill = ISSUE
    t = len(results) + 2
    ws.cell(row=t, column=1, value="Total").font = FB
    for j in (4, 7, 8, 9, 10, 11, 12, 13, 14):
        L = get_column_letter(j)
        c = ws.cell(row=t, column=j, value=f"=SUM({L}2:{L}{t - 1})")
        c.font, c.number_format = FB, NUM

    adj = _sheet(wb, "Proposed adjustments", ["Sheet", "Card statement", "Area", "Amount", "Explanation"],
                 [11, 13, 22, 13, 110])
    n = 2
    for r in results:
        for area, amt, why in [
            ("Dental visa expense", r.dpc_diff,
             f"correct Dental visa portion {r.correct_dpc:,.2f} vs booked {r.month.visa_dpc:,.2f}" if r.correct_dpc is not None else ""),
            ("22200 paid but not paid", r.cash_diff,
             f"B6 {r.month.visa_due:,.2f} booked as paid; Hygiene paid {r.bank_paid:,.2f}" if r.bank_paid is not None else "")]:
            if amt is not None and abs(amt) >= 0.01:
                for j, v in enumerate([r.month.sheet, r.statement_date, area, amt, why], 1):
                    c = adj.cell(row=n, column=j, value=v)
                    c.font = F
                    if j == 4: c.number_format = NUM
                    if j == 2: c.number_format = "yyyy-mm-dd"
                n += 1
    adj.cell(row=n + 1, column=3, value="Total Dental visa expense").font = FB
    c = adj.cell(row=n + 1, column=4, value=f'=SUMIF(C2:C{n},"Dental visa expense",D2:D{n})')
    c.font, c.number_format = FB, NUM
    adj.cell(row=n + 2, column=3, value="Total 22200 paid but not paid").font = FB
    c = adj.cell(row=n + 2, column=4, value=f'=SUMIF(C2:C{n},"22200 paid but not paid",D2:D{n})')
    c.font, c.number_format = FB, NUM

    lab = _sheet(wb, "Lab fees", ["Sheet", "Description (workbook)", "Amount", "Found on card", "Card date"],
                 [11, 50, 12, 13, 12])
    n = 2
    for r in results:
        for item in r.month.lab_items:
            hit = r.lab_matches.get(item.row)
            for j, v in enumerate([r.month.sheet, item.description, item.amount, "yes" if hit else "NO",
                                   hit[1] if hit else None], 1):
                c = lab.cell(row=n, column=j, value=v)
                c.font = F
                if j == 3: c.number_format = NUM
                if j == 5: c.number_format = "yyyy-mm-dd"
            if not hit:
                lab.cell(row=n, column=4).fill = ISSUE
            n += 1

    cats = ["salary", "rent", "lease", "bookkeeping", "visa"]
    qbp = _sheet(wb, "QB postings", ["Sheet", "Cheque #"] + [f"{c} -> Dental account" for c in cats],
                 [11, 9] + [34] * len(cats))
    for i, r in enumerate(results, 2):
        qbp.cell(row=i, column=1, value=r.month.sheet).font = F
        qbp.cell(row=i, column=2, value=r.cheque_number).font = F
        for j, cat in enumerate(cats, 3):
            p = r.qb_postings.get(cat)
            qbp.cell(row=i, column=j, value=f"{p[0]}  ({p[1]:,.2f})" if p else None).font = F

    fnd = _sheet(wb, "Findings", ["Sheet", "Area", "Severity", "Amount", "Message"], [11, 10, 9, 12, 120])
    order = {"Issue": 0, "Warning": 1, "Info": 2}
    for i, f in enumerate(sorted(state["findings"], key=lambda f: (order[f["severity"]], f["subject"])), 2):
        for j, v in enumerate([f["subject"].replace("Split ", ""), f["area"], f["severity"], f.get("amount"),
                               f["message"]], 1):
            c = fnd.cell(row=i, column=j, value=v)
            c.font = F
            if j == 4: c.number_format = NUM
            if j == 5: c.alignment = Alignment(wrap_text=True, vertical="top")
        if f["severity"] == "Issue":
            fnd.cell(row=i, column=3).fill = ISSUE

    try:
        wb.save(state["report"])
        print(f"📊 write_report: {state['report']}")
    except PermissionError:
        print(f"❌ write_report: {state['report']} is open in Excel - close it and rerun")
    return {}


def stop(state: SplitState) -> dict:
    print(f"❌ {state['error']}")
    return {}


def build_graph():
    g = StateGraph(SplitState)
    for name, fn in [("read_workbook", read_workbook), ("load_evidence", load_evidence), ("assess", assess),
                     ("save_to_sql", save_to_sql), ("write_report", write_report), ("stop", stop)]:
        g.add_node(name, fn)
    g.set_entry_point("read_workbook")
    g.add_conditional_edges("read_workbook", lambda s: "stop" if s["error"] else "load_evidence",
                            {"stop": "stop", "load_evidence": "load_evidence"})
    g.add_edge("load_evidence", "assess")
    g.add_edge("assess", "save_to_sql")
    g.add_edge("save_to_sql", "write_report")
    g.add_edge("write_report", END)
    g.add_edge("stop", END)
    return g.compile()


def main():
    global ENGINE
    ap = argparse.ArgumentParser(description="Check the monthly Dental / Hygiene split workbook against the bank, card and QB data.")
    ap.add_argument("--fy-end", type=int, default=FISCAL_YEAR_END)
    ap.add_argument("--workbook", help="Path to the split workbook (default: <year root>\\NM_<year>.xlsx)")
    ap.add_argument("--report", help="Where to write the review workbook")
    ap.add_argument("--root", help="Override the year folder")
    ap.add_argument("--show-graph", action="store_true")
    args = ap.parse_args()
    app = build_graph()
    if args.show_graph:
        print(app.get_graph().draw_mermaid())
        return
    root = args.root or YEAR_ROOT.format(year=args.fy_end)
    workbook = args.workbook or os.path.join(root, WORKBOOK.format(year=args.fy_end))
    report = args.report or os.path.join(root, REPORT.format(year=args.fy_end))
    if not os.path.exists(workbook):
        sys.exit(f"❌ Split workbook not found: {workbook}")
    ENGINE = get_engine()
    ensure_schema(ENGINE)
    fy = fiscal_period(args.fy_end)
    print(f"🧾 Split review FY{fy.fy_end_year}: {fy.start} .. {fy.end}")
    app.invoke({"workbook": workbook, "report": report, "fy": fy, "months": [], "evidence": None,
                "results": [], "findings": [], "error": None})


if __name__ == "__main__":
    main()
