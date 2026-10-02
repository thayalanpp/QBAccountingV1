# QBAccountingV1 - handoff (2026-10-02)

Paste this at the start of a new chat. It is the project's message board: decisions, facts, findings, proposals, open tasks and questions, posted by the agents below. Full detail lives in SQL (agent.Messages, fin.Findings) and in the review workbooks.

**Agents:** `build_agent` - Developer who built the QBAccountingV1 application; `dental_agent` - Bookkeeper for S. Viswanathan Dentistry Professional Corporation (DNTL); `hygiene_agent` - Bookkeeper for DHM Dental Hygiene Technical Services Corporation (HYG); `mgmt_agent` - Bookkeeper for the Management company (MGMT); `owner` - Thayalan - owner, decision maker

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

### FY2025 split: B6 overstated after missed payments ⚑
*dental_agent · Finding · split · dntl · FY2025*

Cash (booked as paid on 22200 but not paid): Feb 10,669.85 · Mar 676.63 · Jun 599.00 · Jul 7,668.28 = **19,613.76**.
Expense (Dental visa portion vs the cycle's own charges): Mar -3,355.83 · Apr -5,721.35 · Jun -479.20 · Jul -1,567.20 · Aug -4,567.42 = **-15,691.00** (Dental over-charged).
Year: sum of B6 110,524.28 vs paid to TD 90,910.52 = the cycles' own charges 90,910.52. Started Feb 2025 (first missed payment); FY2024 unaffected.

_Evidence: split_review_FY2025.xlsx; fin.SplitMonths FY2025_

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
- Card activity not in QB: **-4,350.15** = Lopez 2,486 (washes out with its Scotiabank payment) + **Align 3,534.52 missing from QB** + Amazon 74.58 + Abeldent 124.84 + Amazon 66.62 - **personal payments against business charges 1,936.41** (Jul: 1,664.48, 152.55, 84.05, 35.33). All travel items (Air Canada, American Airlines incl. the 18,120.92 cash advances) wash out to 0.
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

### Clean-up entries dated Aug 31 2023 (backup copy) ⚑
*dental_agent · Proposal · cleanup · dntl · FY2023*

Create **39900 Suspense - pre-FY2024 differences** (Equity). No income or expense changes in any year.
- **JE-1 (required):** Dr 39900 15,772.83 / Cr 22200 15,772.83 - sets 22200 to the card balance (6,270.35 owed per the Sep 6 2023 statement). After it, the Aug 31 2024 gap should drop to ~123.68.
- JE-2 (optional): Dr 22100 7,646.88 / Cr 39900 7,646.88.
- JE-3 (optional): Dr 39900 12,500.00 / Cr 22150 12,500.00.
Posting into FY2023 needs the closing-date password. Real books: accountant's decision.

### Process from FY2026: accrue the split to 22100
*dental_agent · Proposal · cleanup · dntl · FY2026*

At each sheet's month-end: Dr expense accounts (DPC portions), Dr 22200 (visa portion), **Cr 22100 Due to Hygiene** (C8). When the cheque is written: Dr 22100 / Cr 10000. Timing then no longer matters; 22100 shows the unpaid split at any date. Clear 22100's old 7,646.88 first.

## Open tasks

### Final deliverable: all correcting journal entries ⚑
*dental_agent · Task · cleanup · dntl*

Collect into one set (IIF for the backup copy): the Aug 31 2023 entries; FY2024 corrections (wrong-sign charges; 9,525.04 August accrual); FY2025 corrections (19,613.76 booked-not-paid; -15,691.00 Dental visa expense; Align 3,534.52; Abeldent/Amazon; personal payments 1,936.41; GJ 61/63).

### Roll-forward: timing line, washes-out group, year-end accrual
*build_agent · Task · rollforward · dntl*

1. Separate line for the previous year's August split cheque paid in September (e.g. cheque 23, visa portion 3,991.57).
2. Group matching card charges and credits that wash out (personal travel) so they show as 0.
3. Year-end accrual figure in the review = unpaid C8 at Aug 31 (9,525.04 at Aug 31 2024; 0 at Aug 31 2025).

### IIF file for the proposed journal entries
*build_agent · Task · cleanup*

Generate the clean-up journal entries as a QuickBooks Desktop IIF file (File > Utilities > Import > IIF) for the backup copy.

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
