"""
split_assess.py

The checks for one month of the split workbook. Pure logic - the evidence
(card cycles, bank lines, QB lines) is loaded by split_sql.py.

For each sheet (e.g. "Apr 2025"):

  workbook   C8 = the sum of the DPC portions; each C / B rate is the usual one;
             C6 follows (B6 - D6) x 0.8 + D6; D6 = the listed lab fees.
  link       the DNTL split cheque (= C8) -> the Hygiene deposit of that cheque
             -> the TD Visa statement dated in the sheet's month.
  cash       B6 ("visa payment due") is booked in QB as PAID on 22200 (cheque visa
             portion C6 + "hygiene supplies" journal B6 - C6). Compare it with what
             Hygiene actually paid TD after that statement, before the next one.
             A missed or partial payment shows here.
  expense    The split should apply to the cycle's OWN charges, not a balance that
             still holds last month's unpaid amount:
                 base = new balance - previous balance + real payments in the cycle
             (a "real" payment is one that left a group bank account; other card
             credits, e.g. points, reduce the base). Lab fees are taken at 100%
             only when the charge is found on this cycle's statement.
                 correct DPC = (base - lab) x 0.8 + lab
  qb         the split cheque in QB: which Dental account each portion was posted
             to; the "hygiene supplies" journal on 22200 (= B6 - C6).

Nothing is changed - differences become proposed adjustments and findings.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from split_reader import USUAL_RATES, VISA_DPC_RATE, SplitMonth
from split_sql import BankLine, CardCycle, QBLine, ref_digits

CHEQUE_SEARCH_DAYS = 45      # the split cheque is written up to ~6 weeks after the month
DEPOSIT_SEARCH_DAYS = 7
PAYMENT_MATCH_DAYS = 5       # card payment line vs bank withdrawal
LAB_LOOKBACK_DAYS = 75
JOURNAL_SEARCH_DAYS = 45
QB_ROUNDING_CENTS = 2        # the workbook keeps 3 decimals; QB lines are rounded so the cheque adds up


def _c(x) -> int:
    return int(round(float(x) * 100))


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1)


@dataclass
class MonthResult:
    month: SplitMonth
    cheque_number: Optional[str] = None
    cheque_date: Optional[date] = None
    deposit_date: Optional[date] = None
    statement_date: Optional[date] = None
    bank_paid: Optional[float] = None
    cash_diff: Optional[float] = None
    expense_base: Optional[float] = None
    lab_on_card: Optional[float] = None
    correct_dpc: Optional[float] = None
    correct_hygiene: Optional[float] = None
    dpc_diff: Optional[float] = None
    qb_cheque_found: Optional[bool] = None
    qb_journal_found: Optional[bool] = None
    qb_postings: dict = field(default_factory=dict)      # category -> (account label, amount)
    lab_matches: dict = field(default_factory=dict)      # lab item row -> (card txn id, card date)
    status: str = "Checked"
    findings: list[dict] = field(default_factory=list)

    def add(self, area, severity, message, amount=None, company="dntl"):
        self.findings.append({"subject": f"Split {self.month.sheet}", "area": area, "severity": severity,
                              "message": message, "amount": amount, "company": company, "result": self})


@dataclass
class Evidence:
    cycles: list[CardCycle]
    dntl_bank: list[BankLine]
    hyg_bank: list[BankLine]
    all_withdrawals: list[BankLine]
    qb_dntl: list[QBLine]
    dntl_bank_qb_account: str        # e.g. "10000"
    card_qb_account: str             # e.g. "22200"
    visa_payment_pattern: str        # how Hygiene's payments to TD look in its bank, e.g. "td visa"


def check_workbook(m: SplitMonth, prev: Optional[SplitMonth], r: MonthResult):
    for n in m.notes:
        r.add("workbook", "Warning", n)
    total = sum(l.dpc_portion for l in m.lines) - m.other_deduction
    if _c(total) != _c(m.cheque_total):
        r.add("workbook", "Issue", f"C8 {m.cheque_total:,.2f} is not the sum of the DPC portions ({total:,.2f})",
              round(m.cheque_total - total, 2))
    for l in m.lines:
        if l.category in USUAL_RATES and l.total:
            if not any(abs(l.rate - u) < 0.0005 for u in USUAL_RATES[l.category]):
                r.add("rate", "Warning", f"{l.label.strip()}: DPC portion {l.dpc_portion:,.2f} is {l.rate:.1%} of "
                      f"{l.total:,.2f} (usual {' / '.join(f'{u:.0%}' for u in USUAL_RATES[l.category])}); formula {l.formula}")
            if prev and prev.line(l.category) and prev.line(l.category).rate and l.rate \
                    and abs(prev.line(l.category).rate - l.rate) >= 0.0005:
                r.add("rate", "Info", f"{l.label.strip()}: rate changed from {prev.line(l.category).rate:.0%} "
                      f"({prev.sheet}) to {l.rate:.0%}")
    expected_c6 = (m.visa_due - m.lab_fees) * VISA_DPC_RATE + m.lab_fees
    if _c(expected_c6) != _c(m.visa_dpc):
        r.add("workbook", "Warning", f"visa DPC portion {m.visa_dpc:,.2f} doesn't follow (B6 - D6) x 0.8 + D6 = "
              f"{expected_c6:,.2f}")
    listed = round(sum(i.amount for i in m.lab_items), 2)
    if _c(listed) != _c(m.lab_fees):
        r.add("lab", "Warning", f"D6 lab fees {m.lab_fees:,.2f} differ from the listed lab charges {listed:,.2f}")


def link_cheque(m: SplitMonth, ev: Evidence, r: MonthResult):
    target = -_c(m.cheque_total)
    lo, hi = m.month, _month_end(m.month) + timedelta(days=CHEQUE_SEARCH_DAYS)
    options = [b for b in ev.dntl_bank if _c(b.amount) == target and lo <= b.txn_date <= hi]
    options.sort(key=lambda b: (0 if (b.tag or "").lower() == "split" else 1, b.txn_date))
    if not options:
        r.add("link", "Issue" if ev.dntl_bank else "Warning",
              f"no Dental bank withdrawal of {m.cheque_total:,.2f} (C8) between {lo} and {hi}"
              + ("" if ev.dntl_bank else " - Dental bank not loaded"), round(m.cheque_total, 2))
        return
    chq = options[0]
    r.cheque_number, r.cheque_date = chq.cheque_number, chq.txn_date
    dep = [b for b in ev.hyg_bank if _c(b.amount) == -target
           and 0 <= (b.txn_date - chq.txn_date).days <= DEPOSIT_SEARCH_DAYS]
    if dep:
        r.deposit_date = dep[0].txn_date
    elif ev.hyg_bank:
        r.add("link", "Warning", f"cheque {chq.cheque_number} ({m.cheque_total:,.2f}, {chq.txn_date}) not found as a "
              f"deposit in Hygiene's bank within {DEPOSIT_SEARCH_DAYS} days", round(m.cheque_total, 2), company="hyg")


def _same_day_next_month(d: date) -> date:
    y, mth = d.year + (d.month == 12), d.month % 12 + 1
    return date(y, mth, min(d.day, _month_end(date(y, mth, 1)).day))


def find_cycle(m: SplitMonth, ev: Evidence) -> tuple[Optional[CardCycle], Optional[date]]:
    """The card statement dated in the sheet's month, and the date the next statement closes."""
    for i, c in enumerate(ev.cycles):
        if c.statement_date.year == m.month.year and c.statement_date.month == m.month.month:
            nxt = _same_day_next_month(c.statement_date)
            if i + 1 < len(ev.cycles) and (ev.cycles[i + 1].statement_date - c.statement_date).days <= 35:
                nxt = ev.cycles[i + 1].statement_date          # the real next statement, when loaded
            return c, nxt
    return None, None


