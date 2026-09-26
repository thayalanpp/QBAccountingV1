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
