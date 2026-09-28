"""
visa_sql.py

Database side of visa_pipeline.py. Kept separate from sql_helper.py so
reflect0.py's existing save path is untouched; both write the same
fin.* tables (the new columns are all nullable).

Rerun-safe: saving a statement again (same account + statement date)
replaces its transactions, its fin.Reconciliation row and its QB
reconciliation - never duplicates.
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import bindparam, text

from qb_matcher import MatchResult, QBLine, StmtLine
from statement_validator import ValidationResult
from visa_models import CardStatement


def _as_date(v) -> Optional[date]:
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------
def find_account_id(engine, account_name: str, account_type: str = "Visa") -> Optional[int]:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT AccountID FROM fin.Accounts WHERE AccountName = :n AND AccountType = :t"),
            {"n": account_name, "t": account_type},
        ).fetchone()
    return row[0] if row else None


def get_or_create_account(engine, account_name: str, institution: str, last_four: Optional[str],
                          account_type: str = "Visa") -> int:
    account_id = find_account_id(engine, account_name, account_type)
    if account_id is not None:
        return account_id
    with engine.begin() as conn:
        return conn.execute(
            text("""INSERT INTO fin.Accounts (AccountName, AccountType, Institution, LastFour)
                     OUTPUT INSERTED.AccountID
                     VALUES (:n, :t, :i, :l4)"""),
            {"n": account_name, "t": account_type, "i": institution, "l4": last_four},
        ).fetchone()[0]


# ---------------------------------------------------------------------
# Validation support
# ---------------------------------------------------------------------
def get_statement_new_balance(engine, account_id: Optional[int], statement_date: Optional[date]) -> Optional[float]:
    """New (ending) balance of the statement dated statement_date, if it's in SQL."""
    if account_id is None or statement_date is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT EndingBalance FROM fin.Statements WHERE AccountID = :a AND StatementDate = :d"),
            {"a": account_id, "d": statement_date},
        ).fetchone()
    return float(row[0]) if row else None


# ---------------------------------------------------------------------
# Save a validated statement
# ---------------------------------------------------------------------
def save_statement(engine, account_id: int, stmt: CardStatement, validation: ValidationResult) -> int:
    s = stmt.summary
    params = {
        "aid": account_id, "sdate": stmt.statement_date,
        "opening": s.previous_balance, "payments": s.payments_and_credits, "purchases": s.purchases,
        "interest": s.interest, "cash": s.cash_advances, "fees": s.fees, "ending": s.new_balance,
        "src": stmt.source_file, "pstart": stmt.period_start, "pend": stmt.period_end,
        "prev": stmt.previous_statement_date, "extractor": stmt.extractor,
        "vstatus": "Balanced" if validation.passed else "Failed",
        "vnotes": validation.short()[:400],
    }

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT StatementID FROM fin.Statements WHERE AccountID = :aid AND StatementDate = :sdate"),
            {"aid": account_id, "sdate": stmt.statement_date},
        ).fetchone()

        if row:
            statement_id = row[0]
            conn.execute(text("""
                UPDATE fin.Statements
                SET OpeningBalance = :opening, TotalPayments = :payments, TotalPurchases = :purchases,
                    TotalInterest = :interest, TotalCashAdvances = :cash, TotalFees = :fees,
                    EndingBalance = :ending, SourceFile = :src, PeriodStart = :pstart, PeriodEnd = :pend,
                    PreviousStatementDate = :prev, Extractor = :extractor,
                    ValidationStatus = :vstatus, ValidationNotes = :vnotes
                WHERE StatementID = :sid"""), {**params, "sid": statement_id})
            for table in ("fin.QBReconItems", "fin.QBRecon", "fin.Transactions", "fin.Reconciliation"):
                conn.execute(text(f"DELETE FROM {table} WHERE StatementID = :sid"), {"sid": statement_id})
        else:
            statement_id = conn.execute(text("""
                INSERT INTO fin.Statements
                    (AccountID, StatementDate, OpeningBalance, TotalPayments, TotalPurchases, TotalInterest,
                     TotalCashAdvances, TotalFees, EndingBalance, SourceFile, PeriodStart, PeriodEnd,
                     PreviousStatementDate, Extractor, ValidationStatus, ValidationNotes)
                OUTPUT INSERTED.StatementID
                VALUES (:aid, :sdate, :opening, :payments, :purchases, :interest,
                        :cash, :fees, :ending, :src, :pstart, :pend,
                        :prev, :extractor, :vstatus, :vnotes)"""), params).fetchone()[0]

        conn.execute(text("""
            INSERT INTO fin.Transactions
                (StatementID, AccountID, TransactionDate, PostingDate, Description, Amount, SourceType,
                 TxnType, ForeignAmount, ForeignCurrency, ExchangeRate, SourcePage, LineNumber)
            VALUES (:sid, :aid, :tdate, :pdate, :desc, :amt, 'Visa',
                    :ttype, :famt, :fcur, :frate, :page, :lineno)"""),
            [{"sid": statement_id, "aid": account_id, "tdate": t.txn_date, "pdate": t.posting_date,
              "desc": t.description[:400], "amt": t.amount, "ttype": t.txn_type.value,
              "famt": t.foreign_amount, "fcur": t.foreign_currency, "frate": t.exchange_rate,
              "page": t.page, "lineno": n}
             for n, t in enumerate(stmt.transactions, start=1)])

        # Keep fin.Reconciliation (opening + activity = ending) filled, as reflect0 did.
        net = round(sum(t.amount for t in stmt.transactions), 2)
        calc = round(s.previous_balance + net, 2)
        conn.execute(text("""
            INSERT INTO fin.Reconciliation (StatementID, NetChange, CalculatedEndingBalance, ActualEndingBalance, Variance)
            VALUES (:sid, :net, :calc, :actual, :var)"""),
            {"sid": statement_id, "net": net, "calc": calc, "actual": s.new_balance,
             "var": round(calc - s.new_balance, 2)})

    return statement_id


