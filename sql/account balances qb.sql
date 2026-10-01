SELECT a.AccountNumber, b.FiscalYearEnd, b.OpeningBalance, b.ClosingBalance
FROM qb.AccountBalances b JOIN qb.ChartOfAccounts a ON a.ChartAccountID = b.ChartAccountID
JOIN qb.Companies c ON c.CompanyID = b.CompanyID
WHERE c.CompanyCode = 'dntl' AND a.AccountNumber IN ('10000', '22200') ORDER BY 1, 2;