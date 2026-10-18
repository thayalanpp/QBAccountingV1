"""
board_seed.py - puts the project history (up to the handoff in Sep 2026) on the agent message board.

    python board_seed.py            # insert / update the seeded posts (safe to rerun)

Each post has a SeedKey, so rerunning updates it in place instead of adding a copy.
Later posts by the agents themselves have no SeedKey and are never touched by this script -
except when a seeded post supersedes one of them (see `supersedes` below).

Superseding: a post that replaces an earlier one names it in `supersedes` - a seed key, or
"amount:<n>" for an un-seeded post (one an agent wrote directly) found by its amount. The new
post gets SupersedesID = that post's MessageID and the old post is set to Status 'Superseded'
(board_export.py then leaves it out). `parent` links an Answer to its Question (ParentID).
"""

from sqlalchemy import text

AGENTS = [
    ("owner", "Thayalan - owner, decision maker",
     "Answers questions, approves proposals, decides what goes into the real QuickBooks files."),
    ("build_agent", "Developer who built the QBAccountingV1 application",
     "Code, pipelines, SQL schema, run order, data-source quirks, technical backlog."),
    ("dental_agent", "Bookkeeper for S. Viswanathan Dentistry Professional Corporation (DNTL)",
     "Dental's accounts: purpose, verified balances, findings, proposed QuickBooks entries, questions for the accountant."),
    ("hygiene_agent", "Bookkeeper for DHM Dental Hygiene Technical Services Corporation (HYG)",
     "Hygiene's side: split cheques received, TD Visa payments made, its intercompany balances. (Not active yet.)"),
    ("mgmt_agent", "Bookkeeper for the Management company (MGMT)",
     "The $2,500 cheques from Dental and Management's intercompany balances. (Not active yet.)"),
]

# (seed_key, thread, author, type, area, company, fy, title, body, amount, evidence, status, priority)
P = []
def post(key, thread, author, mtype, area, company, fy, title, body, amount=None, evidence=None, status="Open", priority=2,
         supersedes=None, parent=None):
    P.append(dict(seed_key=key, thread=thread, author=author, mtype=mtype, area=area, company=company, fy=fy,
                  title=title, body=body.strip(), amount=amount, evidence=evidence, status=status, priority=priority,
                  supersedes=supersedes, parent=parent))

# ---------------------------------------------------------------- owner: context and decisions
post("own-entities", "context", "owner", "Fact", None, None, None, "Entities, accounts and fiscal year", """
- Fiscal year: **Sep 1 - Aug 31**, named by the year it ends (FY2025 = Sep 1 2024 - Aug 31 2025).
- **DNTL** S. Viswanathan Dentistry Professional Corporation - Scotiabank 26492-00281-18 (QB 10000), TD Aeroplan Visa 6761 (QB 22200 "Credit Card-NM interco").
- **HYG** DHM Dental Hygiene Technical Services Corporation - Scotiabank 26492-00688-10 (QB 1010 and sub-account 1010.25 "Post smitha" = the new start in 2021).
- **MGMT** Dental & Hygiene Management - TD 0561-5208812 (QB 1010).
- The TD Visa is Dental's card but is **paid from Hygiene's bank**; Dental reimburses Hygiene with a monthly "split" cheque. Management receives $2,500 cheques from Dental.
- SQL Server: NilashaAI, database QBAccounting. Repo: github.com/thayalanpp/QBAccountingV1 (tag v0.3-baseline).
""", status="Resolved")
post("own-dec-workbook", "decisions", "owner", "Decision", "split", None, None, "Split workbook is read as is", """
NM_<year>.xlsx (one sheet per month, rows 2-8: salary, rent, lease, bookkeeping, visa; B = total, C = Dental's (DPC) portion, C8 = the split cheque, D6 = lab fees on the card at 100%) is read **as is**. The years are closed; differences become proposed adjustments, never edits to the workbook.
""", status="Resolved")
post("own-dec-posting", "decisions", "owner", "Decision", "split", "dntl", None, "Where the split is posted", """
All of Dental's split portions go directly to **Dental expense accounts**. Hygiene's portion of the Visa (B6 - C6) is recorded in Dental's books as the "hygiene supplies" journal (Dr 22200 / Cr 64400 Dental Supplies) so the card account balances.
A missed Visa payment rolls to the next month; **B6 must be checked against the Visa transactions**.
""", status="Resolved")
post("own-dec-qb-exports", "decisions", "owner", "Decision", "qb", None, None, "QuickBooks: one fiscal year per export", """
Multi-year Transaction Detail / General Ledger exports keep income & expense rows only for the **last** fiscal year in the range, so QuickBooks is exported **one fiscal year per file** (C:\\NM\\quickbooks\\<co>\\qb ... {year} - transaction detail.xlsx). The account list doesn't change and is reused. The multi-year GL is still useful for balance-sheet balances.
""", status="Resolved")
post("own-dec-folders", "decisions", "owner", "Decision", "build", None, None, "Folder layout", """
C:\\NM\\<year>yearend\\dntl\\, \\hyg\\, \\mgmt\\ - each company's bank export(s) AND its monthly statement PDFs, directly in the folder.
C:\\NM\\<year>yearend\\visa\\ - TD Visa statements. C:\\NM\\<year>yearend\\NM_<year>.xlsx - split workbook. QuickBooks per-year files in C:\\NM\\quickbooks\\<co>\\.
""", status="Resolved")
post("own-dec-cleanup", "decisions", "owner", "Decision", "cleanup", None, None, "Clean-up entries go into a backup copy first", """
The end product of the analysis is a set of **QuickBooks journal entries**. They are posted to a **backup copy** of the QuickBooks file first; the accountant decides what goes into the real books (FY2023 and earlier are filed).
""", status="Resolved")
post("own-dec-agents", "decisions", "owner", "Decision", "build", None, None, "Agents and this board", """
Future design: a **dental_agent** and a **hygiene_agent** (mgmt later) - each with this project's knowledge - check that each company is charged correctly and collaborate through this board. The AI fallback for PDF reading is on hold; rule-based parsers are used.
""", status="Resolved")

