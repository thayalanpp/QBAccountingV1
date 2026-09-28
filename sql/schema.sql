-- ============================================================
-- QbVisaAgenticSupport - Central Financial Data Store
-- Target: Microsoft SQL Server (2016+)
--
-- Design notes:
--   - Mirrors the two sheets the pipeline already produces today
--     (Summary -> fin.Statements, Transactions -> fin.Transactions),
--     plus the Stage 2 reconciliation output (fin.Reconciliation).
--   - fin.Accounts is the extension point: today every row is
--     AccountType='Visa', but the same tables take Bank statements
--     and QuickBooks transactions once those pipelines land -
--     just a new AccountType and (for QuickBooks) extra columns
--     on Transactions, no redesign needed.
--   - Every CREATE is guarded so this script is safe to run on
--     every deploy, not just the first time.
-- ============================================================

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'fin')
    EXEC('CREATE SCHEMA fin');
GO

-- ------------------------------------------------------------
-- fin.Accounts: one row per financial account / data source
-- (a Visa card, a bank account, a QuickBooks company file, ...)
-- ------------------------------------------------------------
IF OBJECT_ID('fin.Accounts', 'U') IS NULL
BEGIN
    CREATE TABLE fin.Accounts (
        AccountID       INT IDENTITY(1,1)   NOT NULL,
        AccountName     NVARCHAR(200)       NOT NULL,   -- e.g. "TD Aeroplan Visa Infinite Privilege"
        AccountType     NVARCHAR(30)        NOT NULL,   -- 'Visa' | 'Bank' | 'QuickBooks'
        Institution     NVARCHAR(100)       NULL,       -- e.g. "TD"
        LastFour        NVARCHAR(4)         NULL,
        Currency        NVARCHAR(3)         NOT NULL CONSTRAINT DF_Accounts_Currency DEFAULT ('CAD'),
        CreatedAt       DATETIME2           NOT NULL CONSTRAINT DF_Accounts_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Accounts PRIMARY KEY (AccountID),
        CONSTRAINT UQ_Accounts_Name_Type UNIQUE (AccountName, AccountType)
    );
END
GO

