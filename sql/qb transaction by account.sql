SELECT c.CompanyCode, coa.AccountName, je.TxnDate, je.Name, je.Memo, jel.Amount
FROM qb.JournalEntryLines jel
JOIN qb.JournalEntries je   ON je.JournalEntryID = jel.JournalEntryID
JOIN qb.ChartOfAccounts coa ON coa.ChartAccountID = jel.ChartAccountID
JOIN qb.Companies c         ON c.CompanyID = je.CompanyID
WHERE (coa.AccountNumber = '22200' AND c.CompanyCode = 'dntl')
   OR (coa.AccountNumber = '2360'  AND c.CompanyCode = 'mgmt')
ORDER BY c.CompanyCode, je.TxnDate;