# ---------------------------------------------------------------- build_agent
post("bld-architecture", "build-architecture", "build_agent", "Fact", "build", None, None, "Application: pipelines and files", """
All rule-based Python + SQL Server; LangGraph orchestrates each pipeline; nothing is ever written to QuickBooks.
| Pipeline | Files | Does |
|---|---|---|
| QuickBooks | run_qb_load.py, qb_loader.py | Loads one company x one FY from the Transaction Detail export; replaces the year; balance-checks every account section; stores opening/closing in qb.AccountBalances |
| TD Visa | visa_pipeline.py, td_visa_parser.py, statement_validator.py, visa_sql.py, qb_matcher.py | Parses statement PDFs, 10 checks (summary math, totals by type, chaining...), saves, matches to QB 22200 |
| Banks | bank_pipeline.py, bank_readers.py, bank_validator.py, bank_sql.py, bank_models.py | Scotiabank/TD exports (Excel/CSV), statement PDFs as known balances, monthly periods, matches to QB bank accounts, fin.vw_AccountTransfers |
| Split | split_pipeline.py, split_reader.py, split_assess.py, split_sql.py, rollforward.py | Reads NM_<year>.xlsx, links cheques/deposits/card, cash & expense tests, QB postings, boundary months, 22200 roll-forward, cut-off; writes split_review_FY<year>.xlsx |
| GL balances | load_gl_balances.py | Reads the multi-year GL (dntl: C:\\NM\\quickbooks\\dntl\\qb sv gl 2020-2026.xlsx); balance-sheet accounts' balance at every Aug 31, export sign (debit +); refuses to save unless every section re-derives, the known balances agree and each year's movement equals the one-year load; upserts qb.AccountBalances |
| Chart of accounts | load_account_map.py | Loads the reviewed group chart mapping (qb.StdAccounts / qb.AccountMap) |
sql/schema.sql is applied automatically at the start of every pipeline. sql/reset_test_data.sql clears loaded data.
""", status="Resolved")
post("bld-run-order", "build-architecture", "build_agent", "Fact", "build", None, None, "Run order for a year", """
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
""", status="Resolved")
post("bld-qb-quirks", "build-quirks", "build_agent", "Fact", "qb", None, None, "QuickBooks export quirks", """
- Multi-year exports drop income/expense except the final year -> export one FY per file.
- The one-year Transaction Detail report starts 22200's running balance at **0** (no balance brought forward) -> run_qb_load.py stores a 0 opening for balance-sheet accounts; load_gl_balances.py replaces it with the multi-year GL's balances (rerun after every QB reload).
- DNTL QuickBooks was booked only up to **Sep 29, 2025**; the multi-year GL exported Oct 2026 has rows dated up to **Aug 31, 2026** - confirm whether FY2026 is fully booked before using its balances.
- qb_loader refuses a reload writing < 90% of the lines the year already holds (shrink guard). run_qb_load.py has a custom COMPANIES block - don't overwrite it.
""", status="Resolved")
post("bld-bank-quirks", "build-quirks", "build_agent", "Fact", "bank", None, None, "Bank data quirks", """
- Scotiabank exports go back only **18 months**; FY2024 came from the bookkeeper's workbook (sheets dntl / hyg): amounts unsigned (direction from Debit/Credit) and **rows re-sorted** - the reader rebuilds the bank's order from the balances (year +/- 14 days) and reports rows that don't fit (DNTL FY2024 row 69 = duplicate, deleted).
- Monthly statement PDFs in the company folder are read for opening/closing balances and checked against the export.
- FY2025 DNTL file = bookkeeper's tagged file + bank export combined (DNTL_8118_FY2025_combined.xlsx); tags and allocation columns (Salary, rent, lease, bookkeping, visa) are stored in fin.TransactionSplits.
- MGMT TD export: headers credit/debit swapped (reader uses the balance), ends Aug 15 2025 - not loaded.
""", status="Resolved")
post("bld-visa-quirks", "build-quirks", "build_agent", "Fact", "visa", None, None, "TD Visa parser notes", """
Reads page 1's two columns separately; dates get their year from the statement period; posting date must be in the period. Handles cash advances in English/French, the pre-2024 "NET AMOUNT OF MONTHLY ACTIVITY" subtotal, one-line foreign currency, and lines TD counts as cash advances without saying so (unique combination that makes up the printed total - recorded as a note).
""", status="Resolved")
post("bld-split-rules", "build-quirks", "build_agent", "Fact", "split", None, None, "Split review rules", """
- Cash test: B6 vs Hygiene's payments to TD ("td visa" in its bank) after the sheet's statement, before the next.
- Expense test: base = new balance - previous balance + real payments; a card payment is real only if the matching bank withdrawal is itself a payment to the card; payments outside the loaded bank data are assumed real. Correct DPC = (base - lab) x 0.8 + lab.
- QB postings: cheque found by number, or by amount and date when the bank has no numbers (FY2024); 2-cent rounding tolerance.
- Boundary months (last month of the previous year, first of the next) come from the neighbouring NM_<year>.xlsx and are kept out of the totals.
""", status="Resolved")
post("bld-status-data", "state", "build_agent", "Status", "build", None, None, "What is loaded (as of the handoff)", """
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
""")
post("bld-task-gl-balances", "build-backlog", "build_agent", "Task", "rollforward", "dntl", 2025,
     "Take opening balances from the multi-year GL", """
qb.AccountBalances opening for balance-sheet accounts is 0 (see QuickBooks quirks). Build load_gl_balances.py: read the multi-year GL (C:\\NM\\quickbooks\\dntl\\qb sv gl 2020-2026.xlsx), compute each balance-sheet account's balance at every FY end, store in qb.AccountBalances. The 22200 roll-forward will then start from **12,466.03 Dr** at Aug 31 2024.

**Done (Oct 2026).** load_gl_balances.py built and run for DNTL. Dry run: every section re-derives; 10000, 22200, 22100, 22150 agree with the known Aug 31 2023/2024/2025 balances to the cent; movements agree with the FY2024/FY2025 one-year loads. Split review rerun:
| | Opening gap | Closing gap | Unexplained |
|---|---|---|---|
| FY2024 | 15,772.83 (QB owed -9,502.48 vs card 6,270.35) | 15,896.51 | 0.00 |
| FY2025 | 15,896.51 (QB owed -12,466.03 vs card 3,430.48) | 35,669.96 (-35,260.97 vs 408.99) | 0.00 |
FY2025 items agree with dental_agent's itemization (-15,510.10 / -4,350.15 / +174.10 / -87.30). Items 4, 7, 8 are still gross until the washes-out grouping (next roll-forward task).
""", status="Resolved", priority=1)
post("bld-task-rf-lines", "build-backlog", "build_agent", "Task", "rollforward", "dntl", None,
     "Roll-forward: timing line, washes-out group, year-end accrual", """
1. Separate line for the previous year's August split cheque paid in September (e.g. cheque 23, visa portion 3,991.57).
2. Group matching card charges and credits that wash out (personal travel) so they show as 0.
3. Year-end accrual figure in the review = unpaid C8 at Aug 31 (9,525.04 at Aug 31 2024; 0 at Aug 31 2025).
""")
post("bld-task-iif", "build-backlog", "build_agent", "Task", "cleanup", None, None,
     "IIF file for the proposed journal entries", """
Generate the clean-up journal entries as a QuickBooks Desktop IIF file (File > Utilities > Import > IIF) for the backup copy.

**Done (Oct 2026).** iif_export.py writes **Approved** entries from fin.ProposedEntries only - see "Proposed entries: tables, approval and IIF".
""", status="Resolved")
post("bld-task-later", "build-backlog", "build_agent", "Task", "build", None, None, "Later", """
Scotiabank PDF transaction reader (for FY2021-2023, beyond the 18-month export window); Management company; FY2026 once booked; AI fallback for unknown PDF layouts; the agents themselves (read this board and fin.Findings, post interpretations).
""", priority=3)

