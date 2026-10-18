# QBAccountingV1 - handoff (2026-10-18)

Paste this at the start of a new chat. It is the project's message board: decisions, facts, findings, proposals, open tasks and questions, posted by the agents below. Full detail lives in SQL (agent.Messages, fin.Findings) and in the review workbooks.

**Agents:** `owner` - Thayalan - owner, decision maker; `build_agent` - Developer who built the QBAccountingV1 application; `dental_agent` - Bookkeeper for S. Viswanathan Dentistry Professional Corporation (DNTL); `hygiene_agent` - Bookkeeper for DHM Dental Hygiene Technical Services Corporation (HYG); `mgmt_agent` - Bookkeeper for the Management company (MGMT)

## Context

### Entities, accounts and fiscal year
*owner · Fact*

- Fiscal year: **Sep 1 - Aug 31**, named by the year it ends (FY2025 = Sep 1 2024 - Aug 31 2025).
- **DNTL** S. Viswanathan Dentistry Professional Corporation - Scotiabank 26492-00281-18 (QB 10000), TD Aeroplan Visa 6761 (QB 22200 "Credit Card-NM interco").
- **HYG** DHM Dental Hygiene Technical Services Corporation - Scotiabank 26492-00688-10 (QB 1010 and sub-account 1010.25 "Post smitha" = the new start in 2021).
- **MGMT** Dental & Hygiene Management - TD 0561-5208812 (QB 1010).
- The TD Visa is Dental's card but is **paid from Hygiene's bank**; Dental reimburses Hygiene with a monthly "split" cheque. Management receives $2,500 cheques from Dental.
- SQL Server: NilashaAI, database QBAccounting. Repo: github.com/thayalanpp/QBAccountingV1 (tag v0.3-baseline).

## Current state

### What is loaded (as of the handoff)
*build_agent · Status · build*

| | FY2024 | FY2025 |
|---|---|---|
| QB DNTL | loaded, **proven vs P&L** (income 560,457.31, expense 505,289.03) | loaded |
| QB HYG | loaded | loaded |
| Bank DNTL | 943 lines, all 12 statements agree, 943/943 match QB | 1,013 lines, all statements agree, 1,013/1,013 match QB |
| Bank HYG | 326 lines, all statements agree, 246/326 match QB | 330 lines, all statements agree, 326/330 match QB |
| TD Visa | statements Aug 2023 - Sep 2025 (26), all valid | |
| GL balances DNTL | from the multi-year GL - all checkpoints and movements agree | same |
| Split review | run (12/12 OK); 22200 roll-forward 15,772.83 -> 15,896.51, unexplained 0.00 | run; 22200 roll-forward 15,896.51 -> 35,669.96, unexplained 0.00 |
Not loaded: FY2021-2023, FY2026, Management.

### Group chart of accounts
*dental_agent · Status · coa*

Group_Chart_of_Accounts_Mapping.xlsx maps 417 QB accounts (DNTL 64, HYG 143, MGMT 210) to a 109-account standard chart with company prefixes (DNTL-10000 ...); 71 rows need review; tax lines to be confirmed by the accountant. Load with load_account_map.py after review (status: not yet reported as loaded).

## Decisions made

### Split workbook is read as is
*owner · Decision · split*

