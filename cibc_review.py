"""
cibc_review.py  -  ONE-OFF quality-control script (not part of the regular pipelines)

Why: before Feb 2023 Dental's QuickBooks account 22200 tracked the CIBC Aeroplan Visa
(card ending 5451), not the TD card. Those statements were never loaded, so the
pre-FY2024 difference on 22200 (15,772.83 at Aug 31 2023) could not be traced.

What it does:
  1. Reads every CIBC statement PDF in the folders given (duplicates of the same
     statement date are read once).
  2. Checks each statement adds up - nothing is used from a statement that doesn't:
       previous - payments - other credits + purchases + cash advances + interest + fees = total balance
       payment lines = Payments;  charge/credit lines = purchases - other credits + interest + fees + cash
  3. Matches each card charge/credit to Dental's QuickBooks 22200 lines (same amount to the
     cent, within MATCH_DAYS of the transaction or posting date). Each QB line is used once.
  4. Tags every card line:
       Matched to QB          - in Dental's books (evidence: the QB line)
       Likely Dental (vendor) - not in QB, but the vendor matched QB elsewhere on this card - REVIEW
       Payment                - a payment to the card (who paid: see the description)
       Interest / adjustment  - card interest, interest reversals, small CIBC adjustments
       Other                  - personal / unknown - listed only
  5. Writes an Excel review (and, with --save-db, the lines to fin.CibcLines - a separate
     table the regular pipelines never read; drop it when done).

Nothing is written to QuickBooks or to the regular fin.* card tables.

Run with the data-load project's Python (it has pdfplumber, pandas, SQLAlchemy and the DB settings):

    C:\\NM\\Claude1_QbVisaAgenticSupport\\.venv\\Scripts\\python.exe cibc_review.py
    ... cibc_review.py --no-db            (read and check the PDFs only)
    ... cibc_review.py --save-db          (also write fin.CibcLines)
"""

import argparse
import glob
import os
import re
import sys
from datetime import date, timedelta

import pdfplumber
import pandas as pd

DATA_LOAD_REPO = r"C:\NM\Claude1_QbVisaAgenticSupport"
DEFAULT_FOLDERS = [r"C:\NM\2022yearend\visa\2021", r"C:\NM\2022yearend\visa\CIBC", r"C:\NM\2023yearend\visa"]
DEFAULT_OUT = r"C:\NM\2023yearend\cibc_review_FY2022_FY2023.xlsx"
MATCH_DAYS = 7
QB_FROM, QB_TO = date(2021, 7, 1), date(2023, 10, 31)

MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
FULL_MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August",
                                           "September", "October", "November", "December"], 1)}
CATEGORIES = ["Hotel, Entertainment and Recreation", "Home and Office Improvement", "Personal and Household Expenses",
              "Professional and Financial Services", "Transportation", "Health and Education",
              "Foreign Currency Transactions", "Retail and Grocery", "Restaurants", "Cash Advances"]
# Vendors used by both the practice and the household - a name match alone is weak evidence.
MIXED_VENDORS = {"AMAZON", "AMZN", "COSTCO", "UBER", "APPLE", "WALMART", "PAYPAL", "DOLLARAMA", "SHOPPERS",
                 "REXALL", "LOBLAWS", "METRO", "HUDSON'S", "CANADIAN", "IKEA", "BEST", "STAPLES", "WWW"}

LINE_RE = re.compile(r"^([A-Z][a-z]{2}) (\d{2}) ([A-Z][a-z]{2}) (\d{2}) (.+?) (-?[\d,]+\.\d{2})\**$")   # *** marks interest adjustments
MONEY = r"\$?(-?[\d,]+\.\d{2})\s?(CR)?"


class CibcParseError(Exception):
    pass


def _money(s, cr=None):
    v = float(s.replace(",", ""))
    return -v if cr else v


def _summary_value(text, label):
    m = re.search(rf"^{label}\s*[-+=]?\s*{MONEY}", text, re.M)
    if not m:
        raise CibcParseError(f"summary line '{label}' not found")
    return _money(m.group(1), m.group(2))


