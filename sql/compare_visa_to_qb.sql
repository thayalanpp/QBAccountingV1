/* =====================================================================
   compare_visa_to_qb.sql  -  Visa statements vs. the linked QuickBooks account
   Database: QBAccounting

   Uses fin.AccountLink to find the QB account that records the card
   (TD Aeroplan Visa -> dntl 22200). Each statement covers the day after
   the previous statement up to its own statement date; the first
   statement loaded is assumed to cover one month.

   Compares ACTIVITY within each statement period, so it does not need
   QuickBooks opening balances (which the loader doesn't store yet).

   Query 1: one row per statement - statement net change vs. QB net change
   Query 2: one statement, transaction by transaction - what matches,
            what's only on the statement, what's only in QuickBooks
   Read-only: nothing is changed.
   ===================================================================== */

USE QBAccounting;
GO

/* ---------------------------------------------------------------------
   QUERY 1 - Statement-period summary
   Sign conventions can differ (a charge may be + on the statement and
   - in QB), so both variances are shown. Whichever column is ~0 across
   most statements tells you the convention; nonzero rows in THAT column
   are the periods to investigate with Query 2.
   --------------------------------------------------------------------- */
DECLARE @AccountName NVARCHAR(200) = N'TD Aeroplan Visa Infinite Privilege';

WITH link AS (
    SELECT al.AccountID, al.ChartAccountID
    FROM fin.AccountLink al
    JOIN fin.Accounts a ON a.AccountID = al.AccountID
    WHERE a.AccountName = @AccountName
),
stmt AS (
    SELECT s.StatementID, s.AccountID, s.StatementDate,
           s.OpeningBalance, s.EndingBalance,
           s.TotalPurchases, s.TotalPayments, s.TotalInterest,
           DATEADD(DAY, 1, COALESCE(
               LAG(s.StatementDate) OVER (PARTITION BY s.AccountID ORDER BY s.StatementDate),
               DATEADD(MONTH, -1, s.StatementDate))) AS PeriodStart
    FROM fin.Statements s
    JOIN link l ON l.AccountID = s.AccountID
)
SELECT
    st.StatementDate,
    st.PeriodStart,
    st.OpeningBalance,
    st.EndingBalance,
    st.EndingBalance - st.OpeningBalance                  AS StatementNetChange,
    q.QBNetChange,
    q.QBLineCount,
    (st.EndingBalance - st.OpeningBalance) - q.QBNetChange AS Variance_SameSign,
    (st.EndingBalance - st.OpeningBalance) + q.QBNetChange AS Variance_OppositeSign
FROM stmt st
JOIN link l ON l.AccountID = st.AccountID
OUTER APPLY (
    SELECT COALESCE(SUM(jel.Amount), 0) AS QBNetChange,
           COUNT(*)                     AS QBLineCount
    FROM qb.JournalEntryLines jel
    JOIN qb.JournalEntries je ON je.JournalEntryID = jel.JournalEntryID
    WHERE jel.ChartAccountID = l.ChartAccountID
      AND je.TxnDate BETWEEN st.PeriodStart AND st.StatementDate
) q
ORDER BY st.StatementDate;
GO

/* ---------------------------------------------------------------------
   QUERY 2 - Transaction-level match for ONE statement
   Pairs statement lines with QB lines of the same dollar amount (sign
   ignored) inside the statement period. Duplicate amounts pair up in
   date order. Look at:
     'Statement only' - on the card, not in the books (missing entry)
     'QB only'        - in the books, not on this statement (wrong
                        account, wrong period, or a manual adjustment)
     'Matched' with a large DaysApart - posted to the wrong period
   --------------------------------------------------------------------- */
DECLARE @AccountName   NVARCHAR(200) = N'TD Aeroplan Visa Infinite Privilege';
DECLARE @StatementDate DATE          = '2025-04-07';   -- <<< statement to inspect

WITH link AS (
    SELECT al.AccountID, al.ChartAccountID
    FROM fin.AccountLink al
    JOIN fin.Accounts a ON a.AccountID = al.AccountID
    WHERE a.AccountName = @AccountName
),
stmt AS (
    SELECT s.StatementID, s.AccountID, s.StatementDate,
           DATEADD(DAY, 1, COALESCE(
               LAG(s.StatementDate) OVER (PARTITION BY s.AccountID ORDER BY s.StatementDate),
               DATEADD(MONTH, -1, s.StatementDate))) AS PeriodStart
    FROM fin.Statements s
    JOIN link l ON l.AccountID = s.AccountID
),
win AS (
    SELECT StatementID, PeriodStart, StatementDate FROM stmt WHERE StatementDate = @StatementDate
),
v AS (
    SELECT t.TransactionID, t.TransactionDate, t.Description, t.Amount,
           ABS(t.Amount) AS AbsAmt,
           ROW_NUMBER() OVER (PARTITION BY ABS(t.Amount) ORDER BY t.TransactionDate, t.TransactionID) AS rn
    FROM fin.Transactions t
    JOIN win w ON w.StatementID = t.StatementID
),
q AS (
    SELECT jel.LineID, je.TxnDate, je.TxnType, je.RefNum, je.Name,
           COALESCE(jel.LineMemo, je.Memo) AS Memo, jel.Amount,
           ABS(jel.Amount) AS AbsAmt,
           ROW_NUMBER() OVER (PARTITION BY ABS(jel.Amount) ORDER BY je.TxnDate, jel.LineID) AS rn
    FROM qb.JournalEntryLines jel
    JOIN qb.JournalEntries je ON je.JournalEntryID = jel.JournalEntryID
    CROSS JOIN link l
    JOIN win w ON je.TxnDate BETWEEN w.PeriodStart AND w.StatementDate
    WHERE jel.ChartAccountID = l.ChartAccountID
)
SELECT
    CASE WHEN q.LineID IS NULL THEN 'Statement only'
         WHEN v.TransactionID IS NULL THEN 'QB only'
         ELSE 'Matched' END                         AS MatchStatus,
    v.TransactionDate                               AS StmtDate,
    v.Description                                   AS StmtDescription,
    v.Amount                                        AS StmtAmount,
    q.TxnDate                                       AS QBDate,
    q.TxnType                                       AS QBType,
    q.RefNum                                        AS QBRef,
    q.Name                                          AS QBName,
    q.Memo                                          AS QBMemo,
    q.Amount                                        AS QBAmount,
    DATEDIFF(DAY, v.TransactionDate, q.TxnDate)     AS DaysApart
FROM v
FULL OUTER JOIN q ON q.AbsAmt = v.AbsAmt AND q.rn = v.rn
ORDER BY
    CASE WHEN q.LineID IS NULL THEN 1 WHEN v.TransactionID IS NULL THEN 2 ELSE 3 END,
    COALESCE(v.TransactionDate, q.TxnDate);
GO