NM_<year>.xlsx (one sheet per month, rows 2-8: salary, rent, lease, bookkeeping, visa; B = total, C = Dental's (DPC) portion, C8 = the split cheque, D6 = lab fees on the card at 100%) is read **as is**. The years are closed; differences become proposed adjustments, never edits to the workbook.

### Where the split is posted
*owner · Decision · split · dntl*

All of Dental's split portions go directly to **Dental expense accounts**. Hygiene's portion of the Visa (B6 - C6) is recorded in Dental's books as the "hygiene supplies" journal (Dr 22200 / Cr 64400 Dental Supplies) so the card account balances.
A missed Visa payment rolls to the next month; **B6 must be checked against the Visa transactions**.

### QuickBooks: one fiscal year per export
*owner · Decision · qb*

Multi-year Transaction Detail / General Ledger exports keep income & expense rows only for the **last** fiscal year in the range, so QuickBooks is exported **one fiscal year per file** (C:\NM\quickbooks\<co>\qb ... {year} - transaction detail.xlsx). The account list doesn't change and is reused. The multi-year GL is still useful for balance-sheet balances.

### Folder layout
*owner · Decision · build*

C:\NM\<year>yearend\dntl\, \hyg\, \mgmt\ - each company's bank export(s) AND its monthly statement PDFs, directly in the folder.
C:\NM\<year>yearend\visa\ - TD Visa statements. C:\NM\<year>yearend\NM_<year>.xlsx - split workbook. QuickBooks per-year files in C:\NM\quickbooks\<co>\.

### Clean-up entries go into a backup copy first
*owner · Decision · cleanup*

The end product of the analysis is a set of **QuickBooks journal entries**. They are posted to a **backup copy** of the QuickBooks file first; the accountant decides what goes into the real books (FY2023 and earlier are filed).

### Agents and this board
*owner · Decision · build*

Future design: a **dental_agent** and a **hygiene_agent** (mgmt later) - each with this project's knowledge - check that each company is charged correctly and collaborate through this board. The AI fallback for PDF reading is on hold; rule-based parsers are used.

### Lopez charges are Hygiene's
*owner · Decision · split · dntl · FY2025*

The Lopez Senathi & Associates charges on the card (2,486.00 + 2,062.25 = **4,548.25**, Mar 2025 cycle) are **Hygiene's**. The bookkeeper moved them to a Hygiene account and they were paid by two separate payments on Feb 12 2025; they are correctly left out of Dental's QuickBooks.
They should not have been in the March 2025 split, so Dental's 80% of them (**3,638.60**) is part of what Hygiene owes Dental (JE-5).

### July 2025 credits of 1,936.41 treated as Hygiene's payments
*owner · Decision · split · dntl · FY2025*

The July 2025 card payment credits that did not come from Hygiene's bank (1,664.48, 152.55, 84.05, 35.33 = **1,936.41**) are **assumed to be Hygiene's payments** for now, at the owner's direction.
Effect: they count as real payments to TD, so they are not "booked but not paid". This is exactly why the figures dropped: booked-not-paid 19,613.76 -> **17,677.35** (-1,936.41) and Dental over-charged 15,691.00 -> **14,141.87** (-1,549.13 = 80%).
If they prove to be personal payments, Dental's over-charge rises by 1,549.13 and JE-5 changes; revisit before the accountant finalises JE-5.

### Visa split from now on: 80/20 monthly, one year-end lab adjustment
*owner · Decision · split · FY2026*

Agreed with the bookkeeper (Shaju Paul CPA):
- Each month split the Visa **80% Dental / 20% Hygiene**, using only that statement's **own new charges**: never the full balance, never charges paid separately, net of credits and refunds.
- **One year-end adjustment** moves Hygiene's 20% of lab fees (100% Dental items) back to Dental. Lab vendors are posted to a dedicated "Lab fees - Visa" account so the total is ready at year-end.
- The lab-fee allocation for FY2024/FY2025 (a further **3,967.90** if the unlisted D6 amounts are not labs) is a separate matter, **not part of the 22200 fix**.

### Aug 2024 split accrual is outside the 22200 fix
*owner · Decision · cleanup · dntl · FY2024*

The Aug 2024 sheet's cheque (C8 9,525.04, cheque 23, paid Sep 3 2024) means Dental owed Hygiene 9,525.04 at Aug 31 2024 with nothing recorded. That accrual is a **22100 / expense cut-off matter**, not a 22200 error, and is **not** part of JE-1 to JE-7.
(The 22200 effect of year-end cheque timing - about 1,617.66 net - is one of the parts of the remaining 1,600.96; see "FY2025: 1,600.96 still to explain".)

## build_agent - how the application works

### Application: pipelines and files
*build_agent · Fact · build*

All rule-based Python + SQL Server; LangGraph orchestrates each pipeline; nothing is ever written to QuickBooks.
| Pipeline | Files | Does |
|---|---|---|
| QuickBooks | run_qb_load.py, qb_loader.py | Loads one company x one FY from the Transaction Detail export; replaces the year; balance-checks every account section; stores opening/closing in qb.AccountBalances |
| TD Visa | visa_pipeline.py, td_visa_parser.py, statement_validator.py, visa_sql.py, qb_matcher.py | Parses statement PDFs, 10 checks (summary math, totals by type, chaining...), saves, matches to QB 22200 |
| Banks | bank_pipeline.py, bank_readers.py, bank_validator.py, bank_sql.py, bank_models.py | Scotiabank/TD exports (Excel/CSV), statement PDFs as known balances, monthly periods, matches to QB bank accounts, fin.vw_AccountTransfers |
| Split | split_pipeline.py, split_reader.py, split_assess.py, split_sql.py, rollforward.py | Reads NM_<year>.xlsx, links cheques/deposits/card, cash & expense tests, QB postings, boundary months, 22200 roll-forward, cut-off; writes split_review_FY<year>.xlsx |
| GL balances | load_gl_balances.py | Reads the multi-year GL (dntl: C:\NM\quickbooks\dntl\qb sv gl 2020-2026.xlsx); balance-sheet accounts' balance at every Aug 31, export sign (debit +); refuses to save unless every section re-derives, the known balances agree and each year's movement equals the one-year load; upserts qb.AccountBalances |
| Chart of accounts | load_account_map.py | Loads the reviewed group chart mapping (qb.StdAccounts / qb.AccountMap) |
sql/schema.sql is applied automatically at the start of every pipeline. sql/reset_test_data.sql clears loaded data.

### Run order for a year
*build_agent · Fact · build*

```
python run_qb_load.py   --fy-end <Y> --only dntl      (and --only hyg)
python load_gl_balances.py --company dntl [--last-fy <Y>]
python visa_pipeline.py --fy-end <Y>
python bank_pipeline.py --fy-end <Y> --only dntl,hyg
python split_pipeline.py --fy-end <Y>
python board_seed.py                                    (board posts, incl. the JE-1..JE-7 proposal)
python proposed_entries.py seed                         (fin.ProposedEntries / Lines)
python proposed_entries.py proforma --check             (fin.vw_ProForma22200 vs the report)
python load_yearend.py --all                            (M&Co AJEs + closing TB, after the QB loads)
```
Reloading QuickBooks gives its lines new IDs - **always rerun visa and bank after a QB reload**. A QB reload also rewrites that year's qb.AccountBalances with a 0 opening - **always rerun load_gl_balances.py after it**. Every pipeline replaces its own year (never duplicates); a failed validation saves nothing.

### QuickBooks export quirks
*build_agent · Fact · qb*

- Multi-year exports drop income/expense except the final year -> export one FY per file.
- The one-year Transaction Detail report starts 22200's running balance at **0** (no balance brought forward) -> run_qb_load.py stores a 0 opening for balance-sheet accounts; load_gl_balances.py replaces it with the multi-year GL's balances (rerun after every QB reload).
- DNTL QuickBooks was booked only up to **Sep 29, 2025**; the multi-year GL exported Oct 2026 has rows dated up to **Aug 31, 2026** - confirm whether FY2026 is fully booked before using its balances.
- qb_loader refuses a reload writing < 90% of the lines the year already holds (shrink guard). run_qb_load.py has a custom COMPANIES block - don't overwrite it.

### Bank data quirks
*build_agent · Fact · bank*

- Scotiabank exports go back only **18 months**; FY2024 came from the bookkeeper's workbook (sheets dntl / hyg): amounts unsigned (direction from Debit/Credit) and **rows re-sorted** - the reader rebuilds the bank's order from the balances (year +/- 14 days) and reports rows that don't fit (DNTL FY2024 row 69 = duplicate, deleted).
- Monthly statement PDFs in the company folder are read for opening/closing balances and checked against the export.
- FY2025 DNTL file = bookkeeper's tagged file + bank export combined (DNTL_8118_FY2025_combined.xlsx); tags and allocation columns (Salary, rent, lease, bookkeping, visa) are stored in fin.TransactionSplits.
- MGMT TD export: headers credit/debit swapped (reader uses the balance), ends Aug 15 2025 - not loaded.

### TD Visa parser notes
*build_agent · Fact · visa*

Reads page 1's two columns separately; dates get their year from the statement period; posting date must be in the period. Handles cash advances in English/French, the pre-2024 "NET AMOUNT OF MONTHLY ACTIVITY" subtotal, one-line foreign currency, and lines TD counts as cash advances without saying so (unique combination that makes up the printed total - recorded as a note).

### Split review rules
*build_agent · Fact · split*

- Cash test: B6 vs Hygiene's payments to TD ("td visa" in its bank) after the sheet's statement, before the next.
- Expense test: base = new balance - previous balance + real payments; a card payment is real only if the matching bank withdrawal is itself a payment to the card; payments outside the loaded bank data are assumed real. Correct DPC = (base - lab) x 0.8 + lab.
- QB postings: cheque found by number, or by amount and date when the bank has no numbers (FY2024); 2-cent rounding tolerance.
- Boundary months (last month of the previous year, first of the next) come from the neighbouring NM_<year>.xlsx and are kept out of the totals.

### Proposed entries: tables, approval and IIF
*build_agent · Fact · cleanup*

| Piece | Does |
|---|---|
| fin.ProposedEntries / fin.ProposedEntryLines | One row per entry (JE-1 ... TM-1R) and its legs, debit + / credit -, company QB account numbers; undecided accounts keep AccountNumber NULL + AccountHint |
| fin.vw_ProposedEntries | Per entry: balanced?, undecided lines, ReadyToApprove, effect on 22200 |
| fin.vw_ProForma22200 | Dental 22200 at each Aug 31: QB (multi-year GL), + proposed entries (all / approved only), vs card owed that day |
| proposed_entries.py | `seed` (rerunnable; Draft entries are rewritten, Approved/Posted are never touched), `list`, `set-account`, `approve`, `reject`, `mark-posted`, `proforma --check` |
| iif_export.py | Writes **Approved** entries only to a QuickBooks Desktop IIF (accounts by full name from qb.ChartOfAccounts; creates 39900 via !ACCNT; JE-6 as a Credit Card Charge); refuses unbalanced or undecided entries; stamps IIFExportedAt |
Status flow: Draft -> Approved -> Posted (in the backup copy), or Rejected / Superseded. Dates are written MM/DD/YYYY by default (`--date-format dmy` if QuickBooks reads day first) - check one entry in the register after the import.

### Accountant's year-end files: tables, loader and views
*build_agent · Fact · yearend*

M&Co's adjusting entries never reach QuickBooks, so their figures are loaded alongside the QuickBooks data to compare year by year.
| Piece | Does |
|---|---|
| fin.YearEndAJE | M&Co's AJE lines (Debit / Credit positive; IsRollForward = "to record last year adjustment") |
| fin.YearEndTB | M&Co's closing TB: Prelim + Adj + Reclass = Final, debit + / credit -, with the FS line |
| load_yearend.py | `--company dntl --fy 2025` or `--all`; replaces company x year; refuses to save unless AJEs balance, TB columns sum to 0, every row adds up and TB Adj = AJE net per account. Reports PriorYearFinal continuity, Prelim vs QuickBooks, and M&Co-only accounts |
| fin.vw_YearEndTB_vs_QB | Each TB line vs QuickBooks at the year end (balance sheet: AccountBalances closing; P&L: the year's activity) |
| fin.vw_YearEndCarryForward | Balance-sheet Final vs QuickBooks closing: the cumulative adjustment never posted to QuickBooks |
| fin.vw_YearEndFSLines | Final by statement line, plus a Net income row, to compare with the PDF statements |
Files: C:\NM\<FY>yearend\<co>\accountant\YearEndTB_<co>_<FY>.csv and YearEndAJE_<co>_<FY>.csv (CSVs prepared from the PDFs; the loader does not read PDFs).
FY2025 Dental: 36 TB rows, 6 AJEs (17 lines, 209,368.21 each side), CC group Final 25,137.22, net income 47,997.35. No IIF of AJEs until it is decided whether to record them.

## dental_agent - facts and findings

### 22200 vs the real card balance ⚑
*dental_agent · Finding · rollforward · dntl · FY2025*

| | Aug 31 2023 | Aug 31 2024 | Aug 31 2025 |
|---|---|---|---|
| Owed per QB 22200 | -9,502.48 | -12,466.03 | -35,260.97 |
| Owed per TD statements | 6,270.35 | 3,430.48 | 408.99 |
| QB understates the card by | 15,772.83 | 15,896.51 | 35,669.96 |
FY2024 moved the gap only **+123.68** (wrong-sign entries). FY2025 added **19,773.45**. The 15,772.83 predates FY2024 (FY2021-2023, unexplained until those years are loaded).

_Evidence: split_review_FY2025.xlsx sheet 22200 roll-forward (opens from the GL balance; unexplained 0.00)_

### FY2025 split: Hygiene owes Dental 14,141.87 ⚑
*dental_agent · Finding · split · dntl · FY2025*

Replaces the earlier Dental-visa-expense figure of **-15,691.00**. Dental was over-charged through the split cheques by **14,141.87**:
| Cause | Split in error | Dental over-charged (80%) | Hygiene's 20% |
|---|---|---|---|
| Issue 1 - past balance split again: Apr 2025 (7,151.69), Aug 2025 (5,709.28) | 12,860.97 | 10,288.77 | 2,572.20 |
| Issue 2 - Lopez 4,548.25 (Mar), interest credit not deducted 245.54 (Mar), Jul 22.59; 599.00 fee Mar/Jun nets 0 | 4,816.38 | 3,853.10 | 963.28 |
| **Total** | **17,677.35** | **14,141.87** | **3,535.48** |
Difference from 15,691.00 = 1,549.13 = 80% of the 1,936.41 July credits now treated as Hygiene's. FY2024 needs no split correction.

_Replaces: FY2025 split: B6 overstated after missed payments (superseded)_

_Evidence: Reconciliation report Issues 1-2; split_review_FY2025.xlsx_

### FY2025 split: 17,677.35 booked as paid on 22200 but never paid to TD ⚑
*dental_agent · Finding · split · dntl · FY2025*

Replaces the earlier cash figure of **19,613.76**. The split cheques' visa portions plus the hygiene-supplies journals recorded **17,677.35** of card payments TD never received (Issue 1 12,860.97 + Issue 2 4,816.38). Reversing them is **JE-5** (Cr 22200 17,677.35; Dr 22100 Due from Hygiene 14,141.87; Dr 64400 Dental Supplies 3,535.48).
Difference from 19,613.76 = 1,936.41, the July credits now treated as Hygiene's payments. Over FY2025 Hygiene paid TD exactly the cycles' own charges (90,910.52); only the bookkeeping went beyond that.

_Replaces: FY2025 split: B6 overstated after missed payments (superseded)_

_Evidence: Reconciliation report Issue 1-2 tables; JE-5_

### FY2025: 1,600.96 still to explain after JE-1 to JE-7 ⚑
*dental_agent · Finding · rollforward · dntl · FY2025*

Replaces the earlier working figure of **1,513.66**, which did not allow for the 87.30 FY2024 timing item reversing in FY2025 (1,513.66 + 87.30 = 1,600.96).
After all proposed entries 22200 = **2,009.95 Cr** at Aug 31 2025 vs the card's **408.99** (Aug 31 2024 lands exactly on 3,430.48).
| FY2025 roll-forward line | Roll-forward | Covered by entries | Left |
|---|---|---|---|
| Payments booked vs made | 15,510.10 | 17,677.35 (JE-5) | 2,167.25 |
| Card activity not in QB | 4,350.15 | 3,609.76 (JE-6, JE-7) | -740.39 |
| QB card entries with no card line | 174.10 | none yet | 174.10 |
| **Total** | | | **1,600.96** |
The 2,167.25 looks like timing/treatment, not new errors: split payments booked after the year-end they belong to (~1,617.66 net, Aug 2024 / Aug 2025 sheets), the Lopez 2,486.00 payment from a group bank account, and the 1,936.41 July credits. Confirm before JE-5 is final - part may belong in FY2026.

_Replaces: an earlier working figure (un-seeded post)_

_Evidence: fin.vw_ProForma22200 (FiscalYearEnd 2025, ProFormaOverCard); reconciliation report Proof_

### Dental's key accounts: purpose and balances
*dental_agent · Fact · qb · dntl*

From the multi-year GL (debit +):
| Account | Purpose | Aug 31 2023 | Aug 31 2024 | Aug 31 2025 |
|---|---|---|---|---|
| 10000 Scotiabank | Dental's bank - agrees with the bank on all three dates | 13,263.95 | 65,468.68 | 13,103.87 |
| 22200 Credit Card-NM interco | TD Visa 6761 | 9,502.48 Dr | 12,466.03 Dr | 35,260.97 Dr |
| 22100 NM Hygiene interco | Dental owes Hygiene for supplies (GJ 27, Aug 31 2022) - dormant | 7,646.88 Cr | 7,646.88 Cr | 7,646.88 Cr |
| 22150 Interco Management | Cheque 129 to Management, May 3 2023 - dormant | 12,500.00 Dr | 12,500.00 Dr | 12,500.00 Dr |
The monthly allocations do **not** go through 22100/22150.

_Evidence: multi-year GL: qb sv gl 2020-2026.xlsx_

### How 22200 works
*dental_agent · Fact · qb · dntl*

Credited with every card charge (expensed 100% in Dental). Debited by (1) the split cheque's **visa portion** (Dental's share, paid to Hygiene, which paid TD) and (2) the monthly **"hygiene supplies" journal** (Hygiene's share, other side **Cr 64400 Dental Supplies**). 22200 equals the real card only if (1)+(2) = what was actually paid to TD. Each cheque is written about a month after its sheet.

### FY2024 split: clean
*dental_agent · Finding · split · dntl · FY2024*

12/12 months: B6 = what Hygiene paid TD = the cycle's own charges. No carried balances. Only item: the Sep 2023 cheque was 13,028.47 vs C8 13,023.47 (**$5.00** over).

### FY2025 roll-forward: the 19,773.45 itemized
*dental_agent · Finding · rollforward · dntl · FY2025*

- Payments booked vs made: journals 8,056.37 + cheque visa portions 100,850.25 booked vs 93,396.52 paid = **-15,510.10** (the paid figure includes Lopez 2,486 which is not a group payment; ~1,617.66 of booked items cross the year boundary: cheque 23, negative GJ 63).
- Card activity not in QB: **-4,350.15** = Lopez 2,486 (washes out with its Scotiabank payment) + **Align 3,534.52 missing from QB** + Amazon 74.58 + Abeldent 124.84 + Amazon 66.62 - **payments not from Hygiene's bank 1,936.41** (Jul: 1,664.48, 152.55, 84.05, 35.33; treated as Hygiene's payments - see decision). All travel items (Air Canada, American Airlines incl. the 18,120.92 cash advances) wash out to 0.
- QB entries with no card line: +174.10. Timing: -87.30. Unexplained: 0.00.

_Evidence: split_review_FY2025.xlsx sheets 22200 roll-forward / Roll-forward lines_

### QuickBooks entries that don't match the card
*dental_agent · Finding · qb · dntl*

FY2025: Abeldent -124.18 vs card +124.84 (Aug 2025); Amazon -49.92 vs card 66.62 (Aug 26 2025); GJ 61 = 0.00 (Jun 2025, expected 558.19); GJ 63 = -3,425.06 (Aug 2025, only negative journal; expected ~2,224.98); Lopez charge entered as 0.00 (Feb 2025); Align 3,534.52 (Feb 3 2025) missing.
FY2024: Bell 238.92 as -238.49; Bell 237.64 as -327.64; K-Dental 62.14 as -62.14; Google 9.35 as -9.23; Rogers -174.05, -162.75 and Car park -120.00 (wrong sign); Dec 2023 QB-only -2,468.00 on 10000.

### Year-end timing: August split paid in September
*dental_agent · Finding · split · dntl · FY2024*

The Aug 2024 sheet's cheque (C8 9,525.04, visa portion 3,991.57) was paid **Sep 3 2024** (cheque 23) - Dental owed Hygiene 9,525.04 at Aug 31 2024 with nothing recorded. Aug 2025's cheque 82 was paid Aug 26 2025 -> nothing outstanding at Aug 31 2025.

### Intercompany balances don't agree
*dental_agent · Finding · qb*

From the account lists (~Sep 2025): HYG-MGMT differ by 3,543.55 = HYG 2160 Other payables; DNTL 22150 12,500 has no account at all in MGMT's books; DNTL 22100 (owes HYG 7,646.88) and HYG 1340 "Dr. Smitha V" (owes 12,613.12) both show amounts owed. Needs Hygiene's and Management's agents/books to settle.

_Evidence: Group_Chart_of_Accounts_Mapping.xlsx sheet Intercompany_

### Bank vs QuickBooks
*dental_agent · Finding · bank*

DNTL 10000 matches its bank 100% in FY2024 (943/943; one QB-only Dec 2023 -2,468.00) and FY2025 (1,013/1,013). HYG FY2025 326/330; **HYG FY2024 only 246/326** (80 bank-only, 49 QB-only, Dec 2023 - Jul 2024) - for the hygiene_agent.

### Card paid twice in July 2023
*dental_agent · Fact · visa · dntl · FY2023*

Two SCOTIABANK PAYMENTs of 3,391.85 (Jul 20 and Jul 25 2023) paid the same balance - check in Hygiene's FY2023 bank when loaded.

## Proposals awaiting a decision

### Proposed entries JE-1 to JE-7 (Dental, backup copy) ⚑
*dental_agent · Proposal · cleanup · dntl · FY2025*

Seven entries in Dental's books bring 22200 back to the card; held in **fin.ProposedEntries** (Draft until approved). Only JE-5 involves Hygiene (matching entry needed in Hygiene's books).
| No. | Date | Debit | Credit | Amount | Fixes |
|---|---|---|---|---|---|
| JE-1 | Aug 31 2023 | 39900 Suspense (new, Equity) | 22200 | 15,772.83 | Pre-FY2024 difference, parked |
| JE-2 | FY2024 (card dates) | 22200 89.45 net | Telephone / internet | 89.45 | Bell 237.64 as 327.64 (+90.00), Bell 238.92 as 238.49 (-0.43), Google 9.35 as 9.23 (-0.12) |
| JE-3 | FY2024 (card dates) | Expense (per charge) | 22200 | 302.45 | Two FY2024 charges never entered |
| JE-4 | Aug 31 2024 | 22200 | 64400 Dental Supplies | 2.02 | Rounding on FY2024 split payments |
| JE-5 | Aug 31 2025 | 22100 Due from Hygiene 14,141.87; 64400 3,535.48 | 22200 | 17,677.35 | Issues 1-2: split payments booked, not made |
| JE-6 | Feb 3 2025 | Lab fees | 22200 | 3,534.52 | Align never entered (Credit Card Charge) |
| JE-7 | FY2025 (card dates) | Expense (per charge) | 22200 | 75.24 | Feb 74.58 + Aug 0.66 never entered (to identify) |
Plus **TM-1** (Aug 31 2024, Dr 22200 87.30) and its reversal **TM-1R** (Sep 1 2024): an item recorded in FY2024 that the card posted in FY2025.
Effect on 22200: JE-1 Cr 15,772.83; FY2024 net Cr 210.98; FY2025 Cr 21,287.11. Pro-forma: Aug 31 2024 **3,430.48 Cr = card**; Aug 31 2025 **2,009.95 Cr vs 408.99** (1,600.96 open).
Replaces the earlier numbering where JE-2/JE-3 were the optional intercompany entries (now IC-1/IC-2).

