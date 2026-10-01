"""
rollforward.py

Two year-end calculations for the TD Visa, pure logic (evidence comes from split_sql.py).

1. Roll-forward of QB account 22200 against the real card balance
   On the day before the year starts and on its last day:
       QB owed    = QuickBooks' balance on 22200, turned into "amount owed on the card"
       Card owed  = the card's real balance that day (statement opening + lines posted up to the day)
       Difference = QB owed - Card owed
   The change in the difference over the year is itemized: every QB line on 22200 and
   every card line posted in the year either matches (and cancels) or is listed, grouped
   by what it is (split cheques and hygiene journals booked as payments, real payments,
   card charges missing from QB, QB entries with no card line, year-end timing).
   Opening difference + items = closing difference; any gap is shown as "unexplained".

2. Cut-off
   The statement that spans the year-end has charges on both sides of it. Charges posted
   up to the year-end are FY expenses, booked 100% in Dental when they happen; Hygiene's
   share only comes off with the next month's split, in the NEXT year. The same happens
   in reverse at the start of the year. Share = (charges - lab fees) x 20%; lab 100% Dental.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from qb_matcher import QB_SIGN          # QB amount = QB_SIGN x card amount (credit card = -1)
from split_reader import VISA_DPC_RATE

PAYMENT_TYPES = {"payment"}
CHARGE_TYPES = {"purchase", "interest", "fee", "cash_advance"}


def _c(x) -> int:
    return int(round(float(x) * 100))


# ---------------------------------------------------------------------
# Card balance on a given day
# ---------------------------------------------------------------------
def card_balance_at(cycles, d: date) -> Optional[float]:
    """The card's balance at the end of day d, from the statement whose period contains d."""
    for c in cycles:
        start = c.period_start or (c.statement_date - timedelta(days=31))
        if start <= d <= c.statement_date:
            return round(c.opening + sum(l.amount for l in c.lines if l.posting_date <= d), 2)
    return None


def _card_lines_in(cycles, lo: date, hi: date):
    seen, out = set(), []
    for c in cycles:
        for l in c.lines:
            if lo <= l.posting_date <= hi and l.txn_id not in seen:
                seen.add(l.txn_id)
                out.append(l)
    return out


# ---------------------------------------------------------------------
# 1. Roll-forward
# ---------------------------------------------------------------------
@dataclass
class RFItem:
    group: str
    side: str            # "QB" | "Card"
    txn_date: date
    description: str
    amount: float        # as recorded on its own side
    effect: float        # effect on the difference (QB owed - card owed)
    note: str = ""