def _same_amount_withdrawals(p, ev: Evidence) -> list[BankLine]:
    return [w for w in ev.all_withdrawals
            if _c(w.amount) == _c(p.amount) and abs((w.txn_date - p.posting_date).days) <= PAYMENT_MATCH_DAYS]


def _outside_bank_data(p, ev: Evidence) -> bool:
    """True when the payment falls before/after the paying bank's loaded data, so it can't be checked."""
    if not ev.hyg_bank:
        return True
    first, last = ev.hyg_bank[0].txn_date, ev.hyg_bank[-1].txn_date
    return p.posting_date < first or p.posting_date > last


def _is_real_payment(p, ev: Evidence) -> bool:
    """A card payment counts as paid from the group only if a bank withdrawal of the same amount,
    within a few days, is itself a payment to the card (e.g. 'Pc-Td Visa ...') - an e-transfer or
    cheque that happens to have the same amount doesn't count. A payment dated outside the loaded
    bank data (e.g. before the fiscal-year export starts) can't be checked and is assumed real."""
    if _outside_bank_data(p, ev):
        return True
    pat = ev.visa_payment_pattern.lower()
    return any(pat in f"{w.description} {w.sub_description or ''}".lower() for w in _same_amount_withdrawals(p, ev))


def cash_test(m: SplitMonth, cycle: CardCycle, next_date: date, ev: Evidence, r: MonthResult):
    if not ev.hyg_bank:
        r.add("cash", "Warning", "Hygiene's bank isn't loaded - the card payments can't be checked", company="hyg")
        return
    pat = ev.visa_payment_pattern.lower()
    paid = [b for b in ev.hyg_bank if b.amount < 0 and pat in f"{b.description} {b.sub_description or ''}".lower()
            and cycle.statement_date < b.txn_date <= next_date]
    r.bank_paid = round(-sum(b.amount for b in paid), 2)
    r.cash_diff = round(m.visa_due - r.bank_paid, 2)
    if _c(r.cash_diff):
        when = ", ".join(f"{b.txn_date} {-b.amount:,.2f}" for b in paid) or "nothing"
        r.add("cash", "Issue", f"QB books {m.visa_due:,.2f} (B6) as paid on {ev.card_qb_account} for the "
              f"{cycle.statement_date} statement, but Hygiene paid TD {when} before the next statement "
              f"({next_date}): {r.cash_diff:+,.2f} recorded as paid but not paid", r.cash_diff)