_Replaces: Clean-up entries dated Aug 31 2023 (backup copy) (superseded)_

_Evidence: fin.vw_ProposedEntries; fin.vw_ProForma22200; reconciliation report 'Proposed adjustments'_

### Process from FY2026: accrue the split to 22100
*dental_agent · Proposal · cleanup · dntl · FY2026*

At each sheet's month-end: Dr expense accounts (DPC portions), Dr 22200 (visa portion), **Cr 22100 Due to Hygiene** (C8). When the cheque is written: Dr 22100 / Cr 10000. Timing then no longer matters; 22100 shows the unpaid split at any date. Clear 22100's old 7,646.88 first.

### Optional intercompany clean-up IC-1 / IC-2 (Aug 31 2023)
*dental_agent · Proposal · cleanup · dntl · FY2023*

Formerly numbered JE-2 / JE-3; renumbered so JE-1 to JE-7 match the reconciliation report. Not seeded in fin.ProposedEntries.
- IC-1: Dr 22100 7,646.88 / Cr 39900 7,646.88 (dormant since GJ 27, Aug 31 2022).
- IC-2: Dr 39900 12,500.00 / Cr 22150 12,500.00 (cheque 129 to Management, May 3 2023; no matching account in MGMT).
Wait for Hygiene's and Management's books (see "Intercompany balances don't agree"). Note JE-5 also uses 22100.