-- ------------------------------------------------------------
-- fin.Statements: one row per statement period per account
-- (mirrors today's "Summary" sheet)
-- ------------------------------------------------------------
IF OBJECT_ID('fin.Statements', 'U') IS NULL
BEGIN
    CREATE TABLE fin.Statements (
        StatementID     INT IDENTITY(1,1)  NOT NULL,
        AccountID       INT                NOT NULL,
        StatementDate   DATE               NOT NULL,
        OpeningBalance  DECIMAL(14,2)      NOT NULL,
        TotalPayments   DECIMAL(14,2)      NOT NULL CONSTRAINT DF_Statements_Payments DEFAULT (0),
        TotalPurchases  DECIMAL(14,2)      NOT NULL CONSTRAINT DF_Statements_Purchases DEFAULT (0),
        TotalInterest   DECIMAL(14,2)      NOT NULL CONSTRAINT DF_Statements_Interest DEFAULT (0),
        EndingBalance   DECIMAL(14,2)      NOT NULL,
        SourceFile      NVARCHAR(400)      NULL,        -- original PDF/Excel filename, for traceability
        ImportedAt      DATETIME2          NOT NULL CONSTRAINT DF_Statements_ImportedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Statements PRIMARY KEY (StatementID),
        CONSTRAINT FK_Statements_Accounts FOREIGN KEY (AccountID) REFERENCES fin.Accounts(AccountID),
        CONSTRAINT UQ_Statements_Account_Date UNIQUE (AccountID, StatementDate)
    );
END
GO

-- ------------------------------------------------------------
-- fin.Transactions: one row per transaction line
-- (mirrors today's "Transactions" sheet)
-- ------------------------------------------------------------
IF OBJECT_ID('fin.Transactions', 'U') IS NULL
BEGIN
    CREATE TABLE fin.Transactions (
        TransactionID   BIGINT IDENTITY(1,1) NOT NULL,
        StatementID     INT                  NOT NULL,
        AccountID       INT                  NOT NULL,   -- denormalized for easy filtering without a join
        TransactionDate DATE                 NOT NULL,
        PostingDate     DATE                 NULL,
        Description     NVARCHAR(400)        NOT NULL,
        Amount          DECIMAL(14,2)        NOT NULL,
        RunningBalance  DECIMAL(14,2)        NULL,        -- bank statements state this per line; Visa doesn't
        SourceType      NVARCHAR(30)         NOT NULL CONSTRAINT DF_Transactions_SourceType DEFAULT ('Visa'),
        CreatedAt       DATETIME2            NOT NULL CONSTRAINT DF_Transactions_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Transactions PRIMARY KEY (TransactionID),
        CONSTRAINT FK_Transactions_Statements FOREIGN KEY (StatementID) REFERENCES fin.Statements(StatementID) ON DELETE CASCADE,
        CONSTRAINT FK_Transactions_Accounts FOREIGN KEY (AccountID) REFERENCES fin.Accounts(AccountID)
    );

    CREATE INDEX IX_Transactions_Statement ON fin.Transactions(StatementID);
    CREATE INDEX IX_Transactions_Account_Date ON fin.Transactions(AccountID, TransactionDate);
END
GO

-- ------------------------------------------------------------
-- fin.Reconciliation: Stage 2 output (Opening + Net Change = Ending)
-- One row per statement, replaced whenever that statement is reprocessed.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.Reconciliation', 'U') IS NULL
BEGIN
    CREATE TABLE fin.Reconciliation (
        ReconciliationID        INT IDENTITY(1,1) NOT NULL,
        StatementID              INT              NOT NULL,
        NetChange                 DECIMAL(14,2)    NOT NULL,
        CalculatedEndingBalance   DECIMAL(14,2)    NOT NULL,
        ActualEndingBalance       DECIMAL(14,2)    NOT NULL,
        Variance                  DECIMAL(14,2)    NOT NULL,
        IsBalanced                AS (CASE WHEN Variance = 0 THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END) PERSISTED,
        ReconciledAt               DATETIME2       NOT NULL CONSTRAINT DF_Reconciliation_At DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Reconciliation PRIMARY KEY (ReconciliationID),
        CONSTRAINT FK_Reconciliation_Statements FOREIGN KEY (StatementID) REFERENCES fin.Statements(StatementID) ON DELETE CASCADE,
        CONSTRAINT UQ_Reconciliation_Statement UNIQUE (StatementID)
    );
END
GO

-- ------------------------------------------------------------
-- Convenience view: one row per statement with its reconciliation
-- status joined in, for quick reporting / the future analysis agent.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.vw_StatementStatus', 'V') IS NOT NULL
    DROP VIEW fin.vw_StatementStatus;
GO

CREATE VIEW fin.vw_StatementStatus AS
SELECT
    a.AccountID,
    a.AccountName,
    a.AccountType,
    s.StatementID,
    s.StatementDate,
    s.OpeningBalance,
    s.EndingBalance,
    r.Variance,
    r.IsBalanced,
    (SELECT COUNT(*) FROM fin.Transactions t WHERE t.StatementID = s.StatementID) AS TransactionCount
FROM fin.Statements s
JOIN fin.Accounts a ON a.AccountID = s.AccountID
LEFT JOIN fin.Reconciliation r ON r.StatementID = s.StatementID;
GO


-- ============================================================
-- QuickBooks ledger side (qb.*)
--
-- Three separate QuickBooks company files (dntl, hyg, mgmt), each
-- with its own account numbering scheme - hyg's "1010.25" isn't
-- comparable to dntl's "22200", so every ledger table is scoped
-- by CompanyID. fin.AccountLink at the bottom is the ONLY place
-- this side touches fin.* - it's what turns "opening + activity =
-- ending" into "does the bank statement match what the books say."
-- ============================================================

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'qb')
    EXEC('CREATE SCHEMA qb');
GO

-- ------------------------------------------------------------
-- qb.Companies: one row per QuickBooks company file
-- ------------------------------------------------------------
IF OBJECT_ID('qb.Companies', 'U') IS NULL
BEGIN
    CREATE TABLE qb.Companies (
        CompanyID     INT IDENTITY(1,1)  NOT NULL,
        CompanyCode   NVARCHAR(20)       NOT NULL,   -- 'dntl' | 'hyg' | 'mgmt'
        LegalName     NVARCHAR(200)      NOT NULL,   -- e.g. "S. Viswanathan Dentistry Professional Corporation"
        FiscalYearStartMonth TINYINT     NOT NULL CONSTRAINT DF_Companies_FYStart DEFAULT (9),
        CreatedAt     DATETIME2          NOT NULL CONSTRAINT DF_Companies_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Companies PRIMARY KEY (CompanyID),
        CONSTRAINT UQ_Companies_Code UNIQUE (CompanyCode)
    );
END
GO

-- ------------------------------------------------------------
-- qb.ChartOfAccounts: one row per account, per company. Self-
-- referencing parent because hyg nests three levels deep
-- (1000 -> 1010 -> 1010.25).
-- ------------------------------------------------------------
IF OBJECT_ID('qb.ChartOfAccounts', 'U') IS NULL
BEGIN
    CREATE TABLE qb.ChartOfAccounts (
        ChartAccountID        INT IDENTITY(1,1) NOT NULL,
        CompanyID              INT              NOT NULL,
        AccountNumber           NVARCHAR(20)     NOT NULL,  -- text: hyg has "1010.25"
        AccountName              NVARCHAR(200)    NOT NULL,
        AccountType               NVARCHAR(40)    NOT NULL, -- taken as-is from QuickBooks' own Type column
        ParentChartAccountID       INT             NULL,
        Description                  NVARCHAR(400) NULL,
        TaxLine                       NVARCHAR(200) NULL,
        CreatedAt                      DATETIME2    NOT NULL CONSTRAINT DF_COA_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_ChartOfAccounts PRIMARY KEY (ChartAccountID),
        CONSTRAINT FK_COA_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID),
        CONSTRAINT FK_COA_Parent FOREIGN KEY (ParentChartAccountID) REFERENCES qb.ChartOfAccounts(ChartAccountID),
        CONSTRAINT UQ_COA_Company_Number UNIQUE (CompanyID, AccountNumber)
    );
END
GO

-- ------------------------------------------------------------
-- qb.Classes: QuickBooks Class tracking (seen in hyg's export,
-- e.g. postings tagged "Post Smitha"). Optional per company.
-- ------------------------------------------------------------
IF OBJECT_ID('qb.Classes', 'U') IS NULL
BEGIN
    CREATE TABLE qb.Classes (
        ClassID     INT IDENTITY(1,1) NOT NULL,
        CompanyID   INT                NOT NULL,
        ClassName   NVARCHAR(200)      NOT NULL,
        CreatedAt   DATETIME2          NOT NULL CONSTRAINT DF_Classes_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Classes PRIMARY KEY (ClassID),
        CONSTRAINT FK_Classes_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID),
        CONSTRAINT UQ_Classes_Company_Name UNIQUE (CompanyID, ClassName)
    );
