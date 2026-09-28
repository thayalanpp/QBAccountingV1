"""
bank_sql.py

Database side of bank_pipeline.py. Writes the same fin.* tables as the
Visa pipeline (AccountType = 'Bank'), one fin.Statements row per month.

Rerun-safe: saving an account's fiscal year deletes that account's
statements dated in the year first (their transactions, allocations and
QB reconciliation go with them) and writes the year again - all in one
transaction.
"""

from datetime import date

from sqlalchemy import text

from bank_models import BankExport
from bank_validator import BankValidation
from statement_validator import ValidationResult


def ensure_qb_links(engine, account_id: int, company_code: str, qb_account_numbers: list[str]) -> list[str]:
    """Create the fin.AccountLink rows for this bank account if they're missing. Returns warnings."""
    warnings = []
    with engine.begin() as conn:
        company = conn.execute(text("SELECT CompanyID FROM qb.Companies WHERE CompanyCode = :c"),
                               {"c": company_code}).fetchone()
        if not company:
            return [f"company '{company_code}' isn't in qb.Companies yet - run run_qb_load.py; no QB link made"]
        for num in qb_account_numbers:
            coa = conn.execute(text("""SELECT ChartAccountID FROM qb.ChartOfAccounts
                                        WHERE CompanyID = :cid AND AccountNumber = :n"""),
                               {"cid": company[0], "n": num}).fetchone()
            if not coa:
                warnings.append(f"QB account {company_code.upper()}-{num} not found - no link made")
                continue
            exists = conn.execute(text("""SELECT 1 FROM fin.AccountLink
                                           WHERE AccountID = :a AND CompanyID = :c AND ChartAccountID = :ca"""),
                                  {"a": account_id, "c": company[0], "ca": coa[0]}).fetchone()
            if not exists:
                conn.execute(text("""INSERT INTO fin.AccountLink (AccountID, CompanyID, ChartAccountID, Notes)
                                     VALUES (:a, :c, :ca, :n)"""),
                             {"a": account_id, "c": company[0], "ca": coa[0], "n": "created by bank_pipeline.py"})
    return warnings


def save_bank_year(engine, account_id: int, exp: BankExport, v: BankValidation,
                   fy_start: date, fy_end: date) -> list[tuple[int, object]]:
    """Replace this account's statements for the fiscal year. Returns [(StatementID, BankPeriod)]."""
    result: ValidationResult = v.result
    status = "Partial" if v.partial else "Balanced"
    notes = result.short()[:400]
    extractor = f"{exp.reader}_export"
    saved = []

    with engine.begin() as conn:
        conn.execute(text("""DELETE FROM fin.Statements
                             WHERE AccountID = :a AND StatementDate BETWEEN :s AND :e"""),
                     {"a": account_id, "s": fy_start, "e": fy_end})

        for p in v.periods:
            sid = conn.execute(text("""
                INSERT INTO fin.Statements
                    (AccountID, StatementDate, OpeningBalance, TotalPayments, TotalPurchases, TotalInterest,
                     EndingBalance, SourceFile, PeriodStart, PeriodEnd, Extractor, ValidationStatus, ValidationNotes,
                     TotalDeposits, TotalWithdrawals)
                OUTPUT INSERTED.StatementID
                VALUES (:a, :sdate, :opening, 0, 0, 0, :closing, :src, :ps, :pe, :ex, :vs, :vn, :dep, :wd)"""),
                {"a": account_id, "sdate": p.period_end, "opening": p.opening, "closing": p.closing,
                 "src": exp.source_file, "ps": p.period_start, "pe": p.period_end, "ex": extractor,
                 "vs": status, "vn": notes, "dep": p.deposits, "wd": p.withdrawals}).fetchone()[0]

            if p.transactions:
                conn.execute(text("""
                    INSERT INTO fin.Transactions
                        (StatementID, AccountID, TransactionDate, PostingDate, Description, Amount, RunningBalance,
                         SourceType, TxnType, SubDescription, ChequeNumber, Tag, BankOrder, LineNumber)
                    VALUES (:sid, :a, :d, :d, :desc, :amt, :bal, 'Bank', :tt, :sub, :chq, :tag, :seq, :seq)"""),
                    [{"sid": sid, "a": account_id, "d": t.txn_date, "desc": t.description[:400], "amt": t.amount,
                      "bal": t.balance, "tt": "cheque" if t.cheque_number else ("deposit" if t.amount > 0 else "withdrawal"),
                      "sub": (t.sub_description or None) and t.sub_description[:400], "chq": t.cheque_number,
                      "tag": (t.tag or None) and t.tag[:200], "seq": t.seq} for t in p.transactions])

                with_splits = [t for t in p.transactions if t.splits]
                if with_splits:
                    ids = dict(conn.execute(text("SELECT BankOrder, TransactionID FROM fin.Transactions WHERE StatementID = :sid"),
                                            {"sid": sid}).fetchall())
                    conn.execute(text("""INSERT INTO fin.TransactionSplits (TransactionID, Category, Amount)
                                         VALUES (:tid, :cat, :amt)"""),
                                 [{"tid": ids[t.seq], "cat": cat[:50], "amt": amt}
                                  for t in with_splits for cat, amt in t.splits.items()])

            net = round(sum(t.amount for t in p.transactions), 2)
            calc = round(p.opening + net, 2)
            conn.execute(text("""
                INSERT INTO fin.Reconciliation (StatementID, NetChange, CalculatedEndingBalance, ActualEndingBalance, Variance)
                VALUES (:sid, :net, :calc, :actual, :var)"""),
                {"sid": sid, "net": net, "calc": calc, "actual": p.closing, "var": round(calc - p.closing, 2)})
            saved.append((sid, p))
    return saved