# ---------------------------------------------------------------- dental_agent
post("dnt-accounts", "dntl-accounts", "dental_agent", "Fact", "qb", "dntl", None,
     "Dental's key accounts: purpose and balances", """
From the multi-year GL (debit +):
| Account | Purpose | Aug 31 2023 | Aug 31 2024 | Aug 31 2025 |
|---|---|---|---|---|
| 10000 Scotiabank | Dental's bank - agrees with the bank on all three dates | 13,263.95 | 65,468.68 | 13,103.87 |
| 22200 Credit Card-NM interco | TD Visa 6761 | 9,502.48 Dr | 12,466.03 Dr | 35,260.97 Dr |
| 22100 NM Hygiene interco | Dental owes Hygiene for supplies (GJ 27, Aug 31 2022) - dormant | 7,646.88 Cr | 7,646.88 Cr | 7,646.88 Cr |
| 22150 Interco Management | Cheque 129 to Management, May 3 2023 - dormant | 12,500.00 Dr | 12,500.00 Dr | 12,500.00 Dr |
The monthly allocations do **not** go through 22100/22150.
""", evidence="multi-year GL: qb sv gl 2020-2026.xlsx", status="Resolved")
post("dnt-22200-mechanism", "dntl-22200", "dental_agent", "Fact", "qb", "dntl", None, "How 22200 works", """
Credited with every card charge (expensed 100% in Dental). Debited by (1) the split cheque's **visa portion** (Dental's share, paid to Hygiene, which paid TD) and (2) the monthly **"hygiene supplies" journal** (Hygiene's share, other side **Cr 64400 Dental Supplies**). 22200 equals the real card only if (1)+(2) = what was actually paid to TD. Each cheque is written about a month after its sheet.
""", status="Resolved")
post("dnt-22200-gap", "dntl-22200", "dental_agent", "Finding", "rollforward", "dntl", 2025,
     "22200 vs the real card balance", """
| | Aug 31 2023 | Aug 31 2024 | Aug 31 2025 |
|---|---|---|---|
| Owed per QB 22200 | -9,502.48 | -12,466.03 | -35,260.97 |
| Owed per TD statements | 6,270.35 | 3,430.48 | 408.99 |
| QB understates the card by | 15,772.83 | 15,896.51 | 35,669.96 |
FY2024 moved the gap only **+123.68** (wrong-sign entries). FY2025 added **19,773.45**. The 15,772.83 predates FY2024 (FY2021-2023, unexplained until those years are loaded).
""", amount=35669.96, evidence="split_review_FY2025.xlsx sheet 22200 roll-forward (opens from the GL balance; unexplained 0.00)", priority=1)
post("dnt-fy2024-split", "dntl-split", "dental_agent", "Finding", "split", "dntl", 2024, "FY2024 split: clean", """
12/12 months: B6 = what Hygiene paid TD = the cycle's own charges. No carried balances. Only item: the Sep 2023 cheque was 13,028.47 vs C8 13,023.47 (**$5.00** over).
""", amount=5.00, status="Resolved")
post("dnt-fy2025-split", "dntl-split", "dental_agent", "Finding", "split", "dntl", 2025,
     "FY2025 split: B6 overstated after missed payments (superseded)", """
Cash (booked as paid on 22200 but not paid): Feb 10,669.85 · Mar 676.63 · Jun 599.00 · Jul 7,668.28 = **19,613.76**.
Expense (Dental visa portion vs the cycle's own charges): Mar -3,355.83 · Apr -5,721.35 · Jun -479.20 · Jul -1,567.20 · Aug -4,567.42 = **-15,691.00** (Dental over-charged).
Year: sum of B6 110,524.28 vs paid to TD 90,910.52 = the cycles' own charges 90,910.52. Started Feb 2025 (first missed payment); FY2024 unaffected.
""", amount=19613.76, evidence="split_review_FY2025.xlsx; fin.SplitMonths FY2025", status="Superseded", priority=1)
post("dnt-fy2025-rf", "dntl-22200", "dental_agent", "Finding", "rollforward", "dntl", 2025,
     "FY2025 roll-forward: the 19,773.45 itemized", """
- Payments booked vs made: journals 8,056.37 + cheque visa portions 100,850.25 booked vs 93,396.52 paid = **-15,510.10** (the paid figure includes Lopez 2,486 which is not a group payment; ~1,617.66 of booked items cross the year boundary: cheque 23, negative GJ 63).
- Card activity not in QB: **-4,350.15** = Lopez 2,486 (washes out with its Scotiabank payment) + **Align 3,534.52 missing from QB** + Amazon 74.58 + Abeldent 124.84 + Amazon 66.62 - **payments not from Hygiene's bank 1,936.41** (Jul: 1,664.48, 152.55, 84.05, 35.33; treated as Hygiene's payments - see decision). All travel items (Air Canada, American Airlines incl. the 18,120.92 cash advances) wash out to 0.
- QB entries with no card line: +174.10. Timing: -87.30. Unexplained: 0.00.
""", amount=-19773.45, evidence="split_review_FY2025.xlsx sheets 22200 roll-forward / Roll-forward lines")
post("dnt-qb-errors", "dntl-corrections", "dental_agent", "Finding", "qb", "dntl", None,
     "QuickBooks entries that don't match the card", """
FY2025: Abeldent -124.18 vs card +124.84 (Aug 2025); Amazon -49.92 vs card 66.62 (Aug 26 2025); GJ 61 = 0.00 (Jun 2025, expected 558.19); GJ 63 = -3,425.06 (Aug 2025, only negative journal; expected ~2,224.98); Lopez charge entered as 0.00 (Feb 2025); Align 3,534.52 (Feb 3 2025) missing.
FY2024: Bell 238.92 as -238.49; Bell 237.64 as -327.64; K-Dental 62.14 as -62.14; Google 9.35 as -9.23; Rogers -174.05, -162.75 and Car park -120.00 (wrong sign); Dec 2023 QB-only -2,468.00 on 10000.
""")
post("dnt-yearend-timing", "dntl-split", "dental_agent", "Finding", "split", "dntl", 2024,
     "Year-end timing: August split paid in September", """
The Aug 2024 sheet's cheque (C8 9,525.04, visa portion 3,991.57) was paid **Sep 3 2024** (cheque 23) - Dental owed Hygiene 9,525.04 at Aug 31 2024 with nothing recorded. Aug 2025's cheque 82 was paid Aug 26 2025 -> nothing outstanding at Aug 31 2025.
""", amount=9525.04)
post("dnt-interco", "dntl-interco", "dental_agent", "Finding", "qb", None, None, "Intercompany balances don't agree", """
From the account lists (~Sep 2025): HYG-MGMT differ by 3,543.55 = HYG 2160 Other payables; DNTL 22150 12,500 has no account at all in MGMT's books; DNTL 22100 (owes HYG 7,646.88) and HYG 1340 "Dr. Smitha V" (owes 12,613.12) both show amounts owed. Needs Hygiene's and Management's agents/books to settle.
""", evidence="Group_Chart_of_Accounts_Mapping.xlsx sheet Intercompany")
post("dnt-bank-recon", "dntl-accounts", "dental_agent", "Finding", "bank", None, None, "Bank vs QuickBooks", """
DNTL 10000 matches its bank 100% in FY2024 (943/943; one QB-only Dec 2023 -2,468.00) and FY2025 (1,013/1,013). HYG FY2025 326/330; **HYG FY2024 only 246/326** (80 bank-only, 49 QB-only, Dec 2023 - Jul 2024) - for the hygiene_agent.
""")
post("dnt-card-history", "dntl-22200", "dental_agent", "Fact", "visa", "dntl", 2023, "Card paid twice in July 2023", """
Two SCOTIABANK PAYMENTs of 3,391.85 (Jul 20 and Jul 25 2023) paid the same balance - check in Hygiene's FY2023 bank when loaded.
""", amount=3391.85)
post("dnt-prop-je-2023", "dntl-cleanup", "dental_agent", "Proposal", "cleanup", "dntl", 2023,
     "Clean-up entries dated Aug 31 2023 (backup copy) (superseded)", """
Create **39900 Suspense - pre-FY2024 differences** (Equity). No income or expense changes in any year.
- **JE-1 (required):** Dr 39900 15,772.83 / Cr 22200 15,772.83 - sets 22200 to the card balance (6,270.35 owed per the Sep 6 2023 statement). After it, the Aug 31 2024 gap should drop to ~123.68.
- JE-2 (optional): Dr 22100 7,646.88 / Cr 39900 7,646.88.
- JE-3 (optional): Dr 39900 12,500.00 / Cr 22150 12,500.00.
Posting into FY2023 needs the closing-date password. Real books: accountant's decision.
""", amount=15772.83, status="Superseded", priority=1)
post("dnt-prop-process", "dntl-cleanup", "dental_agent", "Proposal", "cleanup", "dntl", 2026,
     "Process from FY2026: accrue the split to 22100", """
At each sheet's month-end: Dr expense accounts (DPC portions), Dr 22200 (visa portion), **Cr 22100 Due to Hygiene** (C8). When the cheque is written: Dr 22100 / Cr 10000. Timing then no longer matters; 22100 shows the unpaid split at any date. Clear 22100's old 7,646.88 first.
""")
post("dnt-prop-final", "dntl-cleanup", "dental_agent", "Task", "cleanup", "dntl", None,
     "Final deliverable: all correcting journal entries", """
One set, **JE-1 to JE-7** plus the timing pair TM-1 / TM-1R, now held in fin.ProposedEntries (see "Proposed entries JE-1 to JE-7"):
1. Accountant reviews; owner approves each entry (`python proposed_entries.py approve JE-n --by owner`).
2. Set the undecided expense accounts first (JE-2, JE-3, JE-6, JE-7, TM-1) - an entry with an undecided account cannot be approved.
3. `python iif_export.py --company dntl` -> IIF of the Approved entries -> import into the **backup copy**.
4. Hygiene's matching entry for JE-5 (Due to Dental 14,141.87) - in Hygiene's books, not seeded yet.
Out of scope for this set (by decision): the Aug 2024 split accrual (9,525.04) and the lab-fee allocation (3,967.90).
""", priority=1)
post("dnt-coa", "dntl-accounts", "dental_agent", "Status", "coa", None, None, "Group chart of accounts", """
Group_Chart_of_Accounts_Mapping.xlsx maps 417 QB accounts (DNTL 64, HYG 143, MGMT 210) to a 109-account standard chart with company prefixes (DNTL-10000 ...); 71 rows need review; tax lines to be confirmed by the accountant. Load with load_account_map.py after review (status: not yet reported as loaded).
""")
post("dnt-questions", "questions", "dental_agent", "Question", None, "dntl", 2025, "Questions for the owner / accountant", """
1. Hygiene's Interac e-transfer 1,664.48 on Jun 19 2025 - related to the card credit of the same amount (business charges paid personally)?
2. Align 3,534.52 (Feb 2025) - business expense to record?
3. Personal payments against business charges (1,936.41) - record as owed to the payer (shareholder loan)?
4. Pre-FY2024 gap 15,772.83 - clear to suspense now (JE-1) or investigate FY2021-2023 first?
5. Where should corrections for closed years be booked in the real file?
""", priority=1)