END
GO

-- ------------------------------------------------------------
-- qb.JournalEntries: one row per real transaction. The raw
-- "Transaction Detail by Account" export shows each of these
-- TWICE (once under each account it touches) - this collapses
-- that back to one row. Matched on a natural key since the text
-- export carries no real QuickBooks transaction ID.
-- ------------------------------------------------------------
IF OBJECT_ID('qb.JournalEntries', 'U') IS NULL
BEGIN
    CREATE TABLE qb.JournalEntries (
        JournalEntryID  INT IDENTITY(1,1) NOT NULL,
        CompanyID        INT              NOT NULL,
        TxnType           NVARCHAR(40)    NOT NULL,  -- 'Cheque' | 'Deposit' | 'Credit Card Charge' | ...
        TxnDate            DATE           NOT NULL,
        RefNum              NVARCHAR(40)  NULL,       -- QuickBooks "Num" column (cheque #, etc.)
        Name                 NVARCHAR(200) NULL,
        Memo                  NVARCHAR(400) NULL,
        OccurrenceRank         INT          NOT NULL CONSTRAINT DF_JE_Rank DEFAULT (1),
                                                        -- Informational only (which Nth same-key row this was
                                                        -- within its own account section). NOT used to match
                                                        -- entries across accounts - see IX_JE_NaturalKey below.
        SourceFile              NVARCHAR(400) NULL,
        ImportedAt                DATETIME2   NOT NULL CONSTRAINT DF_JE_ImportedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_JournalEntries PRIMARY KEY (JournalEntryID),
        CONSTRAINT FK_JE_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID)
    );

    -- The loader only ever merges rows into one JournalEntry when they
    -- share a real QuickBooks-assigned RefNum (a cheque #, a journal
    -- entry #) - the one field this export keeps consistent across every
    -- account a transaction touches. Name/Memo/Class are frequently NOT
    -- consistent across accounts for the same transaction in this report
    -- (a bank deposit's generic "Deposit" memo vs. itemized income-side
    -- memos; a payroll cheque's liability/expense legs vs. its net-pay
    -- leg), so a row with no RefNum becomes its own one-line entry
    -- instead of a guessed grouping. Every row's dollar amount still
    -- lands under the correct ChartAccountID either way - only the
    -- "these lines are one real-world transaction" grouping is narrower
    -- than it could be. Not a hard UNIQUE constraint (NULLs in RefNum
    -- make that unreliable in SQL Server) - the loader enforces the
    -- match itself. This index just makes that lookup fast.
    CREATE INDEX IX_JE_NaturalKey ON qb.JournalEntries(CompanyID, TxnDate, TxnType, RefNum);
END
GO

-- ------------------------------------------------------------
-- qb.JournalEntryLines: one row per leg. A simple purchase makes
-- two; a "-SPLIT-" transaction makes as many as it touched.
-- Lines for one entry always sum to zero.
-- ------------------------------------------------------------
IF OBJECT_ID('qb.JournalEntryLines', 'U') IS NULL
BEGIN
    CREATE TABLE qb.JournalEntryLines (
        LineID           BIGINT IDENTITY(1,1) NOT NULL,
        JournalEntryID    INT                 NOT NULL,
        ChartAccountID     INT                NOT NULL,
        ClassID              INT              NULL,
        Amount                DECIMAL(14,2)   NOT NULL,
        LineMemo               NVARCHAR(400)  NULL,
        CreatedAt                DATETIME2    NOT NULL CONSTRAINT DF_JEL_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_JournalEntryLines PRIMARY KEY (LineID),
        CONSTRAINT FK_JEL_JournalEntries FOREIGN KEY (JournalEntryID) REFERENCES qb.JournalEntries(JournalEntryID) ON DELETE CASCADE,
        CONSTRAINT FK_JEL_ChartOfAccounts FOREIGN KEY (ChartAccountID) REFERENCES qb.ChartOfAccounts(ChartAccountID),
        CONSTRAINT FK_JEL_Classes FOREIGN KEY (ClassID) REFERENCES qb.Classes(ClassID)
    );

    CREATE INDEX IX_JEL_Entry ON qb.JournalEntryLines(JournalEntryID);
    CREATE INDEX IX_JEL_Account ON qb.JournalEntryLines(ChartAccountID);
END
GO

-- ------------------------------------------------------------
-- qb.LoadRuns: one row per run of the QuickBooks loader. Each run
-- replaces a company's whole fiscal year (delete + reload in one
-- transaction), so this is the history of which export is currently
-- in the tables and what each reload changed.
-- ------------------------------------------------------------
IF OBJECT_ID('qb.LoadRuns', 'U') IS NULL
BEGIN
    CREATE TABLE qb.LoadRuns (
        LoadRunID          INT IDENTITY(1,1) NOT NULL,
        CompanyID          INT               NOT NULL,
        FiscalYearEnd      SMALLINT          NOT NULL,  -- FY named by the year it ends (2025 = Sep 2024 - Aug 2025)
        PeriodStart        DATE              NOT NULL,
        PeriodEnd          DATE              NOT NULL,
        SourceFile         NVARCHAR(400)     NULL,
        Status             NVARCHAR(30)      NOT NULL,  -- 'Loaded' | 'Loaded (partial)' | 'Aborted'
        EntriesDeleted     INT               NOT NULL CONSTRAINT DF_LoadRuns_ED DEFAULT (0),
        LinesDeleted       INT               NOT NULL CONSTRAINT DF_LoadRuns_LD DEFAULT (0),
        EntriesWritten     INT               NOT NULL CONSTRAINT DF_LoadRuns_EW DEFAULT (0),
        LinesWritten       INT               NOT NULL CONSTRAINT DF_LoadRuns_LW DEFAULT (0),
        SectionsLoaded     INT               NOT NULL CONSTRAINT DF_LoadRuns_SL DEFAULT (0),
        SectionsSkipped    INT               NOT NULL CONSTRAINT DF_LoadRuns_SS DEFAULT (0),
        RowsOutsidePeriod  INT               NOT NULL CONSTRAINT DF_LoadRuns_ROP DEFAULT (0),
        Notes              NVARCHAR(400)     NULL,
        RunAt              DATETIME2         NOT NULL CONSTRAINT DF_LoadRuns_RunAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_LoadRuns PRIMARY KEY (LoadRunID),
        CONSTRAINT FK_LoadRuns_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID)
    );

    CREATE INDEX IX_LoadRuns_Company_FY ON qb.LoadRuns(CompanyID, FiscalYearEnd, RunAt);
