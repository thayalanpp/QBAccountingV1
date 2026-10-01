/* =====================================================================
   reset_test_data.sql  -  wipe loaded data for clean testing
   Database: QBAccounting

   @FullReset = 0 (default) - TRANSACTION DATA ONLY
       Clears:  fin.Findings, fin.Split* (monthly split), fin.QBReconItems, fin.QBRecon, fin.TransactionSplits,
                fin.Transactions, fin.Reconciliation, fin.Statements,
                qb.JournalEntryLines, qb.JournalEntries, qb.AccountBalances, qb.LoadRuns
       Keeps:   fin.Accounts, fin.AccountLink, qb.Companies, qb.AccountMap, qb.StdAccounts,
                qb.ChartOfAccounts, qb.Classes
       Use this between normal test runs - the loaders refill everything.

   @FullReset = 1 - EVERYTHING
       Also clears the "setup" tables above (accounts, account links,
       companies, chart of accounts, classes) and the group chart
       mapping (qb.StdAccounts, qb.AccountMap). Any fin.AccountLink rows
       you entered by hand are lost and must be re-entered.

   Run visa_pipeline.py (or run_qb_load.py) once first so the newer
   tables exist - the script fails and rolls back if one is missing.

   Tables and views are NOT dropped - only rows. Identity counters are
   reset so IDs start again at 1. All-or-nothing: any error rolls back.
   ===================================================================== */

USE QBAccounting;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @FullReset BIT = 0;   -- <<< change to 1 for a full wipe

-- ---------- Row counts before ----------
SELECT 'BEFORE' AS Stage, t.TableName, t.RowCnt
FROM (
    SELECT 'fin.Findings'          AS TableName, COUNT_BIG(*) AS RowCnt FROM fin.Findings          UNION ALL
    SELECT 'fin.SplitMonths',                    COUNT_BIG(*)           FROM fin.SplitMonths       UNION ALL
    SELECT 'fin.QBReconItems',                   COUNT_BIG(*)           FROM fin.QBReconItems      UNION ALL
    SELECT 'fin.QBRecon',                        COUNT_BIG(*)           FROM fin.QBRecon           UNION ALL
    SELECT 'fin.TransactionSplits',              COUNT_BIG(*)           FROM fin.TransactionSplits UNION ALL
    SELECT 'fin.Transactions',                   COUNT_BIG(*)           FROM fin.Transactions      UNION ALL
    SELECT 'fin.Reconciliation',                 COUNT_BIG(*)           FROM fin.Reconciliation    UNION ALL
    SELECT 'fin.Statements',                     COUNT_BIG(*)           FROM fin.Statements        UNION ALL
    SELECT 'fin.AccountLink',                    COUNT_BIG(*)           FROM fin.AccountLink       UNION ALL
    SELECT 'fin.Accounts',                       COUNT_BIG(*)           FROM fin.Accounts          UNION ALL
    SELECT 'qb.JournalEntryLines',               COUNT_BIG(*)           FROM qb.JournalEntryLines  UNION ALL
    SELECT 'qb.JournalEntries',                  COUNT_BIG(*)           FROM qb.JournalEntries     UNION ALL
    SELECT 'qb.AccountBalances',                 COUNT_BIG(*)           FROM qb.AccountBalances    UNION ALL
    SELECT 'qb.LoadRuns',                        COUNT_BIG(*)           FROM qb.LoadRuns           UNION ALL
    SELECT 'qb.Classes',                         COUNT_BIG(*)           FROM qb.Classes            UNION ALL
    SELECT 'qb.ChartOfAccounts',                 COUNT_BIG(*)           FROM qb.ChartOfAccounts    UNION ALL
    SELECT 'qb.AccountMap',                      COUNT_BIG(*)           FROM qb.AccountMap         UNION ALL
    SELECT 'qb.StdAccounts',                     COUNT_BIG(*)           FROM qb.StdAccounts        UNION ALL
    SELECT 'qb.Companies',                       COUNT_BIG(*)           FROM qb.Companies
) t;

