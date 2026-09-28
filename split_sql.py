"""
split_sql.py

Database side of split_pipeline.py: loads what the checks need (card
statements, bank lines, QuickBooks entries) and saves the results.
Rerun-safe: a run replaces its own fiscal year's rows and findings.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text

from visa_sql import _as_date


@dataclass
class CardLine:
    txn_id: int
    txn_date: date
    posting_date: date
    description: str
    amount: float
    txn_type: Optional[str]


@dataclass
class CardCycle:
    statement_id: int
    statement_date: date
    opening: float
    ending: float
    lines: list[CardLine] = field(default_factory=list)

    @property
    def payments(self) -> list[CardLine]:
        return [l for l in self.lines if l.txn_type == "payment"]


@dataclass
class BankLine:
    txn_id: int
    txn_date: date
    amount: float
    description: str
    sub_description: Optional[str]
    cheque_number: Optional[str]
    tag: Optional[str]


@dataclass
class QBLine:
    line_id: int
    txn_date: date
    txn_type: Optional[str]
    ref_num: Optional[str]
    account_number: str
    account_label: str
    amount: float
    memo: Optional[str]
    entry_id: Optional[int] = None      # lines of one QB transaction share this


def _account_id(conn, name: str, account_type: str) -> Optional[int]:
    row = conn.execute(text("SELECT AccountID FROM fin.Accounts WHERE AccountName = :n AND AccountType = :t"),
                       {"n": name, "t": account_type}).fetchone()
    return row[0] if row else None


def load_card_cycles(engine, card_account_name: str) -> list[CardCycle]:
    with engine.connect() as conn:
        aid = _account_id(conn, card_account_name, "Visa")
        if aid is None:
            return []
        stmts = conn.execute(text("""SELECT StatementID, StatementDate, OpeningBalance, EndingBalance
                                     FROM fin.Statements WHERE AccountID = :a ORDER BY StatementDate"""),
                             {"a": aid}).fetchall()
        cycles = []
        for sid, sdate, opening, ending in stmts:
            rows = conn.execute(text("""SELECT TransactionID, TransactionDate, PostingDate, Description, Amount, TxnType
                                        FROM fin.Transactions WHERE StatementID = :s ORDER BY LineNumber, TransactionID"""),
                                {"s": sid}).fetchall()
            cycles.append(CardCycle(sid, _as_date(sdate), float(opening), float(ending),
                                    [CardLine(r[0], _as_date(r[1]), _as_date(r[2] or r[1]), r[3], float(r[4]), r[5])
                                     for r in rows]))
    return cycles


def load_bank_lines(engine, account_name: str) -> list[BankLine]:
    with engine.connect() as conn:
        aid = _account_id(conn, account_name, "Bank")
        if aid is None:
            return []
        rows = conn.execute(text("""SELECT TransactionID, TransactionDate, Amount, Description, SubDescription,
                                           ChequeNumber, Tag
                                    FROM fin.Transactions WHERE AccountID = :a
                                    ORDER BY TransactionDate, BankOrder"""), {"a": aid}).fetchall()
    return [BankLine(r[0], _as_date(r[1]), float(r[2]), r[3] or "", r[4], r[5], r[6]) for r in rows]


def load_all_bank_withdrawals(engine) -> list[BankLine]:
    """Every withdrawal in any of the group's bank accounts - used to tell real card payments from credits."""
    with engine.connect() as conn:
        rows = conn.execute(text("""SELECT t.TransactionID, t.TransactionDate, t.Amount, t.Description, t.SubDescription,
                                           t.ChequeNumber, t.Tag
                                    FROM fin.Transactions t JOIN fin.Accounts a ON a.AccountID = t.AccountID
                                    WHERE a.AccountType = 'Bank' AND t.Amount < 0""")).fetchall()
    return [BankLine(r[0], _as_date(r[1]), float(r[2]), r[3] or "", r[4], r[5], r[6]) for r in rows]


def load_qb_lines(engine, company_code: str, date_from: date, date_to: date) -> list[QBLine]:
    """Every QB line of the company in the window (all accounts), with its account."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT jel.LineID, je.TxnDate, je.TxnType, je.RefNum, coa.AccountNumber, coa.AccountName,
                   jel.Amount, COALESCE(jel.LineMemo, je.Memo), je.JournalEntryID
            FROM qb.JournalEntryLines jel
            JOIN qb.JournalEntries je    ON je.JournalEntryID = jel.JournalEntryID
            JOIN qb.Companies c          ON c.CompanyID = je.CompanyID
            JOIN qb.ChartOfAccounts coa  ON coa.ChartAccountID = jel.ChartAccountID
            WHERE c.CompanyCode = :co AND je.TxnDate BETWEEN :f AND :t"""),
            {"co": company_code, "f": date_from, "t": date_to}).fetchall()
    out = []
    for r in rows:
        label = r[5] if str(r[5]).startswith(str(r[4])) else f"{r[4]} · {r[5]}"
        out.append(QBLine(r[0], _as_date(r[1]), r[2], r[3], str(r[4]), label, float(r[6]), r[7], r[8]))
    return out