END
GO

-- ------------------------------------------------------------
-- fin.AccountLink: the bridge. Links a real-world account
-- (fin.Accounts - a Visa card, a bank account) to the QuickBooks
-- ledger account that tracks it, per company. Four rows today.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.AccountLink', 'U') IS NULL
BEGIN
    CREATE TABLE fin.AccountLink (
        LinkID          INT IDENTITY(1,1) NOT NULL,
        AccountID        INT              NOT NULL,
        CompanyID         INT             NOT NULL,
        ChartAccountID     INT            NOT NULL,
        Notes                NVARCHAR(400) NULL,
        CreatedAt              DATETIME2   NOT NULL CONSTRAINT DF_AccountLink_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_AccountLink PRIMARY KEY (LinkID),
        CONSTRAINT FK_AccountLink_Accounts FOREIGN KEY (AccountID) REFERENCES fin.Accounts(AccountID),
        CONSTRAINT FK_AccountLink_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID),
        CONSTRAINT FK_AccountLink_COA FOREIGN KEY (ChartAccountID) REFERENCES qb.ChartOfAccounts(ChartAccountID),
        CONSTRAINT UQ_AccountLink UNIQUE (AccountID, CompanyID, ChartAccountID)
    );
END
GO

-- ------------------------------------------------------------
-- Convenience view: per-account cross-source reconciliation.
-- Joins a fin.Accounts balance (from its latest statement) to
-- what the linked QuickBooks account currently shows.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.vw_CrossSourceBalance', 'V') IS NOT NULL
    DROP VIEW fin.vw_CrossSourceBalance;
GO

CREATE VIEW fin.vw_CrossSourceBalance AS
SELECT
    a.AccountID,
    a.AccountName,
    a.Institution,
    c.CompanyCode,
    coa.AccountNumber   AS QBAccountNumber,
    coa.AccountName     AS QBAccountName,
    (SELECT SUM(jel.Amount)
       FROM qb.JournalEntryLines jel
       WHERE jel.ChartAccountID = coa.ChartAccountID) AS QBLedgerBalance,
    (SELECT TOP 1 s.EndingBalance
       FROM fin.Statements s
       WHERE s.AccountID = a.AccountID
       ORDER BY s.StatementDate DESC) AS LatestStatementBalance
FROM fin.AccountLink al
JOIN fin.Accounts a ON a.AccountID = al.AccountID
JOIN qb.Companies c ON c.CompanyID = al.CompanyID
JOIN qb.ChartOfAccounts coa ON coa.ChartAccountID = al.ChartAccountID;
GO

-- ============================================================
-- Visa pipeline (visa_pipeline.py) additions
-- All guarded / nullable: safe on every run, and reflect0.py's
-- older save path keeps working unchanged.
-- ============================================================

-- fin.Statements: full printed summary, period, and how it was read/validated
IF COL_LENGTH('fin.Statements', 'PeriodStart') IS NULL
    ALTER TABLE fin.Statements ADD
        PeriodStart            DATE           NULL,
        PeriodEnd              DATE           NULL,
        PreviousStatementDate  DATE           NULL,
        TotalCashAdvances      DECIMAL(14,2)  NULL,
        TotalFees              DECIMAL(14,2)  NULL,
        Extractor              NVARCHAR(40)   NULL,   -- 'td_visa_parser' | 'openai' | 'gemini'
        ValidationStatus       NVARCHAR(20)   NULL,   -- 'Balanced' | 'Failed'
        ValidationNotes        NVARCHAR(400)  NULL;
GO

-- fin.Transactions: type, foreign currency, and where on the PDF it came from
IF COL_LENGTH('fin.Transactions', 'TxnType') IS NULL
    ALTER TABLE fin.Transactions ADD
        TxnType          NVARCHAR(20)   NULL,   -- purchase | refund | payment | interest | fee | cash_advance
        ForeignAmount    DECIMAL(14,2)  NULL,
        ForeignCurrency  NVARCHAR(3)    NULL,
        ExchangeRate     DECIMAL(12,6)  NULL,
        SourcePage       INT            NULL,
        LineNumber       INT            NULL;   -- order on the statement
