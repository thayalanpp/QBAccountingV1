"""
qb_matcher.py

Matches one card statement's lines to the QuickBooks lines posted to the
linked account (for the TD Visa: dntl 22200). Pure logic - no database -
so it can be tested on its own; visa_sql.py loads the inputs and saves
the result.

How a match is made
  - Same amount to the cent, with QuickBooks' sign flipped (QB_SIGN):
    a $46.24 charge on the statement is -46.24 in QB, a -$28.24 refund is +28.24.
  - QB date within MAX_DAYS_APART of the statement POSTING date. QB is
    usually entered on or a few days after posting; the window absorbs that.
  - Cheques: a statement line with a cheque number is first matched to the
    QB line with the same amount AND the same reference number, up to
    REF_MAX_DAYS apart (a cheque clears long after it's written).
  - When several QB lines qualify (e.g. two $242.45 Abeldent charges),
    the closest date wins, so duplicates pair up in date order.
  - Each QB line matches at most one statement line. Lines already matched
    to ANOTHER statement are excluded before this runs (see visa_sql.py),
    so a charge near a statement boundary can't be counted twice.

What comes out
  matched         statement line + QB line + days apart
  statement_only  on the card, not found in the books      -> missing entry?
  qb_only         in the books in this period, not on the card
                  -> intercompany entry, wrong account/period, or a payment
                     recorded differently (e.g. Cheque 58 vs the Scotiabank payment)
  variance        statement activity vs QB activity for the lines above,
                  in statement terms. It always equals
                  sum(statement_only) + sum(qb_only, converted to statement sign),
                  so every dollar of variance is listed item by item.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

QB_SIGN = -1          # QB amount = QB_SIGN * statement amount. Credit card (liability): -1. Bank account (asset): +1.
MAX_DAYS_APART = 7
REF_MAX_DAYS = 60     # a cheque can clear weeks after QB dates it; matched by cheque number within this window


def _cents(x) -> int:
    return int(round(float(x) * 100))


@dataclass
class StmtLine:
    transaction_id: Optional[int]
    txn_date: date
    posting_date: date
    description: str
    amount: float
    ref: Optional[str] = None       # cheque number, when the bank gives one


@dataclass
class QBLine:
    line_id: int
    txn_date: date
    amount: float
    txn_type: Optional[str] = None
    ref_num: Optional[str] = None
    name: Optional[str] = None
    memo: Optional[str] = None


@dataclass
class Match:
    stmt: StmtLine
    qb: QBLine
    days_apart: int     # QB date minus statement posting date


@dataclass
class MatchResult:
    matched: list[Match] = field(default_factory=list)
    statement_only: list[StmtLine] = field(default_factory=list)
    qb_only: list[QBLine] = field(default_factory=list)
    qb_sign: int = QB_SIGN

    @property
    def statement_net(self) -> float:
        lines = [m.stmt for m in self.matched] + self.statement_only
        return round(sum(l.amount for l in lines), 2)

    @property
    def qb_net(self) -> float:
        lines = [m.qb for m in self.matched] + self.qb_only
        return round(sum(l.amount for l in lines), 2)

    @property
    def variance(self) -> float:
        """Statement activity minus QB activity, both in statement sign. 0 = fully explained."""
        return round(self.statement_net - self.qb_sign * self.qb_net, 2)


def _ref_digits(ref) -> Optional[str]:
    m = re.match(r"\s*0*(\d+)", str(ref or ""))
    return m.group(1) if m else None


def match_statement(stmt_lines: list[StmtLine], qb_candidates: list[QBLine],
                    period_start: date, period_end: date,
                    max_days: int = MAX_DAYS_APART, qb_sign: int = QB_SIGN,
                    ref_max_days: int = REF_MAX_DAYS) -> MatchResult:
    """
    qb_candidates: QB lines on the linked account(s) dated in a window around
    the period (wide enough for cheques), minus any already matched to
    another statement.
    """
    result = MatchResult(qb_sign=qb_sign)
    unused = {q.line_id: q for q in qb_candidates}
    ordered = sorted(stmt_lines, key=lambda l: (l.posting_date, l.txn_date, l.transaction_id or 0))
    matched_ids = set()

    def take(s, options):
        best = min(options, key=lambda q: (abs((q.txn_date - s.posting_date).days), q.txn_date, q.line_id))
        del unused[best.line_id]
        matched_ids.add(id(s))
        result.matched.append(Match(s, best, (best.txn_date - s.posting_date).days))

    # Pass 1: cheques by number + amount
    for s in ordered:
        ref = _ref_digits(s.ref)
        if not ref:
            continue
        target = qb_sign * _cents(s.amount)
        options = [q for q in unused.values() if _cents(q.amount) == target and _ref_digits(q.ref_num) == ref
                   and abs((q.txn_date - s.posting_date).days) <= ref_max_days]
        if options:
            take(s, options)

    # Pass 2: everything else by amount + date
    for s in ordered:
        if id(s) in matched_ids:
            continue
        target = qb_sign * _cents(s.amount)
        options = [q for q in unused.values()
                   if _cents(q.amount) == target and abs((q.txn_date - s.posting_date).days) <= max_days]
        if options:
            take(s, options)
        else:
            result.statement_only.append(s)

    # QB lines left over that belong to THIS period (the look-around margin is only for matching)
    result.qb_only = sorted((q for q in unused.values() if period_start <= q.txn_date <= period_end),
                            key=lambda q: (q.txn_date, q.line_id))
    result.matched.sort(key=lambda m: (m.stmt.posting_date, m.stmt.transaction_id or 0))
    return result