# ---------------------------------------------------------------- Oct 2026: reconciliation report (JE-1 to JE-7)
# Source: "Dental Visa (22200) Reconciliation - Issues and Proposed Adjustments to FY2025" (Claude Doc, Oct 3 2026).
post("own-dec-lopez", "decisions", "owner", "Decision", "split", "dntl", 2025, "Lopez charges are Hygiene's", """
The Lopez Senathi & Associates charges on the card (2,486.00 + 2,062.25 = **4,548.25**, Mar 2025 cycle) are **Hygiene's**. The bookkeeper moved them to a Hygiene account and they were paid by two separate payments on Feb 12 2025; they are correctly left out of Dental's QuickBooks.
They should not have been in the March 2025 split, so Dental's 80% of them (**3,638.60**) is part of what Hygiene owes Dental (JE-5).
""", amount=4548.25, status="Resolved")
post("own-dec-1936", "decisions", "owner", "Decision", "split", "dntl", 2025, "July 2025 credits of 1,936.41 treated as Hygiene's payments", """
The July 2025 card payment credits that did not come from Hygiene's bank (1,664.48, 152.55, 84.05, 35.33 = **1,936.41**) are **assumed to be Hygiene's payments** for now, at the owner's direction.
Effect: they count as real payments to TD, so they are not "booked but not paid". This is exactly why the figures dropped: booked-not-paid 19,613.76 -> **17,677.35** (-1,936.41) and Dental over-charged 15,691.00 -> **14,141.87** (-1,549.13 = 80%).
If they prove to be personal payments, Dental's over-charge rises by 1,549.13 and JE-5 changes; revisit before the accountant finalises JE-5.
""", amount=1936.41, status="Resolved")
post("own-dec-split-process", "decisions", "owner", "Decision", "split", None, 2026, "Visa split from now on: 80/20 monthly, one year-end lab adjustment", """
Agreed with the bookkeeper (Shaju Paul CPA):
- Each month split the Visa **80% Dental / 20% Hygiene**, using only that statement's **own new charges**: never the full balance, never charges paid separately, net of credits and refunds.
- **One year-end adjustment** moves Hygiene's 20% of lab fees (100% Dental items) back to Dental. Lab vendors are posted to a dedicated "Lab fees - Visa" account so the total is ready at year-end.
- The lab-fee allocation for FY2024/FY2025 (a further **3,967.90** if the unlisted D6 amounts are not labs) is a separate matter, **not part of the 22200 fix**.
""", status="Resolved")
post("own-dec-aug2024-accrual", "decisions", "owner", "Decision", "cleanup", "dntl", 2024, "Aug 2024 split accrual is outside the 22200 fix", """
The Aug 2024 sheet's cheque (C8 9,525.04, cheque 23, paid Sep 3 2024) means Dental owed Hygiene 9,525.04 at Aug 31 2024 with nothing recorded. That accrual is a **22100 / expense cut-off matter**, not a 22200 error, and is **not** part of JE-1 to JE-7.
(The 22200 effect of year-end cheque timing - about 1,617.66 net - is one of the parts of the remaining 1,600.96; see "FY2025: 1,600.96 still to explain".)
""", amount=9525.04, status="Resolved")
post("own-ans-questions", "questions", "owner", "Answer", None, "dntl", 2025, "Answers (Oct 2026)", """
2. Align 3,534.52 (Feb 3 2025) - yes, a Dental lab fee that was never entered: **JE-6**.
3. Personal payments 1,936.41 - treated as **Hygiene's payments** for now (decision "July 2025 credits of 1,936.41").
4. Pre-FY2024 gap 15,772.83 - **park in suspense now (JE-1)**; investigate FY2021-FY2023 later.
Still open: 1 (Hygiene's e-transfer 1,664.48 on Jun 19 2025) and 5 (where closed-year corrections go in the real file).
""", status="Resolved", parent="dnt-questions")

