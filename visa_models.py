"""
visa_models.py

The one shape every credit card statement is turned into, no matter how
it was read - the Python TD parser today, an OpenAI/Gemini fallback later.
Everything downstream (validation, SQL, QuickBooks reconciliation) works
only with these classes, so it never needs to know which extractor ran.
"""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TxnType(str, Enum):
    PURCHASE = "purchase"          # a charge
    REFUND = "refund"              # negative merchant line (e.g. Air Canada reversal)
    PAYMENT = "payment"            # money paid to the card ("SCOTIABANK PAYMENT MOBILE", "PAYMENT - THANK YOU")
    INTEREST = "interest"          # "RETAIL INTEREST"
    FEE = "fee"                    # annual fee, foreign transaction fee, ...
    CASH_ADVANCE = "cash_advance"


class Transaction(BaseModel):
    txn_date: date                 # when it happened (can be a day before the period starts)
    posting_date: date             # when TD posted it (always inside the statement period)
    description: str
    amount: float                  # as printed: charges +, payments/refunds -
    txn_type: TxnType
    foreign_amount: Optional[float] = None
    foreign_currency: Optional[str] = None
    exchange_rate: Optional[float] = None
    page: int                      # 1-based page it was read from, for tracing back to the PDF


class StatementSummary(BaseModel):
    """The 'Calculating Your Balance' box, exactly as printed."""
    previous_balance: float
    payments_and_credits: float    # printed as a positive number
    purchases: float
    cash_advances: float
    interest: float
    fees: float
    new_balance: float


class CardStatement(BaseModel):
    source_file: str
    extractor: str                 # "td_visa_parser" | "openai" | "gemini"
    card_last_four: Optional[str] = None
    statement_date: date
    previous_statement_date: Optional[date] = None
    period_start: date
    period_end: date
    summary: StatementSummary
    transactions: list[Transaction] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)     # anything the reader decided that a person should know

    def total_by_type(self, txn_type: TxnType) -> float:
        return round(sum(t.amount for t in self.transactions if t.txn_type == txn_type), 2)