@dataclass
class RollForward:
    open_date: date
    close_date: date
    qb_open: Optional[float] = None       # QB owed
    qb_close: Optional[float] = None
    card_open: Optional[float] = None
    card_close: Optional[float] = None
    items: list[RFItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def diff_open(self):
        return None if self.qb_open is None or self.card_open is None else round(self.qb_open - self.card_open, 2)

    @property
    def diff_close(self):
        return None if self.qb_close is None or self.card_close is None else round(self.qb_close - self.card_close, 2)

    @property
    def explained(self) -> float:
        return round(sum(i.effect for i in self.items), 2)

    @property
    def unexplained(self) -> Optional[float]:
        if self.diff_open is None or self.diff_close is None:
            return None
        return round(self.diff_close - self.diff_open - self.explained, 2)

    def groups(self) -> list[tuple[str, float, int]]:
        order, totals, counts = [], {}, {}
        for i in self.items:
            if i.group not in totals:
                order.append(i.group)
                totals[i.group], counts[i.group] = 0.0, 0
            totals[i.group] += i.effect
            counts[i.group] += 1
        return [(g, round(totals[g], 2), counts[g]) for g in order]


def _qb_group(txn_type: str) -> str:
    t = (txn_type or "").lower()
    if "journal" in t:
        return "1. Hygiene-supplies journals booked as payments (QB)"
    if "cheque" in t or "check" in t or "bill pmt" in t:
        return "2. Split cheques - visa portion booked as payment (QB)"
    if "credit card" in t:
        return "5. QB card entries with no matching card line"
    return "6. Other QB entries on 22200"


def _card_group(line) -> str:
    if line.txn_type in PAYMENT_TYPES:
        return ("3. Payments actually made to the card (bank)" if "SCOTIABANK" in line.description.upper()
                else "4. Other payment credits on the card (not from the group's bank)")
    if line.txn_type == "refund":
        return "7. Card refunds not in QB"
    return "8. Card charges not in QB"


def build_rollforward(fy_start: date, fy_end: date, cycles, qb_lines_22200, qb_balances: Optional[tuple],
                      matches: dict) -> RollForward:
    """
    qb_lines_22200: QB lines on the card account (any dates); qb_balances: (opening, closing)
    of 22200 for the year from qb.AccountBalances; matches: card TransactionID -> QB LineID.
    """
    rf = RollForward(open_date=fy_start - timedelta(days=1), close_date=fy_end)
    rf.card_open = card_balance_at(cycles, rf.open_date)
    rf.card_close = card_balance_at(cycles, rf.close_date)
    if rf.card_open is None or rf.card_close is None:
        rf.notes.append("card statements don't cover both year-end dates - load the statements that span them")

    qb_in = [q for q in qb_lines_22200 if fy_start <= q.txn_date <= fy_end]
    if qb_balances:
        rf.qb_open = round(QB_SIGN * qb_balances[0], 2)
        rf.qb_close = round(QB_SIGN * qb_balances[1], 2)
        movement = round(sum(q.amount for q in qb_in), 2)
        if _c(qb_balances[0] + movement) != _c(qb_balances[1]):
            rf.notes.append(f"QB's 22200 lines for the year ({movement:,.2f}) don't take its opening "
                            f"{qb_balances[0]:,.2f} to its closing {qb_balances[1]:,.2f} - reload QuickBooks")
    else:
        rf.notes.append("no QB opening/closing balance for 22200 this year - reload QuickBooks with the "
                        "updated qb_loader.py (it now stores them)")

    card_in = _card_lines_in(cycles, fy_start, fy_end)
    qb_by_id = {q.line_id: q for q in qb_lines_22200}
    card_by_line = {v: k for k, v in matches.items()}
    card_all = {l.txn_id: l for c in cycles for l in c.lines}

    for l in card_in:
        qid = matches.get(l.txn_id)
        q = qb_by_id.get(qid) if qid else None
        if q is not None and fy_start <= q.txn_date <= fy_end:
            continue                                            # matched inside the year: cancels
        if q is not None:
            rf.items.append(RFItem("9. Matched across the year-end (timing)", "Card", l.posting_date, l.description,
                                   l.amount, -l.amount, f"QB dated {q.txn_date}"))
        else:
            rf.items.append(RFItem(_card_group(l), "Card", l.posting_date, l.description, l.amount, -l.amount))
    for q in qb_in:
        tid = card_by_line.get(q.line_id)
        l = card_all.get(tid) if tid else None
        if l is not None and fy_start <= l.posting_date <= fy_end:
            continue
        label = " ".join(str(x) for x in (q.txn_type, q.ref_num, q.memo) if x)
        if l is not None:
            rf.items.append(RFItem("9. Matched across the year-end (timing)", "QB", q.txn_date, label, q.amount,
                                   round(QB_SIGN * q.amount, 2), f"card posted {l.posting_date}"))
        else:
            rf.items.append(RFItem(_qb_group(q.txn_type), "QB", q.txn_date, label, q.amount,
                                   round(QB_SIGN * q.amount, 2)))

    # Flag QB entries that look like a card charge entered with the wrong sign or amount
    qb_items = [i for i in rf.items if i.group.startswith("5.")]
    card_items = [i for i in rf.items if i.group.startswith("8.")]
    for qi in qb_items:
        for ci in card_items:
            if abs(abs(qi.amount) - abs(ci.amount)) <= 1.0 and abs((qi.txn_date - ci.txn_date).days) <= 7:
                qi.note = ci.note = (f"probably the same charge: card {ci.amount:,.2f} on {ci.txn_date}, "
                                     f"QB {qi.amount:,.2f} on {qi.txn_date} - check sign / amount")
    rf.items.sort(key=lambda i: (i.group, i.txn_date))
    return rf


# ---------------------------------------------------------------------
# 2. Cut-off
# ---------------------------------------------------------------------
def lab_keywords(months) -> set[str]:
    """Vendor names of the lab fees listed in the split workbook, e.g. 'BALANCED ARCH', 'TULSA DENTAL'."""
    words = set()
    for m in months:
        for item in m.lab_items:
            text = re.sub(r"^(\s*[A-Z]{3}\s*\d{1,2}\s*){1,2}", "", item.description.upper())
            text = re.sub(r"\$[\d,.]+", "", text)
            toks = [t for t in re.split(r"[^A-Z0-9\-]+", text) if t]
            if toks:
                words.add(" ".join(toks[:2]))
    return words


@dataclass
class CutOff:
    label: str
    statement_date: Optional[date]
    window: tuple
    charges: float = 0.0
    lab: float = 0.0
    lines: int = 0

    @property
    def hygiene_share(self) -> float:
        return round((self.charges - self.lab) * (1 - VISA_DPC_RATE), 2)


def _cutoff(cycles, lo_fn, d: date, label: str, keywords: set[str]) -> Optional[CutOff]:
    for c in cycles:
        start = c.period_start or (c.statement_date - timedelta(days=31))
        if start <= d < c.statement_date:
            lo, hi = lo_fn(start, d)
            lines = [l for l in c.lines if lo <= l.posting_date <= hi and l.txn_type not in PAYMENT_TYPES]
            co = CutOff(label, c.statement_date, (lo, hi), lines=len(lines),
                        charges=round(sum(l.amount for l in lines), 2))
            co.lab = round(sum(l.amount for l in lines if l.amount > 0
                               and any(k in l.description.upper() for k in keywords)), 2)
            return co
    return None


def build_cutoffs(cycles, fy_start: date, fy_end: date, keywords: set[str]) -> list[CutOff]:
    """Opening: prior-year charges split in this year. Closing: this year's charges split next year."""
    out = []
    opening = _cutoff(cycles, lambda start, d: (start, d), fy_start - timedelta(days=1),
                      f"Opening: charges up to {fy_start - timedelta(days=1)}, split in this year", keywords)
    closing = _cutoff(cycles, lambda start, d: (start, d), fy_end,
                      f"Closing: charges up to {fy_end}, split next year", keywords)
    for co in (opening, closing):
        if co:
            out.append(co)
    return out