post("dnt-fy2025-overcharge", "dntl-split", "dental_agent", "Finding", "split", "dntl", 2025,
     "FY2025 split: Hygiene owes Dental 14,141.87", """
Replaces the earlier Dental-visa-expense figure of **-15,691.00**. Dental was over-charged through the split cheques by **14,141.87**:
| Cause | Split in error | Dental over-charged (80%) | Hygiene's 20% |
|---|---|---|---|
| Issue 1 - past balance split again: Apr 2025 (7,151.69), Aug 2025 (5,709.28) | 12,860.97 | 10,288.77 | 2,572.20 |
| Issue 2 - Lopez 4,548.25 (Mar), interest credit not deducted 245.54 (Mar), Jul 22.59; 599.00 fee Mar/Jun nets 0 | 4,816.38 | 3,853.10 | 963.28 |
| **Total** | **17,677.35** | **14,141.87** | **3,535.48** |
Difference from 15,691.00 = 1,549.13 = 80% of the 1,936.41 July credits now treated as Hygiene's. FY2024 needs no split correction.
""", amount=14141.87, evidence="Reconciliation report Issues 1-2; split_review_FY2025.xlsx", priority=1, supersedes="dnt-fy2025-split")
post("dnt-fy2025-booked-not-paid", "dntl-split", "dental_agent", "Finding", "split", "dntl", 2025,
     "FY2025 split: 17,677.35 booked as paid on 22200 but never paid to TD", """
Replaces the earlier cash figure of **19,613.76**. The split cheques' visa portions plus the hygiene-supplies journals recorded **17,677.35** of card payments TD never received (Issue 1 12,860.97 + Issue 2 4,816.38). Reversing them is **JE-5** (Cr 22200 17,677.35; Dr 22100 Due from Hygiene 14,141.87; Dr 64400 Dental Supplies 3,535.48).
Difference from 19,613.76 = 1,936.41, the July credits now treated as Hygiene's payments. Over FY2025 Hygiene paid TD exactly the cycles' own charges (90,910.52); only the bookkeeping went beyond that.
""", amount=17677.35, evidence="Reconciliation report Issue 1-2 tables; JE-5", priority=1, supersedes="dnt-fy2025-split")
post("dnt-fy2025-residual", "dntl-22200", "dental_agent", "Finding", "rollforward", "dntl", 2025,
     "FY2025: 1,600.96 still to explain after JE-1 to JE-7", """
Replaces the earlier working figure of **1,513.66**, which did not allow for the 87.30 FY2024 timing item reversing in FY2025 (1,513.66 + 87.30 = 1,600.96).
After all proposed entries 22200 = **2,009.95 Cr** at Aug 31 2025 vs the card's **408.99** (Aug 31 2024 lands exactly on 3,430.48).
| FY2025 roll-forward line | Roll-forward | Covered by entries | Left |
|---|---|---|---|
| Payments booked vs made | 15,510.10 | 17,677.35 (JE-5) | 2,167.25 |
| Card activity not in QB | 4,350.15 | 3,609.76 (JE-6, JE-7) | -740.39 |
| QB card entries with no card line | 174.10 | none yet | 174.10 |
| **Total** | | | **1,600.96** |
The 2,167.25 looks like timing/treatment, not new errors: split payments booked after the year-end they belong to (~1,617.66 net, Aug 2024 / Aug 2025 sheets), the Lopez 2,486.00 payment from a group bank account, and the 1,936.41 July credits. Confirm before JE-5 is final - part may belong in FY2026.
""", amount=1600.96, evidence="fin.vw_ProForma22200 (FiscalYearEnd 2025, ProFormaOverCard); reconciliation report Proof", priority=1,
     supersedes="amount:1513.66")
