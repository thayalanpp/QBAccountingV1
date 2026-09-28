"""
bank_models.py

The one shape every bank export is turned into, whichever bank it came
from (Scotiabank Excel export, TD account-activity export, ...).
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class BankTxn(BaseModel):
    seq: int                         # 1 = oldest; the bank's own order (needed for same-day balance chains)
    txn_date: date
    description: str                 # "cheque", "insurance", "MOBILE DEPOSIT", ...
    sub_description: Optional[str] = None   # payer / payee, e.g. "Sunlife Med Ins"
    amount: float                    # + deposit, - withdrawal
    balance: Optional[float] = None  # running balance after this line (None if the export has no Balance column)
    cheque_number: Optional[str] = None
    tag: Optional[str] = None        # your own label from an extra column, e.g. "hyg", "split", "smitha"
    splits: dict[str, float] = Field(default_factory=dict)   # your allocation columns, e.g. {"visa": 9290.55, "rent": 1935.23}
    source_row: int                  # Excel row number, for tracing back to the file
    source_file: Optional[str] = None


class BankExport(BaseModel):
    source_file: str
    reader: str                      # "scotia" | "td"
    filter_from: Optional[date] = None   # date range the export was run for, when the file says so
    filter_to: Optional[date] = None
    has_bank_balances: bool          # True when the running balance came from the bank, not computed here
    transactions: list[BankTxn]
    type_mismatches: list[int] = Field(default_factory=list)   # rows where Debit/Credit disagrees with the amount's sign
    notes: list[str] = Field(default_factory=list)
    anchors: list[tuple[date, float, str]] = Field(default_factory=list)   # known balances (end of day) + where from

    @property
    def first_date(self) -> Optional[date]:
        return self.transactions[0].txn_date if self.transactions else None

    @property
    def last_date(self) -> Optional[date]:
        return self.transactions[-1].txn_date if self.transactions else None