def parse_cibc(path):
    with pdfplumber.open(path) as pdf:
        pages = [p.extract_text() or "" for p in pdf.pages]
    text = "\n".join(pages)
    if "CIBC" not in text or "Your account at a glance" not in text:
        raise CibcParseError("not a CIBC credit card statement")

    # "May 20to June 19, 2022" or "December 20,2021to January 19, 2022"
    m = re.search(r"([A-Z][a-z]+) (\d{1,2}),?\s?(\d{4})?\s?to ([A-Z][a-z]+) (\d{1,2}), (\d{4})", text)
    if not m or m.group(1) not in FULL_MONTHS or m.group(4) not in FULL_MONTHS:
        raise CibcParseError("statement period not found")
    sm, sd, em, ed, ey = FULL_MONTHS[m.group(1)], int(m.group(2)), FULL_MONTHS[m.group(4)], int(m.group(5)), int(m.group(6))
    sy = int(m.group(3)) if m.group(3) else (ey - 1 if sm > em else ey)
    p_start, p_end = date(sy, sm, sd), date(ey, em, ed)

    acct = re.search(r"Account number\s*\n?\s*(4500 [\dX ]{14}\d{4})", text)
    st = {
        "file": os.path.basename(path), "period_start": p_start, "statement_date": p_end,
        "card": acct.group(1)[-4:] if acct else None,
        "previous": _summary_value(text, "Previous balance"),
        "payments": _summary_value(text, "Payments"),
        "other_credits": _summary_value(text, "Other credits"),
        "purchases": _summary_value(text, "Purchases"),
        "cash_advances": _summary_value(text, "Cash advances"),
        "interest": _summary_value(text, "Interest"),
        "fees": _summary_value(text, "Fees"),
        "total": _summary_value(text, "Total balance"),
    }

    def to_date(mon, day):
        """Statement dates print without a year - take the year that puts the date nearest the period."""
        cands = []
        for y in (ey - 1, ey):
            try:
                cands.append(date(y, MONTHS[mon], int(day)))
            except ValueError:
                pass
        def dist(d):
            return 0 if p_start <= d <= p_end else min(abs((d - p_start).days), abs((d - p_end).days))
        return min(cands, key=dist)

    lines, section = [], None
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("Your payments"):
            section = "payment"; continue
        if s.startswith("Your new charges and credits") or s.startswith("Your interest") or s.startswith("Your fees"):
            section = "charge"; continue
        if s.startswith("Information about your CIBC") or s.startswith("Prepared for:"):
            section = None; continue
        lm = LINE_RE.match(s)
        if not lm or section is None:
            continue
        desc = lm.group(5).strip()
        cat = next((c for c in CATEGORIES if desc.endswith(c)), None)
        if cat:
            desc = desc[: -len(cat)].strip()
        lines.append({
            "statement_date": p_end, "section": section,
            "trans_date": to_date(lm.group(1), lm.group(2)), "post_date": to_date(lm.group(3), lm.group(4)),
            "description": desc, "category": cat, "amount": _money(lm.group(6)),
        })

    # ---- checks: nothing from a statement that doesn't add up is used
    problems = []
    calc_total = round(st["previous"] - st["payments"] - st["other_credits"] + st["purchases"]
                       + st["cash_advances"] + st["interest"] + st["fees"], 2)
    if abs(calc_total - st["total"]) > 0.005:
        problems.append(f"summary: computed total {calc_total:,.2f} vs printed {st['total']:,.2f}")
    pay = round(sum(l["amount"] for l in lines if l["section"] == "payment"), 2)
    if abs(pay - st["payments"]) > 0.005:
        problems.append(f"payment lines {pay:,.2f} vs Payments {st['payments']:,.2f}")
    chg = round(sum(l["amount"] for l in lines if l["section"] == "charge"), 2)
    exp = round(st["purchases"] - st["other_credits"] + st["cash_advances"] + st["interest"] + st["fees"], 2)
    if abs(chg - exp) > 0.005:
        problems.append(f"charge/credit lines {chg:,.2f} vs purchases - credits + interest + fees {exp:,.2f}")
    bad_dates = [l for l in lines if not (p_start - timedelta(days=7) <= l["post_date"] <= p_end)]
    if bad_dates:
        problems.append(f"{len(bad_dates)} line(s) posted outside the period")
    st["own_charges"] = exp                      # this cycle's new charges net of credits
    st["valid"] = not problems
    st["problems"] = "; ".join(problems)
    st["lines"] = len(lines)
    return st, lines


def read_statements(folders):
    by_date, errors = {}, []
    for folder in folders:
        for f in sorted(glob.glob(os.path.join(folder, "*.pdf"))):
            try:
                st, lines = parse_cibc(f)
            except CibcParseError as e:
                if "not a CIBC" not in str(e):
                    errors.append((f, str(e)))
                continue
            key = (st["card"], st["statement_date"])
            if key in by_date:                  # same statement saved twice - keep the valid / first copy
                if by_date[key][0]["valid"] or not st["valid"]:
                    continue
            by_date[key] = (st, lines)
    stmts = [v[0] for v in sorted(by_date.values(), key=lambda v: v[0]["statement_date"])]
    lines = [l for v in sorted(by_date.values(), key=lambda v: v[0]["statement_date"]) for l in v[1]]
    return stmts, lines, errors