post("dnt-prop-je-set", "dntl-cleanup", "dental_agent", "Proposal", "cleanup", "dntl", 2025, "Proposed entries JE-1 to JE-7 (Dental, backup copy)", """
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
""", amount=15772.83, evidence="fin.vw_ProposedEntries; fin.vw_ProForma22200; reconciliation report 'Proposed adjustments'",
     priority=1, supersedes="dnt-prop-je-2023")
post("dnt-prop-interco", "dntl-cleanup", "dental_agent", "Proposal", "cleanup", "dntl", 2023, "Optional intercompany clean-up IC-1 / IC-2 (Aug 31 2023)", """
Formerly numbered JE-2 / JE-3; renumbered so JE-1 to JE-7 match the reconciliation report. Not seeded in fin.ProposedEntries.
- IC-1: Dr 22100 7,646.88 / Cr 39900 7,646.88 (dormant since GJ 27, Aug 31 2022).
- IC-2: Dr 39900 12,500.00 / Cr 22150 12,500.00 (cheque 129 to Management, May 3 2023; no matching account in MGMT).
Wait for Hygiene's and Management's books (see "Intercompany balances don't agree"). Note JE-5 also uses 22100.
""", priority=3, supersedes="dnt-prop-je-2023")
post("dnt-questions-je", "questions", "dental_agent", "Question", "cleanup", "dntl", 2025, "Open decisions before JE-5 is final", """
1. Is the **14,141.87** Hygiene owes Dental repaid, or netted against 22100 (Dental currently owes Hygiene 7,646.88, leaving 6,494.99 due to Dental)?
2. Are the D6 amounts not on the lab list real labs? Settles the further **3,967.90** (separate from 22200).
3. Date the FY2025 entries in FY2025, or partly in FY2026 where the split cheques were booked after Aug 31 2025?
4. Expense accounts for JE-2, JE-3, JE-7 and TM-1, and the vendors behind 302.45 (FY2024), 74.58 and 0.66 (FY2025).
""", priority=1)
post("bld-proposed-entries", "build-architecture", "build_agent", "Fact", "cleanup", None, None,
     "Proposed entries: tables, approval and IIF", """
| Piece | Does |
|---|---|
| fin.ProposedEntries / fin.ProposedEntryLines | One row per entry (JE-1 ... TM-1R) and its legs, debit + / credit -, company QB account numbers; undecided accounts keep AccountNumber NULL + AccountHint |
| fin.vw_ProposedEntries | Per entry: balanced?, undecided lines, ReadyToApprove, effect on 22200 |
| fin.vw_ProForma22200 | Dental 22200 at each Aug 31: QB (multi-year GL), + proposed entries (all / approved only), vs card owed that day |
| proposed_entries.py | `seed` (rerunnable; Draft entries are rewritten, Approved/Posted are never touched), `list`, `set-account`, `approve`, `reject`, `mark-posted`, `proforma --check` |
| iif_export.py | Writes **Approved** entries only to a QuickBooks Desktop IIF (accounts by full name from qb.ChartOfAccounts; creates 39900 via !ACCNT; JE-6 as a Credit Card Charge); refuses unbalanced or undecided entries; stamps IIFExportedAt |
Status flow: Draft -> Approved -> Posted (in the backup copy), or Rejected / Superseded. Dates are written MM/DD/YYYY by default (`--date-format dmy` if QuickBooks reads day first) - check one entry in the register after the import.
""", status="Resolved")

