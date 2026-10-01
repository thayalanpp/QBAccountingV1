SELECT c.AccountNumber, c.AccountName,
       SUM(CASE WHEN je.TxnDate BETWEEN '2023-09-01' AND '2024-08-31' THEN l.Amount ELSE 0 END)
         * CASE WHEN c.AccountType LIKE '%Income%' THEN -1 ELSE 1 END AS FY2024,
       SUM(CASE WHEN je.TxnDate BETWEEN '2024-09-01' AND '2025-08-31' THEN l.Amount ELSE 0 END)
         * CASE WHEN c.AccountType LIKE '%Income%' THEN -1 ELSE 1 END AS FY2025
FROM qb.JournalEntryLines l
JOIN qb.JournalEntries je ON je.JournalEntryID = l.JournalEntryID
JOIN qb.ChartOfAccounts c ON c.ChartAccountID = l.ChartAccountID
JOIN qb.Companies co      ON co.CompanyID = je.CompanyID
WHERE co.CompanyCode = 'hyg'
  AND c.AccountType IN ('Income', 'Expense', 'Cost of Goods Sold', 'Other Income', 'Other Expense')
GROUP BY c.AccountNumber, c.AccountName, c.AccountType
ORDER BY c.AccountNumber;