def load_qb_lines(engine):
    from sqlalchemy import text
    sql = text("""
        SELECT jel.LineID, je.TxnType, je.TxnDate, je.RefNum, je.Name, je.Memo, jel.Amount
        FROM qb.JournalEntryLines jel
        JOIN qb.JournalEntries je   ON je.JournalEntryID = jel.JournalEntryID
        JOIN qb.ChartOfAccounts coa ON coa.ChartAccountID = jel.ChartAccountID
        JOIN qb.Companies c         ON c.CompanyID = je.CompanyID
        WHERE c.CompanyCode = 'dntl' AND coa.AccountNumber = '22200'
          AND je.TxnDate BETWEEN :d1 AND :d2
        ORDER BY je.TxnDate, jel.LineID""")
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"d1": QB_FROM, "d2": QB_TO})
    df["TxnDate"] = pd.to_datetime(df["TxnDate"]).dt.date
    df["Amount"] = df["Amount"].astype(float)
    return df


def vendor_key(desc):
    w = re.sub(r"[^A-Z0-9' ]", " ", desc.upper()).split()
    return w[0] if w else ""


def match(lines, qb):
    """Card charge +x  <->  QB credit card charge -x (QB stores credits to 22200 as negative)."""
    used = set()
    qb_rows = qb.to_dict("records")
    for l in lines:
        l.update(tag=None, qb_line=None, qb_date=None, qb_type=None, qb_name=None, qb_memo=None, days_apart=None)
        if l["section"] == "payment":
            l["tag"] = "Payment"
            continue
        want = round(-l["amount"], 2)
        best = None
        for r in qb_rows:
            if r["LineID"] in used or abs(r["Amount"] - want) > 0.005:
                continue
            gap = min(abs((r["TxnDate"] - l["trans_date"]).days), abs((r["TxnDate"] - l["post_date"]).days))
            if gap <= MATCH_DAYS and (best is None or gap < best[0]):
                best = (gap, r)
        if best:
            r = best[1]
            used.add(r["LineID"])
            l.update(tag="Matched to QB", qb_line=r["LineID"], qb_date=r["TxnDate"], qb_type=r["TxnType"],
                     qb_name=r["Name"], qb_memo=r["Memo"], days_apart=best[0])

    dental_vendors = {vendor_key(l["description"]) for l in lines if l["tag"] == "Matched to QB"}
    for l in lines:
        if l["tag"]:
            continue
        d = l["description"].upper()
        if d.startswith(("REGULAR PURCHASES", "CASH ADVANCES ", "INTEREST", "CREDIT ADJUSTMENT", "ANNUAL FEE")):
            l["tag"] = "Interest / adjustment"
            continue
        k = vendor_key(l["description"])
        if k and k in dental_vendors:
            l["tag"] = "Likely Dental (vendor)"
            l["note"] = "vendor used by both practice and household - check" if k in MIXED_VENDORS else None
        else:
            l["tag"] = "Other"
    return used


def monthly(stmts, lines):
    out = []
    for st in stmts:
        ls = [l for l in lines if l["statement_date"] == st["statement_date"]]
        def tot(tag):
            return round(sum(l["amount"] for l in ls if l["tag"] == tag), 2)
        out.append({
            "Statement date": st["statement_date"], "Valid": st["valid"],
            "Previous balance": st["previous"], "Payments": st["payments"], "New balance": st["total"],
            "Own charges (all)": st["own_charges"],
            "Dental - matched to QB": tot("Matched to QB"),
            "Likely Dental (vendor) - review": tot("Likely Dental (vendor)"),
            "Interest / adjustment": tot("Interest / adjustment"),
            "Other (personal / unknown)": tot("Other"),
            "Paid twice?": "yes" if st["previous"] > 0 and abs(st["payments"] - 2 * st["previous"]) < 0.01 else "",
        })
    return pd.DataFrame(out)