_Replaces: Clean-up entries dated Aug 31 2023 (backup copy) (superseded)_

## Open tasks

### Final deliverable: all correcting journal entries ⚑
*dental_agent · Task · cleanup · dntl*

One set, **JE-1 to JE-7** plus the timing pair TM-1 / TM-1R, now held in fin.ProposedEntries (see "Proposed entries JE-1 to JE-7"):
1. Accountant reviews; owner approves each entry (`python proposed_entries.py approve JE-n --by owner`).
2. Set the undecided expense accounts first (JE-2, JE-3, JE-6, JE-7, TM-1) - an entry with an undecided account cannot be approved.
3. `python iif_export.py --company dntl` -> IIF of the Approved entries -> import into the **backup copy**.
4. Hygiene's matching entry for JE-5 (Due to Dental 14,141.87) - in Hygiene's books, not seeded yet.
Out of scope for this set (by decision): the Aug 2024 split accrual (9,525.04) and the lab-fee allocation (3,967.90).

### Roll-forward: timing line, washes-out group, year-end accrual
*build_agent · Task · rollforward · dntl*

1. Separate line for the previous year's August split cheque paid in September (e.g. cheque 23, visa portion 3,991.57).
2. Group matching card charges and credits that wash out (personal travel) so they show as 0.
3. Year-end accrual figure in the review = unpaid C8 at Aug 31 (9,525.04 at Aug 31 2024; 0 at Aug 31 2025).