GO

-- ------------------------------------------------------------
-- fin.QBRecon: one row per statement - statement vs linked QB account(s).
-- Variance is in statement sign and always equals the sum of the
-- StatementOnly and QBOnly items, so every dollar is itemized.
-- Replaced whenever the statement is reprocessed. Rerun the Visa
-- pipeline after reloading QuickBooks so matches point at the new rows.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.QBRecon', 'U') IS NULL
BEGIN
    CREATE TABLE fin.QBRecon (
        QBReconID           INT IDENTITY(1,1) NOT NULL,
        StatementID         INT               NOT NULL,
        QBAccounts          NVARCHAR(400)     NULL,   -- e.g. "dntl 22200 NM interco credit card"
        WindowStart         DATE              NOT NULL,
        WindowEnd           DATE              NOT NULL,
        StatementNet        DECIMAL(14,2)     NOT NULL,
        QBNet               DECIMAL(14,2)     NOT NULL,
        Variance            DECIMAL(14,2)     NOT NULL,
        MatchedCount        INT               NOT NULL,
        StatementOnlyCount  INT               NOT NULL,
        QBOnlyCount         INT               NOT NULL,
        RunAt               DATETIME2         NOT NULL CONSTRAINT DF_QBRecon_RunAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_QBRecon PRIMARY KEY (QBReconID),
        CONSTRAINT FK_QBRecon_Statements FOREIGN KEY (StatementID) REFERENCES fin.Statements(StatementID) ON DELETE CASCADE,
        CONSTRAINT UQ_QBRecon_Statement UNIQUE (StatementID)
    );
END
GO

-- ------------------------------------------------------------
-- fin.QBReconItems: every statement line and every in-period QB line,
-- with how it was resolved. No FK to fin.Transactions (would create a
-- second cascade path) or to qb.JournalEntryLines (would block QB reloads).
-- ------------------------------------------------------------
IF OBJECT_ID('fin.QBReconItems', 'U') IS NULL
BEGIN
    CREATE TABLE fin.QBReconItems (
        ItemID          BIGINT IDENTITY(1,1) NOT NULL,
        StatementID     INT                  NOT NULL,
        MatchStatus     NVARCHAR(20)         NOT NULL,   -- 'Matched' | 'StatementOnly' | 'QBOnly'
        TransactionID   BIGINT               NULL,       -- fin.Transactions
        QBLineID        BIGINT               NULL,       -- qb.JournalEntryLines.LineID
        DaysApart       INT                  NULL,       -- QB date minus statement posting date
        CONSTRAINT PK_QBReconItems PRIMARY KEY (ItemID),
        CONSTRAINT FK_QBReconItems_Statements FOREIGN KEY (StatementID) REFERENCES fin.Statements(StatementID) ON DELETE CASCADE
    );

    CREATE INDEX IX_QBReconItems_Statement ON fin.QBReconItems(StatementID);
    -- A QB line can be matched to only one statement line, ever.
    CREATE UNIQUE INDEX UX_QBReconItems_MatchedLine ON fin.QBReconItems(QBLineID) WHERE MatchStatus = 'Matched';
END
GO

-- ------------------------------------------------------------
-- fin.vw_QBReconDetail: the reconciliation, readable. One row per item
-- with the statement line and the QB line side by side.
--   SELECT * FROM fin.vw_QBReconDetail WHERE StatementDate = '2025-04-07'
--   ORDER BY SortOrder, LineDate;
-- ------------------------------------------------------------
IF OBJECT_ID('fin.vw_QBReconDetail', 'V') IS NOT NULL
    DROP VIEW fin.vw_QBReconDetail;
GO

CREATE VIEW fin.vw_QBReconDetail AS
SELECT
    s.StatementDate,
    ri.MatchStatus,
    CASE ri.MatchStatus WHEN 'StatementOnly' THEN 1 WHEN 'QBOnly' THEN 2 ELSE 3 END AS SortOrder,
    COALESCE(t.PostingDate, je.TxnDate)   AS LineDate,
    t.PostingDate                         AS StmtPostingDate,
    t.Description                         AS StmtDescription,
    t.TxnType                             AS StmtType,
    t.Amount                              AS StmtAmount,
    je.TxnDate                            AS QBDate,
    je.TxnType                            AS QBType,
    je.RefNum                             AS QBRef,
    je.Name                               AS QBName,
    COALESCE(jel.LineMemo, je.Memo)       AS QBMemo,
    jel.Amount                            AS QBAmount,
    ri.DaysApart,
    ri.StatementID,
    ri.TransactionID,
    ri.QBLineID
FROM fin.QBReconItems ri
JOIN fin.Statements s                 ON s.StatementID = ri.StatementID
LEFT JOIN fin.Transactions t          ON t.TransactionID = ri.TransactionID
LEFT JOIN qb.JournalEntryLines jel    ON jel.LineID = ri.QBLineID
LEFT JOIN qb.JournalEntries je        ON je.JournalEntryID = jel.JournalEntryID;
GO