def write_excel(path, stmts, lines, qb, used, errors):
    from openpyxl.styles import Font, PatternFill
    st_df = pd.DataFrame([{k: v for k, v in s.items()} for s in stmts])
    ln_df = pd.DataFrame(lines)
    qb_un = qb[~qb["LineID"].isin(used)].copy() if qb is not None else pd.DataFrame()
    if not qb_un.empty:
        qb_un["Kind"] = qb_un["TxnType"].map(lambda t: "card charge/credit" if "Credit Card" in str(t) else "payment / journal")
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        readme = pd.DataFrame({"Read me": [
            "One-off CIBC Aeroplan Visa (5451) review for FY2022-FY2023 - quality control only.",
            "Nothing was written to QuickBooks or to the regular card tables.",
            f"Matched to QB = same amount to the cent within {MATCH_DAYS} days of a Dental QuickBooks 22200 line (each QB line used once).",
            "Likely Dental (vendor) = not in QuickBooks, vendor matched QB elsewhere on this card. Decide each one.",
            "Other = personal / unknown - listed only, not used.",
            "Payment = paid to the card; the description shows the source (e.g. SCOTIABANK PAYMENT).",
            "'QB not on CIBC' lists Dental's 22200 lines with no CIBC match - TD card lines (from Feb 2023), split cheques, journals, or charges not on this card.",
            f"Statements read: {len(stmts)}; failed checks: {sum(not s['valid'] for s in stmts)}; unreadable files: {len(errors)}",
        ]})
        readme.to_excel(xw, sheet_name="Read me", index=False)
        monthly(stmts, lines).to_excel(xw, sheet_name="By statement", index=False)
        st_df.to_excel(xw, sheet_name="Statements", index=False)
        ln_df.to_excel(xw, sheet_name="Card lines", index=False)
        if not qb_un.empty:
            qb_un.to_excel(xw, sheet_name="QB not on CIBC", index=False)
        if errors:
            pd.DataFrame(errors, columns=["file", "problem"]).to_excel(xw, sheet_name="Unreadable", index=False)
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(name="Arial", bold=True, color="FFFFFF")
                c.fill = PatternFill("solid", start_color="1F4E78")
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 70)
            ws.freeze_panes = "A2"


def save_db(engine, lines):
    df = pd.DataFrame(lines)
    df.to_sql("CibcLines", engine, schema="fin", if_exists="replace", index=False)
    return len(df)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folders", nargs="+", default=DEFAULT_FOLDERS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-db", action="store_true", help="read and check the PDFs only (no QuickBooks matching)")
    ap.add_argument("--save-db", action="store_true", help="also write the tagged lines to fin.CibcLines")
    args = ap.parse_args()

    stmts, lines, errors = read_statements(args.folders)
    print(f"CIBC statements read: {len(stmts)}  ({stmts[0]['statement_date']} .. {stmts[-1]['statement_date']})" if stmts else "No CIBC statements found")
    for s in stmts:
        flag = "OK " if s["valid"] else "BAD"
        print(f"  {flag} {s['statement_date']}  prev {s['previous']:>10,.2f}  paid {s['payments']:>10,.2f}  "
              f"own charges {s['own_charges']:>10,.2f}  new {s['total']:>10,.2f}  {s['lines']:>3} lines  {s['problems']}")
    for f, e in errors:
        print(f"  unreadable: {os.path.basename(f)}: {e}")
    bad = [s for s in stmts if not s["valid"]]
    if bad:
        print(f"\n{len(bad)} statement(s) failed their checks - their lines are kept but tagged; fix before relying on them.")
        bad_dates = {s["statement_date"] for s in bad}
        for l in lines:
            if l["statement_date"] in bad_dates:
                l["statement_failed_checks"] = True

    qb, used = None, set()
    if args.no_db:
        for l in lines:
            l["tag"] = "Payment" if l["section"] == "payment" else "(not matched - --no-db)"
    else:
        sys.path.insert(0, DATA_LOAD_REPO)
        from sql_helper import get_engine        # reads the DB_* settings from the data-load project's .env
        engine = get_engine()
        qb = load_qb_lines(engine)
        used = match(lines, qb)
        counts = pd.Series([l["tag"] for l in lines]).value_counts()
        print("\nCard lines by tag:")
        for tag, n in counts.items():
            amt = sum(l["amount"] for l in lines if l["tag"] == tag)
            print(f"  {tag:<26} {n:>4} lines  {amt:>12,.2f}")
        print(f"QB 22200 lines {QB_FROM}..{QB_TO}: {len(qb)}, matched to CIBC: {len(used)}")
        if args.save_db:
            print(f"Saved {save_db(engine, lines)} lines to fin.CibcLines")

    write_excel(args.out, stmts, lines, qb, used, errors)
    print(f"\nReview workbook: {args.out}")


if __name__ == "__main__":
    main()