post("bld-yearend", "build-architecture", "build_agent", "Fact", "yearend", None, None,
     "Accountant's year-end files: tables, loader and views", """
M&Co's adjusting entries never reach QuickBooks, so their figures are loaded alongside the QuickBooks data to compare year by year.
| Piece | Does |
|---|---|
| fin.YearEndAJE | M&Co's AJE lines (Debit / Credit positive; IsRollForward = "to record last year adjustment") |
| fin.YearEndTB | M&Co's closing TB: Prelim + Adj + Reclass = Final, debit + / credit -, with the FS line |
| load_yearend.py | `--company dntl --fy 2025` or `--all`; replaces company x year; refuses to save unless AJEs balance, TB columns sum to 0, every row adds up and TB Adj = AJE net per account. Reports PriorYearFinal continuity, Prelim vs QuickBooks, and M&Co-only accounts |
| fin.vw_YearEndTB_vs_QB | Each TB line vs QuickBooks at the year end (balance sheet: AccountBalances closing; P&L: the year's activity) |
| fin.vw_YearEndCarryForward | Balance-sheet Final vs QuickBooks closing: the cumulative adjustment never posted to QuickBooks |
| fin.vw_YearEndFSLines | Final by statement line, plus a Net income row, to compare with the PDF statements |
Files: C:\\NM\\<FY>yearend\\<co>\\accountant\\YearEndTB_<co>_<FY>.csv and YearEndAJE_<co>_<FY>.csv (CSVs prepared from the PDFs; the loader does not read PDFs).
FY2025 Dental: 36 TB rows, 6 AJEs (17 lines, 209,368.21 each side), CC group Final 25,137.22, net income 47,997.35. No IIF of AJEs until it is decided whether to record them.
""", status="Resolved")
post("bld-task-yearend-history", "build-backlog", "build_agent", "Task", "yearend", "dntl", None,
     "Accountant's files for FY2021-FY2024 (Dental), then Hygiene", """
Prepare YearEndTB / YearEndAJE CSVs for Dental FY2021-FY2024 and load them oldest first (`python load_yearend.py --all`), so each year's PriorYearFinal is checked against the year before. Then check fin.vw_YearEndCarryForward for the CC group (22100 + 22150 + 22200 + 22201 + 31400) at Aug 31 2023, 2024 and 2025 (expected around 15,000 each year). Hygiene's files use the same tables when they arrive.
""", priority=2)


