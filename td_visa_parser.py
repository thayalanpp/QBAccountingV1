"""
td_visa_parser.py

Reads a TD Aeroplan Visa PDF statement with plain Python - no AI.

Why by position: page 1 has two columns (transactions on the left, the
payment / "Calculating Your Balance" boxes on the right). Text-flattening
tools merge them line by line, which is what confused the LLM. Here the
left and right halves are read separately, so they can't mix.

Rules learned from real statements (Dec 2024, Jan 2025, Apr 2025):
  - Dates print without a year ("MAR 10"). The year comes from the
    statement period, so a Dec->Jan statement dates both sides correctly.
  - The transaction date can be a day BEFORE the period starts; the
    posting date is always inside it.
  - Descriptions can wrap: a city, a phone number, the end of a URL, or
    "FOREIGN CURRENCY 216.96 USD" / "@ EXCHANGE RATE 1.47446".
  - Safety rule: a wrapped line that contains a dollar amount means a
    transaction line wasn't recognized - that's an error, never a silent skip.

Raises TDVisaParseError when the PDF isn't a TD Visa statement or a
line can't be read; the orchestrator then routes it to the fallback.
"""

import os
import re
from datetime import date, datetime

import pdfplumber

from visa_models import CardStatement, StatementSummary, Transaction, TxnType


class TDVisaParseError(Exception):
    pass


# Page is 612pt wide; transaction table ends ~345pt, right-hand boxes start ~360pt.
COLUMN_SPLIT_X = 350
X_TOLERANCE = 1   # tight word spacing - the default glues words together on these PDFs

MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
AMOUNT = r"-?\$[\d,]+\.\d{2}"

TXN_LINE = re.compile(
    rf"^([A-Z]{{3}})\s*(\d{{1,2}})\s+([A-Z]{{3}})\s*(\d{{1,2}})\s+(.+?)\s+({AMOUNT})"
    rf"(?:\s+TOTAL NEW BALANCE.*)?$"        # last line of a page can share a line with the total
)
TABLE_START = re.compile(r"^(PREVIOUS STATEMENT BALANCE|DATE DATE)")
TABLE_END = re.compile(r"^(Continued|TOTAL NEW BALANCE|TD MESSAGE CENTRE|NET AMOUNT OF MONTHLY)")   # the last: older statements' subtotal
FX_AMOUNT = re.compile(r"^FOREIGN CURRENCY\s+([\d,]+\.\d{2})\s+([A-Z]{3})(?:\s+@\s*EXCHANGE RATE\s+([\d.]+))?$")
FX_RATE = re.compile(r"^@\s*EXCHANGE RATE\s+([\d.]+)$")


def _money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def _long_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%B %d, %Y").date()


def _summary_value(right_text: str, label: str) -> float:
    m = re.search(re.escape(label) + r"\s+(" + AMOUNT + ")", right_text)
    if not m:
        raise TDVisaParseError(f"summary line '{label}' not found")
    return _money(m.group(1))


# TD words cash advances in English and French, e.g. "CASH ADV./AV. DE FONDS"
CASH_ADVANCE_WORDS = re.compile(r"CASH ADV|AV\. DE FONDS|BALANCE TRANSFER|TRANSFERT DE SOLDE|VISA CHEQUE|CHEQUE VISA")


def _classify(description: str, amount: float) -> TxnType:
    d = description.upper()
    if d.startswith(("RETAIL INTEREST", "CASH INTEREST")) or d.endswith(" INTEREST"):
        return TxnType.INTEREST
    if amount > 0 and CASH_ADVANCE_WORDS.search(d):
        return TxnType.CASH_ADVANCE
    if amount < 0:
        return TxnType.PAYMENT if "PAYMENT" in d else TxnType.REFUND
    if re.search(r"\bFEE\b", d):          # \b so "COFFEE" isn't a fee
        return TxnType.FEE
    return TxnType.PURCHASE