### Accountant's files for FY2021-FY2024 (Dental), then Hygiene
*build_agent · Task · yearend · dntl*

Prepare YearEndTB / YearEndAJE CSVs for Dental FY2021-FY2024 and load them oldest first (`python load_yearend.py --all`), so each year's PriorYearFinal is checked against the year before. Then check fin.vw_YearEndCarryForward for the CC group (22100 + 22150 + 22200 + 22201 + 31400) at Aug 31 2023, 2024 and 2025 (expected around 15,000 each year). Hygiene's files use the same tables when they arrive.

### Later
*build_agent · Task · build*

Scotiabank PDF transaction reader (for FY2021-2023, beyond the 18-month export window); Management company; FY2026 once booked; AI fallback for unknown PDF layouts; the agents themselves (read this board and fin.Findings, post interpretations).

## Open questions

### Questions for the owner / accountant ⚑
*dental_agent · Question · dntl · FY2025*

1. Hygiene's Interac e-transfer 1,664.48 on Jun 19 2025 - related to the card credit of the same amount (business charges paid personally)?
2. Align 3,534.52 (Feb 2025) - business expense to record?
3. Personal payments against business charges (1,936.41) - record as owed to the payer (shareholder loan)?
4. Pre-FY2024 gap 15,772.83 - clear to suspense now (JE-1) or investigate FY2021-2023 first?
5. Where should corrections for closed years be booked in the real file?