# ---------------------------------------------------------------------
# QuickBooks reconciliation
# ---------------------------------------------------------------------
def get_qb_links(engine, account_id: int) -> list[dict]:
    """QB account(s) this card is recorded in, from fin.AccountLink."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT al.CompanyID, c.CompanyCode, al.ChartAccountID, coa.AccountNumber, coa.AccountName
            FROM fin.AccountLink al
            JOIN qb.Companies c         ON c.CompanyID = al.CompanyID
            JOIN qb.ChartOfAccounts coa ON coa.ChartAccountID = al.ChartAccountID
            WHERE al.AccountID = :aid"""), {"aid": account_id}).fetchall()
    return [{"company_id": r[0], "company": r[1], "chart_account_id": r[2],
             "label": f"{r[1]} {r[4]}" if str(r[4]).startswith(str(r[3])) else f"{r[1]} {r[3]} {r[4]}"}
            for r in rows]


def load_statement_lines(engine, statement_id: int) -> list[StmtLine]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT TransactionID, TransactionDate, PostingDate, Description, SubDescription, Amount, ChequeNumber
            FROM fin.Transactions WHERE StatementID = :sid ORDER BY LineNumber, TransactionID"""),
            {"sid": statement_id}).fetchall()
    return [StmtLine(r[0], _as_date(r[1]), _as_date(r[2] or r[1]),
                     r[3] + (f" - {r[4]}" if r[4] else ""), float(r[5]), r[6]) for r in rows]


def load_qb_candidates(engine, chart_account_ids: list[int], date_from: date, date_to: date,
                       statement_id: int) -> list[QBLine]:
    """QB lines on the linked accounts in the date window, excluding lines already matched to another statement."""
    sql = text("""
        SELECT jel.LineID, je.TxnDate, jel.Amount, je.TxnType, je.RefNum, je.Name,
               COALESCE(jel.LineMemo, je.Memo)
        FROM qb.JournalEntryLines jel
        JOIN qb.JournalEntries je ON je.JournalEntryID = jel.JournalEntryID
        WHERE jel.ChartAccountID IN :ids
          AND je.TxnDate BETWEEN :dfrom AND :dto
          AND NOT EXISTS (SELECT 1 FROM fin.QBReconItems ri
                          WHERE ri.QBLineID = jel.LineID AND ri.MatchStatus = 'Matched'
                            AND ri.StatementID <> :sid)
        ORDER BY je.TxnDate, jel.LineID""").bindparams(bindparam("ids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(sql, {"ids": chart_account_ids, "dfrom": date_from, "dto": date_to,
                                  "sid": statement_id}).fetchall()
    return [QBLine(int(r[0]), _as_date(r[1]), float(r[2]), r[3], r[4], r[5], r[6]) for r in rows]


def save_qb_recon(engine, statement_id: int, links: list[dict], result: MatchResult,
                  window_start: date, window_end: date):
    items = (
        [{"sid": statement_id, "st": "Matched", "tid": m.stmt.transaction_id, "lid": m.qb.line_id,
          "days": m.days_apart} for m in result.matched]
        + [{"sid": statement_id, "st": "StatementOnly", "tid": s.transaction_id, "lid": None, "days": None}
           for s in result.statement_only]
        + [{"sid": statement_id, "st": "QBOnly", "tid": None, "lid": q.line_id, "days": None}
           for q in result.qb_only]
    )
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM fin.QBReconItems WHERE StatementID = :sid"), {"sid": statement_id})
        conn.execute(text("DELETE FROM fin.QBRecon WHERE StatementID = :sid"), {"sid": statement_id})
        conn.execute(text("""
            INSERT INTO fin.QBRecon (StatementID, QBAccounts, WindowStart, WindowEnd, StatementNet, QBNet,
                                     Variance, MatchedCount, StatementOnlyCount, QBOnlyCount)
            VALUES (:sid, :accts, :ws, :we, :snet, :qnet, :var, :m, :so, :qo)"""),
            {"sid": statement_id, "accts": ", ".join(l["label"] for l in links)[:400],
             "ws": window_start, "we": window_end, "snet": result.statement_net, "qnet": result.qb_net,
             "var": result.variance, "m": len(result.matched), "so": len(result.statement_only),
             "qo": len(result.qb_only)})
        if items:
            conn.execute(text("""
                INSERT INTO fin.QBReconItems (StatementID, MatchStatus, TransactionID, QBLineID, DaysApart)
                VALUES (:sid, :st, :tid, :lid, :days)"""), items)