def ref_digits(ref) -> Optional[str]:
    m = re.match(r"\s*0*(\d+)", str(ref or ""))
    return m.group(1) if m else None


# ---------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------
def save_results(engine, fy_end: int, source_file: str, results: list, findings: list[dict]):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM fin.SplitMonths WHERE FiscalYearEnd = :fy"), {"fy": fy_end})
        conn.execute(text("DELETE FROM fin.Findings WHERE Source = 'split' AND RunKey = :rk"), {"rk": f"FY{fy_end}"})
        for r in results:
            m = r.month
            sid = conn.execute(text("""
                INSERT INTO fin.SplitMonths
                    (FiscalYearEnd, SheetName, MonthStart, SourceFile, ChequeTotal, VisaDue, VisaDPC, VisaHygiene, LabFees,
                     DntlChequeNumber, DntlChequeDate, HygDepositDate, CardStatementDate, BankPaid, CashDiff,
                     ExpenseBase, LabOnCard, CorrectVisaDPC, CorrectVisaHygiene, DPCDiff, QBChequeFound, QBJournalFound, Status)
                OUTPUT INSERTED.SplitMonthID
                VALUES (:fy, :sheet, :ms, :src, :c8, :b6, :c6, :hyg, :d6, :chq, :chqd, :depd, :csd, :paid, :cdiff,
                        :base, :lab, :cdpc, :chyg, :ddiff, :qbc, :qbj, :status)"""),
                {"fy": fy_end, "sheet": m.sheet, "ms": m.month, "src": source_file, "c8": round(m.cheque_total, 2),
                 "b6": m.visa_due, "c6": round(m.visa_dpc, 2), "hyg": m.visa_hygiene, "d6": m.lab_fees,
                 "chq": r.cheque_number, "chqd": r.cheque_date, "depd": r.deposit_date, "csd": r.statement_date,
                 "paid": r.bank_paid, "cdiff": r.cash_diff, "base": r.expense_base, "lab": r.lab_on_card,
                 "cdpc": r.correct_dpc, "chyg": r.correct_hygiene, "ddiff": r.dpc_diff,
                 "qbc": r.qb_cheque_found, "qbj": r.qb_journal_found, "status": r.status}).fetchone()[0]
            r.split_month_id = sid
            if m.lines:
                conn.execute(text("""INSERT INTO fin.SplitLines (SplitMonthID, Category, Total, DPCPortion, Rate, Formula,
                                                                  QBAccount, QBAmount)
                                     VALUES (:s, :cat, :tot, :dpc, :rate, :f, :qa, :qamt)"""),
                             [{"s": sid, "cat": l.category, "tot": l.total, "dpc": round(l.dpc_portion, 2),
                               "rate": l.rate, "f": (l.formula or "")[:200] or None,
                               "qa": (r.qb_postings.get(l.category) or (None, None))[0],
                               "qamt": (r.qb_postings.get(l.category) or (None, None))[1]} for l in m.lines])
            if m.lab_items:
                conn.execute(text("""INSERT INTO fin.SplitLabItems (SplitMonthID, Description, Amount, CardTransactionID, CardDate)
                                     VALUES (:s, :d, :a, :tid, :cd)"""),
                             [{"s": sid, "d": i.description[:200], "a": i.amount,
                               "tid": (r.lab_matches.get(i.row) or (None, None))[0],
                               "cd": (r.lab_matches.get(i.row) or (None, None))[1]} for i in m.lab_items])
        if findings:
            conn.execute(text("""INSERT INTO fin.Findings (Source, RunKey, Company, Subject, Area, Severity, Message, Amount, RefTable, RefID)
                                 VALUES ('split', :rk, :co, :subj, :area, :sev, :msg, :amt, 'fin.SplitMonths', :ref)"""),
                         [{"rk": f"FY{fy_end}", "co": f.get("company", "dntl"), "subj": f["subject"], "area": f["area"],
                           "sev": f["severity"], "msg": f["message"][:1000], "amt": f.get("amount"),
                           "ref": getattr(f.get("result"), "split_month_id", None)} for f in findings])