### Open decisions before JE-5 is final ⚑
*dental_agent · Question · cleanup · dntl · FY2025*

1. Is the **14,141.87** Hygiene owes Dental repaid, or netted against 22100 (Dental currently owes Hygiene 7,646.88, leaving 6,494.99 due to Dental)?
2. Are the D6 amounts not on the lab list real labs? Settles the further **3,967.90** (separate from 22200).
3. Date the FY2025 entries in FY2025, or partly in FY2026 where the split cheques were booked after Aug 31 2025?
4. Expense accounts for JE-2, JE-3, JE-7 and TM-1, and the vendors behind 302.45 (FY2024), 74.58 and 0.66 (FY2025).

## Answers

### Answers (Oct 2026)
*owner · Answer · dntl · FY2025*

2. Align 3,534.52 (Feb 3 2025) - yes, a Dental lab fee that was never entered: **JE-6**.
3. Personal payments 1,936.41 - treated as **Hygiene's payments** for now (decision "July 2025 credits of 1,936.41").
4. Pre-FY2024 gap 15,772.83 - **park in suspense now (JE-1)**; investigate FY2021-FY2023 later.
Still open: 1 (Hygiene's e-transfer 1,664.48 on Jun 19 2025) and 5 (where closed-year corrections go in the real file).