def expense_test(m: SplitMonth, cycle: CardCycle, ev: Evidence, r: MonthResult, lab_claims: dict):
    real = [p for p in cycle.payments if _is_real_payment(p, ev)]
    credits = [p for p in cycle.payments if p not in real]
    for p in real:
        if _outside_bank_data(p, ev):
            r.add("expense", "Info", f"card payment {p.posting_date} '{p.description[:30]}' {p.amount:,.2f} is outside "
                  f"the loaded bank data - assumed paid from the group", company="hyg")
    r.expense_base = round(cycle.ending - cycle.opening - sum(p.amount for p in real), 2)
    if credits:
        r.add("expense", "Info", "card credits not paid from a group bank (e.g. points) reduce the base: "
              + ", ".join(f"{p.posting_date} {p.description[:30]} {p.amount:,.2f}" for p in credits))
    for p in credits:
        for w in _same_amount_withdrawals(p, ev):
            what = f"{w.description} {w.sub_description or ''}".strip()
            r.add("expense", "Warning", f"card credit {p.posting_date} '{p.description[:30]}' {p.amount:,.2f} has the "
                  f"same amount as a bank withdrawal on {w.txn_date} ('{what}') that is not a card payment - not "
                  f"counted as paid from the group; confirm they're unrelated", round(p.amount, 2))

    # lab fees: each listed charge must be a charge on THIS cycle's statement
    lab = 0.0
    purchases = [l for l in cycle.lines if l.amount > 0]
    all_lines = [(c, l) for c in ev.cycles for l in c.lines if l.amount > 0]
    for item in m.lab_items:
        here = [l for l in purchases if _c(l.amount) == _c(item.amount) and l.txn_id not in lab_claims]
        if here:
            hit = here[0]
            lab_claims[hit.txn_id] = m.sheet
            r.lab_matches[item.row] = (hit.txn_id, hit.posting_date)
            lab += item.amount
            continue
        claimed = [l for l in purchases if _c(l.amount) == _c(item.amount) and l.txn_id in lab_claims]
        elsewhere = [(c, l) for c, l in all_lines if _c(l.amount) == _c(item.amount) and c is not cycle
                     and l.txn_id not in lab_claims
                     and 0 <= (cycle.statement_date - l.posting_date).days <= LAB_LOOKBACK_DAYS + 31]
        if claimed:
            r.add("lab", "Issue", f"lab fee {item.amount:,.2f} ({item.description[:40]}) was already counted on the "
                  f"{lab_claims[claimed[0].txn_id]} sheet - counted twice", item.amount)
        elif elsewhere:
            c, l = elsewhere[0]
            r.add("lab", "Warning", f"lab fee {item.amount:,.2f} ({item.description[:40]}) is on the "
                  f"{c.statement_date} card statement, not this one ({cycle.statement_date})", item.amount)
        else:
            r.add("lab", "Warning", f"lab fee {item.amount:,.2f} ({item.description[:40]}) not found on the loaded "
                  f"card statements", item.amount)
    r.lab_on_card = round(lab, 2)
    r.correct_dpc = round((r.expense_base - lab) * VISA_DPC_RATE + lab, 2)
    r.correct_hygiene = round(r.expense_base - r.correct_dpc, 2)
    r.dpc_diff = round(r.correct_dpc - m.visa_dpc, 2)
    if _c(r.expense_base) != _c(m.visa_due):
        r.add("expense", "Issue", f"visa split on {m.visa_due:,.2f} (B6) but the {cycle.statement_date} cycle's own "
              f"charges are {r.expense_base:,.2f} (new {cycle.ending:,.2f} - previous {cycle.opening:,.2f} + paid "
              f"{-sum(p.amount for p in real):,.2f}); Dental visa portion should be {r.correct_dpc:,.2f}, "
              f"booked {m.visa_dpc:,.2f}: {r.dpc_diff:+,.2f}", r.dpc_diff)
    elif abs(r.dpc_diff) >= 0.01:
        r.add("expense", "Warning", f"Dental visa portion should be {r.correct_dpc:,.2f} (lab fees on this statement "
              f"{lab:,.2f}), booked {m.visa_dpc:,.2f}: {r.dpc_diff:+,.2f}", r.dpc_diff)


