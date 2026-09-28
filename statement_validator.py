"""
statement_validator.py

Proves a CardStatement is complete and correct using only arithmetic -
no AI, no judgement. Works the same whichever extractor read the PDF
(the Python TD parser today, OpenAI/Gemini later).

Checks
  summary_math           previous - payments&credits + purchases + cash advances
                         + interest + fees = new balance   (the printed box adds up)
  lines_to_new_balance   previous + every transaction amount = new balance
                         (nothing missing, nothing extra - independent of how lines are typed)
  purchases_total        purchase lines        = printed "Purchases & Other Charges"
  payments_credits_total payment + refund lines = printed "Payments & Credits"
  interest_total         interest lines        = printed "Interest"
  fees_total             fee lines             = printed "Fees"
  cash_advances_total    cash advance lines    = printed "Cash Advances"
  posting_dates          every POSTING date is inside the statement period
                         (transaction dates may be a day earlier - that's normal)
  statement_date         statement date = last day of the period
  chain_previous         this opening balance = the previous statement's new balance
                         (skipped - not failed - when the previous one isn't in SQL yet)

All money is compared in whole cents, so floating point can't cause a false failure.
"""

from dataclasses import dataclass
from typing import Optional

from visa_models import CardStatement, TxnType


def _cents(x: float) -> int:
    return int(round(float(x) * 100))


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    skipped: bool = False

    def __str__(self):
        mark = "⏭️" if self.skipped else ("✅" if self.passed else "❌")
        return f"{mark} {self.name}: {self.detail}"


@dataclass
class ValidationResult:
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def short(self) -> str:
        if self.passed:
            skipped = sum(c.skipped for c in self.checks)
            return f"all {len(self.checks)} checks passed" + (f" ({skipped} skipped)" if skipped else "")
        return "FAILED: " + "; ".join(f"{c.name} ({c.detail})" for c in self.failures)


def _compare(name: str, actual: float, expected: float, what: str) -> Check:
    diff = _cents(actual) - _cents(expected)
    if diff == 0:
        return Check(name, True, f"{what} {expected:,.2f}")
    return Check(name, False, f"{what}: lines total {actual:,.2f}, printed {expected:,.2f}, off by {diff / 100:+,.2f}")


def validate_statement(stmt: CardStatement, previous_new_balance: Optional[float] = None,
                       previous_label: str = "previous statement") -> ValidationResult:
    s = stmt.summary
    checks: list[Check] = []

    # 1. The printed summary box adds up on its own
    calc = s.previous_balance - s.payments_and_credits + s.purchases + s.cash_advances + s.interest + s.fees
    checks.append(_compare("summary_math", calc, s.new_balance, "new balance"))

    # 2. Opening balance plus every line = new balance
    lines_total = s.previous_balance + sum(t.amount for t in stmt.transactions)
    checks.append(_compare("lines_to_new_balance", lines_total, s.new_balance, "new balance"))

    # 3-7. Each printed total, by transaction type
    checks.append(_compare("purchases_total", stmt.total_by_type(TxnType.PURCHASE), s.purchases, "purchases"))
    credits = -(stmt.total_by_type(TxnType.PAYMENT) + stmt.total_by_type(TxnType.REFUND))
    checks.append(_compare("payments_credits_total", credits, s.payments_and_credits, "payments & credits"))
    checks.append(_compare("interest_total", stmt.total_by_type(TxnType.INTEREST), s.interest, "interest"))
    checks.append(_compare("fees_total", stmt.total_by_type(TxnType.FEE), s.fees, "fees"))
    checks.append(_compare("cash_advances_total", stmt.total_by_type(TxnType.CASH_ADVANCE), s.cash_advances,
                           "cash advances"))

    # 8. Posting dates inside the period
    outside = [t for t in stmt.transactions if not (stmt.period_start <= t.posting_date <= stmt.period_end)]
    if outside:
        sample = ", ".join(f"{t.posting_date} {t.description[:25]}" for t in outside[:3])
        checks.append(Check("posting_dates", False, f"{len(outside)} posted outside "
                                                    f"{stmt.period_start}..{stmt.period_end}: {sample}"))
    else:
        checks.append(Check("posting_dates", True, f"all {len(stmt.transactions)} inside the period"))

    # 9. Statement date is the end of the period
    checks.append(Check("statement_date", stmt.statement_date == stmt.period_end,
                        f"{stmt.statement_date} vs period end {stmt.period_end}"))

    # 10. Chains to the previous statement
    if previous_new_balance is None:
        checks.append(Check("chain_previous", True, f"{previous_label} not in SQL yet - not checked", skipped=True))
    else:
        diff = _cents(s.previous_balance) - _cents(previous_new_balance)
        checks.append(Check("chain_previous", diff == 0,
                            f"opening {s.previous_balance:,.2f} vs {previous_label} new balance "
                            f"{previous_new_balance:,.2f}" + ("" if diff == 0 else f", off by {diff / 100:+,.2f}")))

    return ValidationResult(checks)