def parse_td_visa(pdf_path: str) -> CardStatement:
    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            raise TDVisaParseError("PDF has no pages")

        page1 = pdf.pages[0]
        page1_text = page1.extract_text(x_tolerance=X_TOLERANCE) or ""
        if "Aeroplan" not in page1_text or "STATEMENT PERIOD" not in page1_text:
            raise TDVisaParseError("not a TD Aeroplan Visa statement layout")

        # ---- Header ---------------------------------------------------
        period = re.search(r"STATEMENT PERIOD:\s*(\w+ \d{1,2}, \d{4}) to (\w+ \d{1,2}, \d{4})", page1_text)
        stmt_date = re.search(r"STATEMENT DATE:\s*(\w+ \d{1,2}, \d{4})", page1_text)
        prev_date = re.search(r"PREVIOUS STATEMENT:\s*(\w+ \d{1,2}, \d{4})", page1_text)
        last_four = re.search(r"XXXX\s*(\d{4})", page1_text)
        if not (period and stmt_date):
            raise TDVisaParseError("statement date / period not found")
        period_start, period_end = _long_date(period.group(1)), _long_date(period.group(2))

        # ---- Summary box (right half of page 1) -----------------------
        right = page1.crop((COLUMN_SPLIT_X, 0, page1.width, page1.height)).extract_text(x_tolerance=X_TOLERANCE) or ""
        summary = StatementSummary(
            previous_balance=_summary_value(right, "Previous Balance"),
            payments_and_credits=_summary_value(right, "Payments & Credits"),
            purchases=_summary_value(right, "Purchases & Other Charges"),
            cash_advances=_summary_value(right, "Cash Advances"),
            interest=_summary_value(right, "Interest"),
            fees=_summary_value(right, "Fees"),
            new_balance=_summary_value(right, "NEW BALANCE"),
        )

        def full_date(mon: str, day: str) -> date:
            month = MONTHS.get(mon)
            if month is None:
                raise TDVisaParseError(f"unknown month '{mon}'")
            year = period_end.year if month <= period_end.month else period_end.year - 1
            return date(year, month, int(day))

        # ---- Transactions (left half of every page) -------------------
        transactions: list[Transaction] = []
        for page_no, page in enumerate(pdf.pages, start=1):
            left = page.crop((0, 0, COLUMN_SPLIT_X, page.height)).extract_text(x_tolerance=X_TOLERANCE) or ""
            in_table = False
            for line in (l.strip() for l in left.splitlines()):
                if not line:
                    continue
                if TABLE_START.match(line):
                    in_table = True
                    continue
                if not in_table:
                    continue
                if TABLE_END.match(line):
                    in_table = False
                    continue

                m = TXN_LINE.match(line)
                if m:
                    amount = _money(m.group(6))
                    description = m.group(5).strip()
                    transactions.append(Transaction(
                        txn_date=full_date(m.group(1), m.group(2)),
                        posting_date=full_date(m.group(3), m.group(4)),
                        description=description,
                        amount=amount,
                        txn_type=_classify(description, amount),
                        page=page_no,
                    ))
                    continue

                # Continuation of the previous transaction's description
                if not transactions:
                    raise TDVisaParseError(f"page {page_no}: unexpected line before first transaction: {line!r}")
                last = transactions[-1]
                fx_amt, fx_rate = FX_AMOUNT.match(line), FX_RATE.match(line)
                if fx_amt:
                    last.foreign_amount = _money(fx_amt.group(1))
                    last.foreign_currency = fx_amt.group(2)
                    if fx_amt.group(3):
                        last.exchange_rate = float(fx_amt.group(3))
                elif fx_rate:
                    last.exchange_rate = float(fx_rate.group(1))
                elif re.search(AMOUNT, line):
                    raise TDVisaParseError(f"page {page_no}: unrecognized line with an amount: {line!r}")
                else:
                    last.description += " " + line

    if not transactions:
        raise TDVisaParseError("no transactions found")

    notes = []
    for txn_type, printed in ((TxnType.CASH_ADVANCE, summary.cash_advances), (TxnType.FEE, summary.fees)):
        notes += _match_printed_total(transactions, txn_type, printed)

    return CardStatement(
        source_file=os.path.basename(pdf_path),
        extractor="td_visa_parser",
        card_last_four=last_four.group(1) if last_four else None,
        statement_date=_long_date(stmt_date.group(1)),
        previous_statement_date=_long_date(prev_date.group(1)) if prev_date else None,
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        transactions=transactions,
        notes=notes,
    )


def _match_printed_total(transactions: list[Transaction], txn_type: TxnType, printed: float) -> list[str]:
    """
    TD sometimes counts a line as a cash advance (or fee) without saying so in its
    description - e.g. "LONDON VISA CUSTOMER SERV" in the Jul 2025 statement. If the
    lines of that type fall short of the printed total, look for the ONE combination
    of up to 3 purchase lines that makes up the shortfall exactly and reclassify it.
    If there's no unique answer, nothing changes and the validator flags the statement.
    """
    from itertools import combinations
    cents = lambda x: int(round(x * 100))
    shortfall = cents(printed) - cents(sum(t.amount for t in transactions if t.txn_type == txn_type))
    if shortfall <= 0:
        return []
    purchases = [t for t in transactions if t.txn_type == TxnType.PURCHASE]
    for size in (1, 2, 3):
        hits = [c for c in combinations(purchases, size) if sum(cents(t.amount) for t in c) == shortfall]
        if len(hits) == 1:
            for t in hits[0]:
                t.txn_type = txn_type
            return [f"counted as {txn_type.value} to match TD's printed total ({printed:,.2f}): "
                    + ", ".join(f"{t.posting_date} {t.description[:30]} {t.amount:,.2f}" for t in hits[0])]
        if len(hits) > 1:
            return [f"{txn_type.value} total is {shortfall / 100:,.2f} short and {len(hits)} different line "
                    f"combinations fit - left for review"]
    return []