-- ============================================================
-- Group chart of accounts (load_account_map.py)
--   qb.StdAccounts  - one standard chart for all three companies
--   qb.AccountMap   - each company's QB account -> one standard account
-- Loaded from the reviewed Group_Chart_of_Accounts_Mapping.xlsx.
-- QuickBooks itself is not changed.
-- ============================================================
IF OBJECT_ID('qb.StdAccounts', 'U') IS NULL
BEGIN
    CREATE TABLE qb.StdAccounts (
        StdAccountNumber  NVARCHAR(10)   NOT NULL,
        StdAccountName    NVARCHAR(200)  NOT NULL,
        Section           NVARCHAR(30)   NOT NULL,   -- Assets | Liabilities | Intercompany | Shareholders | Equity | Revenue | Clinical costs | Overhead | Other
        Notes             NVARCHAR(400)  NULL,
        UpdatedAt         DATETIME2      NOT NULL CONSTRAINT DF_StdAccounts_UpdatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_StdAccounts PRIMARY KEY (StdAccountNumber)
    );
END
GO

IF OBJECT_ID('qb.AccountMap', 'U') IS NULL
BEGIN
    CREATE TABLE qb.AccountMap (
        CompanyID         INT            NOT NULL,
        AccountNumber     NVARCHAR(200)  NOT NULL,   -- as in qb.ChartOfAccounts (QB 'Accnt. #')
        StdAccountNumber  NVARCHAR(10)   NOT NULL,
        Confidence        NVARCHAR(10)   NULL,       -- High | Review | Reviewed
        Notes             NVARCHAR(400)  NULL,
        ReviewerNotes     NVARCHAR(400)  NULL,
        SourceFile        NVARCHAR(400)  NULL,
        LoadedAt          DATETIME2      NOT NULL CONSTRAINT DF_AccountMap_LoadedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_AccountMap PRIMARY KEY (CompanyID, AccountNumber),
        CONSTRAINT FK_AccountMap_Companies FOREIGN KEY (CompanyID) REFERENCES qb.Companies(CompanyID),
        CONSTRAINT FK_AccountMap_Std FOREIGN KEY (StdAccountNumber) REFERENCES qb.StdAccounts(StdAccountNumber)
    );
END
GO

-- ------------------------------------------------------------
-- qb.vw_ChartOfAccounts: every account with its company prefix and
-- standard account - use this instead of qb.ChartOfAccounts in reports.
--   DNTL-10000 · Scotiabank   ->  1010 Bank - operating
-- ------------------------------------------------------------
IF OBJECT_ID('qb.vw_ChartOfAccounts', 'V') IS NOT NULL
    DROP VIEW qb.vw_ChartOfAccounts;
GO

CREATE VIEW qb.vw_ChartOfAccounts AS
SELECT
    c.CompanyCode,
    UPPER(c.CompanyCode) + '-' + coa.AccountNumber                          AS QualifiedNumber,
    UPPER(c.CompanyCode) + '-' + coa.AccountNumber + N' · ' + coa.AccountName AS QualifiedAccount,
    coa.AccountNumber,
    coa.AccountName,
    coa.AccountType,
    sa.StdAccountNumber,
    sa.StdAccountName,
    sa.Section,
    am.Confidence                                                             AS MapConfidence,
    coa.ChartAccountID,
    coa.CompanyID
FROM qb.ChartOfAccounts coa
JOIN qb.Companies c          ON c.CompanyID = coa.CompanyID
LEFT JOIN qb.AccountMap am   ON am.CompanyID = coa.CompanyID AND am.AccountNumber = coa.AccountNumber
LEFT JOIN qb.StdAccounts sa  ON sa.StdAccountNumber = am.StdAccountNumber;
GO

-- ------------------------------------------------------------
-- qb.vw_LedgerStd: every QuickBooks line with its prefixed account and
-- standard account - the base for consolidated / cross-company reports.
--   SELECT StdAccountNumber, StdAccountName, CompanyCode, SUM(Amount)
--   FROM qb.vw_LedgerStd WHERE TxnDate BETWEEN '2024-09-01' AND '2025-08-31'
--   GROUP BY StdAccountNumber, StdAccountName, CompanyCode;
-- ------------------------------------------------------------
IF OBJECT_ID('qb.vw_LedgerStd', 'V') IS NOT NULL
    DROP VIEW qb.vw_LedgerStd;
GO

CREATE VIEW qb.vw_LedgerStd AS
SELECT
    v.CompanyCode,
    je.TxnDate,
    je.TxnType,
    je.RefNum,
    je.Name,
    COALESCE(jel.LineMemo, je.Memo) AS Memo,
    jel.Amount,
    v.QualifiedAccount,
    v.StdAccountNumber,
    v.StdAccountName,
    v.Section,
    jel.LineID,
    je.JournalEntryID
FROM qb.JournalEntryLines jel
JOIN qb.JournalEntries je       ON je.JournalEntryID = jel.JournalEntryID
JOIN qb.vw_ChartOfAccounts v    ON v.ChartAccountID = jel.ChartAccountID;
GO

-- ------------------------------------------------------------
-- qb.vw_UnmappedAccounts: accounts with no standard account yet
-- (e.g. new accounts created in QuickBooks since the mapping was loaded).
-- ------------------------------------------------------------
IF OBJECT_ID('qb.vw_UnmappedAccounts', 'V') IS NOT NULL
    DROP VIEW qb.vw_UnmappedAccounts;
GO