def qb_checks(m: SplitMonth, cycle: Optional[CardCycle], ev: Evidence, r: MonthResult):
    if not ev.qb_dntl:
        r.add("qb", "Warning", "Dental QuickBooks lines aren't loaded - postings not checked")
        return
    if r.cheque_date:
        if r.cheque_number:
            entry = [q for q in ev.qb_dntl if ref_digits(q.ref_num) == r.cheque_number
                     and abs((q.txn_date - r.cheque_date).days) <= 60]
            entry_ids = {q.entry_id for q in entry if q.account_number == ev.dntl_bank_qb_account
                         and _c(abs(q.amount)) == _c(m.cheque_total)} or {q.entry_id for q in entry}
        else:
            # No cheque number in the bank data (e.g. the FY2024 export): find the QB entry that takes
            # the same amount out of the Dental bank account within a few days of the bank date.
            entry_ids = {q.entry_id for q in ev.qb_dntl if q.account_number == ev.dntl_bank_qb_account
                         and q.amount < 0 and abs(_c(-q.amount) - _c(m.cheque_total)) <= QB_ROUNDING_CENTS
                         and abs((q.txn_date - r.cheque_date).days) <= 10}
        label = f"cheque {r.cheque_number}" if r.cheque_number else f"the {r.cheque_date} split cheque"
        legs = [q for q in ev.qb_dntl if q.entry_id in entry_ids and q.account_number != ev.dntl_bank_qb_account]
        r.qb_cheque_found = bool(legs)
        if not legs:
            r.add("qb", "Issue", f"{label} ({m.cheque_total:,.2f}) not found in Dental QuickBooks",
                  round(m.cheque_total, 2))
        else:
            unused = list(legs)
            for l in m.lines:
                if not _c(l.dpc_portion):
                    continue
                hit = min((q for q in unused if abs(_c(abs(q.amount)) - _c(l.dpc_portion)) <= QB_ROUNDING_CENTS),
                          key=lambda q: abs(_c(abs(q.amount)) - _c(l.dpc_portion)), default=None)
                if hit:
                    unused.remove(hit)
                    r.qb_postings[l.category] = (hit.account_label, round(abs(hit.amount), 2))
                else:
                    r.add("qb", "Warning", f"{label}: no QB line of {l.dpc_portion:,.2f} for the "
                          f"{l.label.strip()} portion", round(l.dpc_portion, 2))
            if unused:
                r.add("qb", "Info", f"{label}: other QB lines " + ", ".join(
                    f"{q.account_label} {abs(q.amount):,.2f}" for q in unused))
    if cycle and _c(m.visa_hygiene):
        gj = [q for q in ev.qb_dntl if q.account_number == ev.card_qb_account and "journal" in (q.txn_type or "").lower()
              and abs(_c(abs(q.amount)) - _c(m.visa_hygiene)) <= QB_ROUNDING_CENTS
              and abs((q.txn_date - cycle.statement_date).days) <= JOURNAL_SEARCH_DAYS]
        r.qb_journal_found = bool(gj)
        if not gj:
            r.add("qb", "Warning", f"no 'hygiene supplies' journal of {m.visa_hygiene:,.2f} (B6 - C6) on "
                  f"{ev.card_qb_account} near {cycle.statement_date}", m.visa_hygiene)


def assess_month(m: SplitMonth, prev: Optional[SplitMonth], ev: Evidence, lab_claims: dict) -> MonthResult:
    r = MonthResult(month=m)
    check_workbook(m, prev, r)
    link_cheque(m, ev, r)
    cycle, next_date = find_cycle(m, ev)
    if cycle is None:
        r.add("link", "Warning", f"no TD Visa statement dated in {m.month:%b %Y} is loaded - run visa_pipeline.py "
              f"for it; the cash and expense tests were skipped")
        r.status = "Incomplete"
    else:
        r.statement_date = cycle.statement_date
        cash_test(m, cycle, next_date, ev, r)
        if ev.hyg_bank:
            expense_test(m, cycle, ev, r, lab_claims)
        else:
            r.add("expense", "Warning", "Hygiene's bank isn't loaded - the card is paid from it, so real payments "
                  "can't be told apart from other credits; expense test skipped (run bank_pipeline.py --only hyg)",
                  company="hyg")
            r.status = "Incomplete"
    qb_checks(m, cycle, ev, r)
    if r.status != "Incomplete":
        issues = sum(f["severity"] == "Issue" for f in r.findings)
        r.status = "OK" if not issues else f"{issues} issue(s)"
    return r
