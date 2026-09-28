"""
bank_validator.py

Proves a bank export is complete and correct for one fiscal year, then
cuts it into monthly periods. Pure logic - no database.

Checks
  balances          every line's running balance = previous balance + this amount,
                    wherever a file has the bank's Balance column. Without it,
                    balances are computed from the earliest known balance.
  known balances    the export agrees with balances from real statements:
                    monthly PDF statements (opening + closing), a top-up export's
                    own balances, and 'anchors' in BANK_ACCOUNTS. Each one pins the
                    running total, so a missing or wrong line shows up in the
                    statement period that contains it. Balances dated after the
                    export ends are skipped, not failed.
  coverage_start    the export starts at the beginning of the fiscal year
  coverage_end      ...and runs to the end of it (a few days' slack for
                    weekends / holidays). --allow-partial accepts a short year.
  debit_credit      'Debit' lines are negative, 'Credit' lines positive
  allocations       your split columns add up to the cheque (warning only)

Money is compared in whole cents.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from bank_models import BankExport, BankTxn
from statement_validator import Check, ValidationResult

EDGE_SLACK_DAYS = 4       # a year can start on a long weekend / end on a weekend


def _c(x) -> int:
    return int(round(float(x) * 100))


@dataclass
class BankPeriod:
    period_start: date
    period_end: date
    opening: float
    closing: float
    transactions: list[BankTxn]

    @property
    def deposits(self) -> float:
        return round(sum(t.amount for t in self.transactions if t.amount > 0), 2)

    @property
    def withdrawals(self) -> float:
        return round(-sum(t.amount for t in self.transactions if t.amount < 0), 2)


@dataclass
class BankValidation:
    result: ValidationResult
    periods: list[BankPeriod]
    fy_opening: Optional[float]
    fy_closing: Optional[float]
    in_year: int
    outside_year: int
    partial: bool


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def validate_bank_export(exp: BankExport, fy_start: date, fy_end: date,
                         anchors: list[tuple[date, float]], allow_partial: bool = False) -> BankValidation:
    checks: list[Check] = []
    txns = exp.transactions
    coverage_end = max(exp.last_date, exp.filter_to or exp.last_date)
    balances: Optional[list[float]] = None
    opening: Optional[float] = None
    derived_from = None

    # Known balances: config anchors + statement PDFs + top-up exports. (date, balance at end of day, where from)
    known = [(a[0], a[1], a[2] if len(a) > 2 else "configured statement balance") for a in anchors] + list(exp.anchors)
    known = sorted({(d, round(b, 2), lbl) for d, b, lbl in known}, key=lambda a: (a[0], a[2]))

    # ---- 1. Running balances ------------------------------------------
    if exp.has_bank_balances:
        opening = round(txns[0].balance - txns[0].amount, 2)
        balances = [t.balance for t in txns]
    else:
        usable = [a for a in known if a[0] <= coverage_end]
        if usable:
            derived_from = usable[0]
            a_date, a_bal, a_lbl = derived_from
            opening = round(a_bal - sum(t.amount for t in txns if t.txn_date <= a_date), 2)
            running, balances = opening, []
            for t in txns:
                running = round(running + t.amount, 2)
                balances.append(running)

    # the bank's own running balance, wherever a file has one (checked within each file)
    pairs = [(p, t) for p, t in zip(txns, txns[1:])
             if p.balance is not None and t.balance is not None and p.source_file == t.source_file]
    breaks = [t for p, t in pairs if _c(p.balance) + _c(t.amount) != _c(t.balance)]
    if breaks:
        b = breaks[0]
        checks.append(Check("balances", False, f"{len(breaks)} break(s) in the bank's running balance, first at "
                                               f"{b.txn_date} ({b.source_file} row {b.source_row}) - rows missing, doubled or altered"))
    elif exp.has_bank_balances:
        checks.append(Check("balances", True, f"all {len(txns)} running balances chain "
                                              f"({opening:,.2f} -> {txns[-1].balance:,.2f})"))
    elif balances is not None:
        later = [a[0] for a in known if a[0] > derived_from[0] and a[0] <= coverage_end]
        if not later:
            strength = "NOT verified - add monthly statement PDFs or a top-up export with balances"
        else:
            before = sum(t.txn_date <= derived_from[0] for t in txns)
            after = sum(t.txn_date > max(later) for t in txns)
            strength = (f"lines between {derived_from[0]} and {max(later)} are checked by {len(later)} known balance(s)"
                        + (f"; {before} line(s) before and {after} after that can't be checked" if before or after else ""))
        chained = f"{len(pairs) + 1} bank balances chain; " if pairs else ""
        checks.append(Check("balances", True, f"{chained}no bank balance on {sum(t.balance is None for t in txns)} "
                                              f"lines - computed from {derived_from[2]} "
                                              f"({derived_from[0]}: {derived_from[1]:,.2f}); {strength}"))
    else:
        checks.append(Check("balances", False, "no Balance column and no known balance inside the export - add the "
                                               "monthly statement PDFs (bank\\statements) or a top-up export with balances"))

    def balance_at(d: date) -> Optional[float]:
        """Balance at the end of day d = opening + every line dated on or before d
        (independent of the order of same- or near-date lines)."""
        if balances is None:
            return None
        return round(opening + sum(t.amount for t in txns if t.txn_date <= d), 2)

    # ---- 2. Known balances (statements / anchors) ---------------------
    # Each known balance pins the running total. A difference that first
    # appears at one known balance means the problem lies between it and the
    # previous one; later balances carrying the SAME difference add nothing new.
    prev_diff, prev_date = 0, (derived_from[0] if derived_from else None)
    for a_date, a_bal, a_lbl in known:
        if balances is None:
            continue
        name = f"known balance {a_date}"
        if (a_date, a_bal, a_lbl) == derived_from:
            checks.append(Check(name, True, f"{a_lbl}: used to compute the balances", skipped=True))
        elif a_date > coverage_end:
            checks.append(Check(name, True, f"{a_lbl} {a_bal:,.2f} is after the export ends ({coverage_end}) - not checked",
                                skipped=True))
        elif a_date < exp.first_date - timedelta(days=31):
            checks.append(Check(name, True, f"{a_lbl} is well before the export starts - not checked", skipped=True))
        else:
            got = balance_at(a_date)
            diff = _c(got) - _c(a_bal)
            if diff == 0:
                checks.append(Check(name, True, f"{a_lbl} {a_bal:,.2f} vs export {got:,.2f}"))
            elif diff == prev_diff:
                checks.append(Check(name, True, f"⚠️ {a_lbl}: still off by {diff / 100:+,.2f} - the same difference "
                                                f"as before, nothing new in this period"))
            else:
                since = f"between {prev_date} and {a_date}" if prev_date else f"on or before {a_date}"
                checks.append(Check(name, False, f"{a_lbl} {a_bal:,.2f} vs export {got:,.2f}, off by "
                                                 f"{(diff - prev_diff) / 100:+,.2f} - a line is missing, extra or wrong "
                                                 f"{since}"))
            prev_diff, prev_date = diff, a_date

    # ---- 3. Fiscal-year coverage --------------------------------------
    start_gap = (exp.first_date - fy_start).days
    end_gap = (fy_end - coverage_end).days
    partial = False
    if start_gap > EDGE_SLACK_DAYS and not (exp.filter_from and exp.filter_from <= fy_start):
        partial = True
        checks.append(Check("coverage_start", allow_partial, f"export starts {exp.first_date}, {start_gap} days after "
                                                             f"the year starts ({fy_start})"
                            + (" - accepted with --allow-partial" if allow_partial else " - re-export from the year start")))
    else:
        checks.append(Check("coverage_start", True, f"starts {exp.first_date} (year starts {fy_start})"))
    if end_gap > EDGE_SLACK_DAYS:
        partial = True
        checks.append(Check("coverage_end", allow_partial, f"export ends {coverage_end}, {end_gap} days before the "
                                                           f"year ends ({fy_end})"
                            + (" - accepted with --allow-partial" if allow_partial else " - re-export to the year end")))
    else:
        checks.append(Check("coverage_end", True, f"runs to {coverage_end} (year ends {fy_end})"))

    # ---- 4. Debit / credit agrees with the sign ------------------------
    if exp.type_mismatches:
        checks.append(Check("debit_credit", False, f"{len(exp.type_mismatches)} row(s) where Debit/Credit disagrees "
                                                   f"with the amount's sign, e.g. Excel row {exp.type_mismatches[0]}"))
    elif exp.reader == "scotia":
        checks.append(Check("debit_credit", True, "every Debit is negative and every Credit positive"))

    # ---- 5. Your allocation columns add up (warning only) --------------
    with_splits = [t for t in txns if t.splits]
    if with_splits:
        off = [t for t in with_splits if abs(_c(sum(t.splits.values())) - _c(abs(t.amount))) > 2]
        detail = (f"{len(with_splits)} split cheque(s) add up" if not off else
                  f"⚠️ {len(off)} of {len(with_splits)} don't add up, e.g. cheque {off[0].cheque_number}: "
                  f"{sum(off[0].splits.values()):,.2f} vs {abs(off[0].amount):,.2f}")
        checks.append(Check("allocations", True, detail))

    result = ValidationResult(checks)

    # ---- Monthly periods inside the fiscal year -----------------------
    periods: list[BankPeriod] = []
    in_year = [t for t in txns if fy_start <= t.txn_date <= fy_end]
    if balances is not None:
        first_covered = max(fy_start, exp.first_date - timedelta(days=EDGE_SLACK_DAYS))
        m = date(fy_start.year, fy_start.month, 1)
        while m <= fy_end:
            p_end = min(_month_end(m), fy_end)
            if p_end >= first_covered and m <= coverage_end:
                p_txns = [t for t in in_year if m <= t.txn_date <= p_end]
                periods.append(BankPeriod(m, p_end, balance_at(m - timedelta(days=1)), balance_at(p_end), p_txns))
            m = p_end + timedelta(days=1)

    return BankValidation(result=result, periods=periods,
                          fy_opening=balance_at(fy_start - timedelta(days=1)) if balances else None,
                          fy_closing=balance_at(min(fy_end, coverage_end)) if balances else None,
                          in_year=len(in_year), outside_year=len(txns) - len(in_year), partial=partial)