CREATE VIEW qb.vw_UnmappedAccounts AS
SELECT v.CompanyCode, v.QualifiedAccount, v.AccountType,
       (SELECT COUNT(*) FROM qb.JournalEntryLines jel WHERE jel.ChartAccountID = v.ChartAccountID) AS LineCount
FROM qb.vw_ChartOfAccounts v
WHERE v.StdAccountNumber IS NULL;
GO

-- ============================================================
-- Bank pipeline (bank_pipeline.py) additions - all guarded / nullable
-- ============================================================
IF COL_LENGTH('fin.Statements', 'TotalDeposits') IS NULL
    ALTER TABLE fin.Statements ADD
        TotalDeposits     DECIMAL(14,2)  NULL,
        TotalWithdrawals  DECIMAL(14,2)  NULL;
GO

IF COL_LENGTH('fin.Transactions', 'SubDescription') IS NULL
    ALTER TABLE fin.Transactions ADD
        SubDescription  NVARCHAR(400)  NULL,   -- payer / payee from the bank export
        ChequeNumber    NVARCHAR(20)   NULL,
        Tag             NVARCHAR(200)  NULL,   -- your own label from an extra export column (e.g. 'hyg', 'split')
        BankOrder       INT            NULL;   -- the bank's order within the export (running balances chain in this order)
GO

-- ------------------------------------------------------------
-- fin.TransactionSplits: your allocation columns for a line, e.g. a
-- 'split' cheque = salary + rent + lease + bookkeeping + visa.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.TransactionSplits', 'U') IS NULL
BEGIN
    CREATE TABLE fin.TransactionSplits (
        SplitID        BIGINT IDENTITY(1,1) NOT NULL,
        TransactionID  BIGINT               NOT NULL,
        Category       NVARCHAR(50)         NOT NULL,
        Amount         DECIMAL(14,2)        NOT NULL,
        CONSTRAINT PK_TransactionSplits PRIMARY KEY (SplitID),
        CONSTRAINT FK_TransactionSplits_Transactions FOREIGN KEY (TransactionID)
            REFERENCES fin.Transactions(TransactionID) ON DELETE CASCADE
    );
    CREATE INDEX IX_TransactionSplits_Txn ON fin.TransactionSplits(TransactionID);
END
GO

-- ------------------------------------------------------------
-- fin.vw_AccountTransfers: money leaving one account and arriving in
-- another of the group's accounts - same amount, within 5 days.
-- Shows the intercompany flows straight from the bank data, e.g.
--   DNTL cheque 24 (-19,148.23)  ->  HYG deposit (+19,148.23), same day
--   HYG 'Pc-Td Visa' bill payment ->  TD Visa 6761 payment
-- Candidates only (two different $500 transfers on the same days would
-- pair up both ways) - amounts under $100 are left out to cut noise.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.vw_AccountTransfers', 'V') IS NOT NULL
    DROP VIEW fin.vw_AccountTransfers;
GO

CREATE VIEW fin.vw_AccountTransfers AS
SELECT
    fa.AccountName                         AS FromAccount,
    t1.TransactionDate                     AS FromDate,
    t1.Description                         AS FromDescription,
    t1.SubDescription                      AS FromDetail,
    t1.ChequeNumber,
    t1.Tag                                 AS FromTag,
    -t1.Amount                             AS Amount,
    ta.AccountName                         AS ToAccount,
    COALESCE(t2.PostingDate, t2.TransactionDate) AS ToDate,
    t2.Description                         AS ToDescription,
    t2.SubDescription                      AS ToDetail,
    DATEDIFF(DAY, t1.TransactionDate, COALESCE(t2.PostingDate, t2.TransactionDate)) AS DaysApart,
    t1.TransactionID                       AS FromTransactionID,
    t2.TransactionID                       AS ToTransactionID
FROM fin.Transactions t1
JOIN fin.Accounts fa ON fa.AccountID = t1.AccountID AND fa.AccountType = 'Bank'
JOIN fin.Transactions t2
     ON t2.AccountID <> t1.AccountID
    AND ABS(DATEDIFF(DAY, t1.TransactionDate, COALESCE(t2.PostingDate, t2.TransactionDate))) <= 5
JOIN fin.Accounts ta ON ta.AccountID = t2.AccountID
WHERE t1.Amount <= -100
  AND (   (ta.AccountType = 'Bank' AND t2.Amount = -t1.Amount)                      -- bank -> bank: deposit
       OR (ta.AccountType = 'Visa' AND t2.Amount =  t1.Amount AND t2.TxnType = 'payment'));   -- bank -> card: payment
GO

-- ============================================================
-- Monthly split (split_pipeline.py) + findings board
-- ============================================================