def seed(engine):
    with engine.begin() as conn:
        for key, persona, resp in AGENTS:
            done = conn.execute(text("UPDATE agent.Agents SET Persona = :p, Responsibilities = :r WHERE AgentKey = :k"),
                                {"k": key, "p": persona, "r": resp}).rowcount
            if not done:
                conn.execute(text("INSERT INTO agent.Agents (AgentKey, Persona, Responsibilities) VALUES (:k, :p, :r)"),
                             {"k": key, "p": persona, "r": resp})
        n_new = n_upd = 0
        for p in P:
            params = {"sk": p["seed_key"], "th": p["thread"], "au": p["author"], "ty": p["mtype"], "ar": p["area"],
                      "co": p["company"], "fy": p["fy"], "ti": p["title"], "bo": p["body"], "am": p["amount"],
                      "ev": p["evidence"], "st": p["status"], "pr": p["priority"]}
            done = conn.execute(text("""
                UPDATE agent.Messages SET ThreadKey = :th, Author = :au, MsgType = :ty, Area = :ar, Company = :co,
                       FiscalYear = :fy, Title = :ti, Body = :bo, Amount = :am, Evidence = :ev, Status = :st,
                       Priority = :pr, UpdatedAt = SYSUTCDATETIME()
                WHERE SeedKey = :sk"""), params).rowcount
            if done:
                n_upd += 1
            else:
                conn.execute(text("""
                    INSERT INTO agent.Messages (SeedKey, ThreadKey, Author, MsgType, Area, Company, FiscalYear, Title,
                                                Body, Amount, Evidence, Status, Priority)
                    VALUES (:sk, :th, :au, :ty, :ar, :co, :fy, :ti, :bo, :am, :ev, :st, :pr)"""), params)
                n_new += 1
        links = _link_posts(conn)
    print(f"✅ board seeded: {n_new} new, {n_upd} updated posts; {len(AGENTS)} agents")
    for line in links:
        print("   " + line)


def _find_targets(conn, spec):
    """MessageIDs a `supersedes` / `parent` spec points at: a seed key, or 'amount:<n>' for un-seeded posts."""
    if spec.startswith("amount:"):
        amt = float(spec.split(":", 1)[1])
        pattern = f"%{amt:,.2f}%"
        rows = conn.execute(text("""
            SELECT MessageID FROM agent.Messages
            WHERE SeedKey IS NULL AND Status <> 'Superseded'
              AND (Amount = :amt OR Title LIKE :pat OR Body LIKE :pat)
            ORDER BY MessageID"""), {"amt": amt, "pat": pattern}).fetchall()
        return [r[0] for r in rows]
    row = conn.execute(text("SELECT MessageID FROM agent.Messages WHERE SeedKey = :k"), {"k": spec}).fetchone()
    return [row[0]] if row else []


def _link_posts(conn):
    """Second pass: SupersedesID / ParentID, and mark superseded posts. Returns report lines."""
    out = []
    for p in P:
        if not (p["supersedes"] or p["parent"]):
            continue
        me = conn.execute(text("SELECT MessageID FROM agent.Messages WHERE SeedKey = :k"), {"k": p["seed_key"]}).scalar()
        if p["parent"]:
            par = _find_targets(conn, p["parent"])
            conn.execute(text("UPDATE agent.Messages SET ParentID = :par WHERE MessageID = :me"),
                         {"par": par[0] if par else None, "me": me})
        if p["supersedes"]:
            targets = [t for t in _find_targets(conn, p["supersedes"]) if t != me]
            if not targets:
                # keep a link made on an earlier run (the old post is Superseded by now, so not found again)
                linked = conn.execute(text("SELECT SupersedesID FROM agent.Messages WHERE MessageID = :me"), {"me": me}).scalar()
                out.append(f"{p['seed_key']}: " + (f"already supersedes MessageID {linked}" if linked
                                                    else f"nothing found to supersede for '{p['supersedes']}'"))
                continue
            conn.execute(text("UPDATE agent.Messages SET SupersedesID = :t WHERE MessageID = :me"),
                         {"t": max(targets), "me": me})
            for t in targets:
                conn.execute(text("""UPDATE agent.Messages SET Status = 'Superseded', UpdatedAt = SYSUTCDATETIME()
                                     WHERE MessageID = :t"""), {"t": t})
            out.append(f"{p['seed_key']} supersedes MessageID {', '.join(map(str, targets))} ('{p['supersedes']}')")
    return out


if __name__ == "__main__":
    from sql_helper import ensure_schema, get_engine
    eng = get_engine()
    ensure_schema(eng)
    seed(eng)