BEGIN TRY
    BEGIN TRANSACTION;

    -- ---------- Transaction data (children before parents) ----------
    DELETE FROM fin.Findings;
    DELETE FROM fin.SplitLabItems;
    DELETE FROM fin.SplitLines;
    DELETE FROM fin.SplitMonths;
    DELETE FROM fin.QBReconItems;
    DELETE FROM fin.QBRecon;
    DELETE FROM fin.TransactionSplits;
    DELETE FROM fin.Transactions;
    DELETE FROM fin.Reconciliation;
    DELETE FROM fin.Statements;

    DELETE FROM qb.JournalEntryLines;
    DELETE FROM qb.JournalEntries;
    DELETE FROM qb.AccountBalances;
    DELETE FROM qb.LoadRuns;

    -- ---------- Setup data (only on a full reset) ----------
    IF @FullReset = 1
    BEGIN
        DELETE FROM fin.AccountLink;          -- references Accounts, Companies, ChartOfAccounts
        DELETE FROM fin.Accounts;

        DELETE FROM qb.AccountMap;            -- group chart mapping (reload with load_account_map.py)
        DELETE FROM qb.StdAccounts;
        UPDATE qb.ChartOfAccounts SET ParentChartAccountID = NULL;   -- break self-reference
        DELETE FROM qb.ChartOfAccounts;
        DELETE FROM qb.Classes;
        DELETE FROM qb.Companies;
    END

    -- ---------- Reset identity counters so IDs restart at 1 ----------
    -- Only reseeds tables that are now empty AND have issued an ID before
    -- (reseeding a never-used table to 0 would make its first ID 0).
    DECLARE @tbl NVARCHAR(300), @sql NVARCHAR(600);
    DECLARE reseed CURSOR LOCAL FAST_FORWARD FOR
        SELECT QUOTENAME(s.name) + '.' + QUOTENAME(t.name)
        FROM sys.identity_columns ic
        JOIN sys.tables  t ON t.object_id = ic.object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE s.name IN ('fin', 'qb')
          AND ic.last_value IS NOT NULL;

    OPEN reseed;
    FETCH NEXT FROM reseed INTO @tbl;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @sql = N'IF NOT EXISTS (SELECT 1 FROM ' + @tbl + N') DBCC CHECKIDENT (''' + @tbl + N''', RESEED, 0) WITH NO_INFOMSGS;';
        EXEC sp_executesql @sql;
        FETCH NEXT FROM reseed INTO @tbl;
    END
    CLOSE reseed;
    DEALLOCATE reseed;

    COMMIT TRANSACTION;
    PRINT CASE WHEN @FullReset = 1 THEN 'Full reset complete.' ELSE 'Transaction data cleared (setup tables kept).' END;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    PRINT 'Reset FAILED - nothing was changed.';
    THROW;
END CATCH;

-- ---------- Row counts after ----------
SELECT 'AFTER' AS Stage, t.TableName, t.RowCnt
FROM (
    SELECT 'fin.Findings'          AS TableName, COUNT_BIG(*) AS RowCnt FROM fin.Findings          UNION ALL
    SELECT 'fin.SplitMonths',                    COUNT_BIG(*)           FROM fin.SplitMonths       UNION ALL
    SELECT 'fin.QBReconItems',                   COUNT_BIG(*)           FROM fin.QBReconItems      UNION ALL
    SELECT 'fin.QBRecon',                        COUNT_BIG(*)           FROM fin.QBRecon           UNION ALL
    SELECT 'fin.TransactionSplits',              COUNT_BIG(*)           FROM fin.TransactionSplits UNION ALL
    SELECT 'fin.Transactions',                   COUNT_BIG(*)           FROM fin.Transactions      UNION ALL
    SELECT 'fin.Reconciliation',                 COUNT_BIG(*)           FROM fin.Reconciliation    UNION ALL
    SELECT 'fin.Statements',                     COUNT_BIG(*)           FROM fin.Statements        UNION ALL
    SELECT 'fin.AccountLink',                    COUNT_BIG(*)           FROM fin.AccountLink       UNION ALL
    SELECT 'fin.Accounts',                       COUNT_BIG(*)           FROM fin.Accounts          UNION ALL
    SELECT 'qb.JournalEntryLines',               COUNT_BIG(*)           FROM qb.JournalEntryLines  UNION ALL
    SELECT 'qb.JournalEntries',                  COUNT_BIG(*)           FROM qb.JournalEntries     UNION ALL
    SELECT 'qb.AccountBalances',                 COUNT_BIG(*)           FROM qb.AccountBalances    UNION ALL
    SELECT 'qb.LoadRuns',                        COUNT_BIG(*)           FROM qb.LoadRuns           UNION ALL
    SELECT 'qb.Classes',                         COUNT_BIG(*)           FROM qb.Classes            UNION ALL
    SELECT 'qb.ChartOfAccounts',                 COUNT_BIG(*)           FROM qb.ChartOfAccounts    UNION ALL
    SELECT 'qb.AccountMap',                      COUNT_BIG(*)           FROM qb.AccountMap         UNION ALL
    SELECT 'qb.StdAccounts',                     COUNT_BIG(*)           FROM qb.StdAccounts        UNION ALL
    SELECT 'qb.Companies',                       COUNT_BIG(*)           FROM qb.Companies
) t;
GO