-- ------------------------------------------------------------
-- fin.Findings: one row per thing someone should look at.
-- Written by the pipelines today (Source = 'split', ...); designed as
-- the shared message board for future agents (a Dental agent, a
-- Hygiene agent, ...): each posts what it finds for its company and
-- reads what the others posted.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.Findings', 'U') IS NULL
BEGIN
    CREATE TABLE fin.Findings (
        FindingID    BIGINT IDENTITY(1,1) NOT NULL,
        Source       NVARCHAR(50)   NOT NULL,   -- 'split', later 'dental_agent', 'hygiene_agent', ...
        RunKey       NVARCHAR(100)  NOT NULL,   -- e.g. 'FY2025' - a rerun replaces its own findings
        Company      NVARCHAR(20)   NULL,       -- 'dntl' | 'hyg' | 'mgmt'
        Subject      NVARCHAR(100)  NOT NULL,   -- e.g. 'Split Mar 2025'
        Area         NVARCHAR(30)   NOT NULL,   -- 'cash' | 'expense' | 'rate' | 'link' | 'qb' | 'lab' ...
        Severity     NVARCHAR(10)   NOT NULL,   -- 'Info' | 'Warning' | 'Issue'
        Message      NVARCHAR(1000) NOT NULL,
        Amount       DECIMAL(14,2)  NULL,
        RefTable     NVARCHAR(50)   NULL,
        RefID        BIGINT         NULL,
        Status       NVARCHAR(20)   NOT NULL CONSTRAINT DF_Findings_Status DEFAULT ('Open'),
        CreatedAt    DATETIME2      NOT NULL CONSTRAINT DF_Findings_CreatedAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_Findings PRIMARY KEY (FindingID)
    );
    CREATE INDEX IX_Findings_Source_Run ON fin.Findings(Source, RunKey);
END
GO

-- ------------------------------------------------------------
-- fin.SplitMonths: one row per sheet of the split workbook, with the
-- bookkeeper's numbers (as is) and what the pipeline found.
-- ------------------------------------------------------------
IF OBJECT_ID('fin.SplitMonths', 'U') IS NULL
BEGIN
    CREATE TABLE fin.SplitMonths (
        SplitMonthID        INT IDENTITY(1,1) NOT NULL,
        FiscalYearEnd       SMALLINT       NOT NULL,
        SheetName           NVARCHAR(30)   NOT NULL,
        MonthStart          DATE           NOT NULL,
        SourceFile          NVARCHAR(400)  NULL,
        -- the workbook, as is
        ChequeTotal         DECIMAL(14,2)  NOT NULL,   -- C8
        VisaDue             DECIMAL(14,2)  NOT NULL,   -- B6
        VisaDPC             DECIMAL(14,2)  NOT NULL,   -- C6
        VisaHygiene         DECIMAL(14,2)  NOT NULL,   -- B6 - C6
        LabFees             DECIMAL(14,2)  NOT NULL,   -- D6
        -- links
        DntlChequeNumber    NVARCHAR(20)   NULL,
        DntlChequeDate      DATE           NULL,
        HygDepositDate      DATE           NULL,
        CardStatementDate   DATE           NULL,
        -- cash test: what Hygiene actually paid TD for this statement
        BankPaid            DECIMAL(14,2)  NULL,
        CashDiff            DECIMAL(14,2)  NULL,       -- VisaDue - BankPaid (booked as paid in QB but not paid)
        -- expense test: the cycle's own charges
        ExpenseBase         DECIMAL(14,2)  NULL,       -- new balance - previous balance + bank payments
        LabOnCard           DECIMAL(14,2)  NULL,
        CorrectVisaDPC      DECIMAL(14,2)  NULL,
        CorrectVisaHygiene  DECIMAL(14,2)  NULL,
        DPCDiff             DECIMAL(14,2)  NULL,       -- CorrectVisaDPC - VisaDPC (+ = Dental under-charged)
        -- QuickBooks
        QBChequeFound       BIT            NULL,
        QBJournalFound      BIT            NULL,
        Status              NVARCHAR(30)   NULL,
        RunAt               DATETIME2      NOT NULL CONSTRAINT DF_SplitMonths_RunAt DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_SplitMonths PRIMARY KEY (SplitMonthID),
        CONSTRAINT UQ_SplitMonths UNIQUE (FiscalYearEnd, SheetName)
    );
END
GO

IF OBJECT_ID('fin.SplitLines', 'U') IS NULL
BEGIN
    CREATE TABLE fin.SplitLines (
        SplitLineID    INT IDENTITY(1,1) NOT NULL,
        SplitMonthID   INT            NOT NULL,
        Category       NVARCHAR(30)   NOT NULL,   -- salary | rent | lease | bookkeeping | visa
        Total          DECIMAL(14,2)  NOT NULL,   -- column B
        DPCPortion     DECIMAL(14,2)  NOT NULL,   -- column C, as is
        Rate           DECIMAL(9,4)   NULL,       -- C / B
        Formula        NVARCHAR(200)  NULL,
        QBAccount      NVARCHAR(250)  NULL,       -- Dental account the cheque line was posted to in QB
        QBAmount       DECIMAL(14,2)  NULL,
        CONSTRAINT PK_SplitLines PRIMARY KEY (SplitLineID),
        CONSTRAINT FK_SplitLines_Months FOREIGN KEY (SplitMonthID) REFERENCES fin.SplitMonths(SplitMonthID) ON DELETE CASCADE
    );
END
GO

IF OBJECT_ID('fin.SplitLabItems', 'U') IS NULL
BEGIN
    CREATE TABLE fin.SplitLabItems (
        SplitLabItemID   INT IDENTITY(1,1) NOT NULL,
        SplitMonthID     INT            NOT NULL,
        Description      NVARCHAR(200)  NULL,
        Amount           DECIMAL(14,2)  NOT NULL,
        CardTransactionID BIGINT        NULL,      -- the matching line on a card statement, if found
        CardDate         DATE           NULL,
        CONSTRAINT PK_SplitLabItems PRIMARY KEY (SplitLabItemID),
        CONSTRAINT FK_SplitLabItems_Months FOREIGN KEY (SplitMonthID) REFERENCES fin.SplitMonths(SplitMonthID) ON DELETE CASCADE
    );
END
GO
