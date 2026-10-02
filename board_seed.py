"""
board_seed.py - puts the project history (up to the handoff in Sep 2026) on the agent message board.

    python board_seed.py            # insert / update the seeded posts (safe to rerun)

Each post has a SeedKey, so rerunning updates it in place instead of adding a copy.
Later posts by the agents themselves have no SeedKey and are never touched by this script.
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
def post(key, thread, author, mtype, area, company, fy, title, body, amount=None, evidence=None, status="Open", priority=2):
    P.append(dict(seed_key=key, thread=thread, author=author, mtype=mtype, area=area, company=company, fy=fy,
                  title=title, body=body.strip(), amount=amount, evidence=evidence, status=status, priority=priority))

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
""")
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
     "FY2025 split: B6 overstated after missed payments", """
Cash (booked as paid on 22200 but not paid): Feb 10,669.85 · Mar 676.63 · Jun 599.00 · Jul 7,668.28 = **19,613.76**.
Expense (Dental visa portion vs the cycle's own charges): Mar -3,355.83 · Apr -5,721.35 · Jun -479.20 · Jul -1,567.20 · Aug -4,567.42 = **-15,691.00** (Dental over-charged).
Year: sum of B6 110,524.28 vs paid to TD 90,910.52 = the cycles' own charges 90,910.52. Started Feb 2025 (first missed payment); FY2024 unaffected.
""", amount=19613.76, evidence="split_review_FY2025.xlsx; fin.SplitMonths FY2025", priority=1)
post("dnt-fy2025-rf", "dntl-22200", "dental_agent", "Finding", "rollforward", "dntl", 2025,
     "FY2025 roll-forward: the 19,773.45 itemized", """
- Payments booked vs made: journals 8,056.37 + cheque visa portions 100,850.25 booked vs 93,396.52 paid = **-15,510.10** (the paid figure includes Lopez 2,486 which is not a group payment; ~1,617.66 of booked items cross the year boundary: cheque 23, negative GJ 63).
- Card activity not in QB: **-4,350.15** = Lopez 2,486 (washes out with its Scotiabank payment) + **Align 3,534.52 missing from QB** + Amazon 74.58 + Abeldent 124.84 + Amazon 66.62 - **personal payments against business charges 1,936.41** (Jul: 1,664.48, 152.55, 84.05, 35.33). All travel items (Air Canada, American Airlines incl. the 18,120.92 cash advances) wash out to 0.
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
     "Clean-up entries dated Aug 31 2023 (backup copy)", """
Create **39900 Suspense - pre-FY2024 differences** (Equity). No income or expense changes in any year.
- **JE-1 (required):** Dr 39900 15,772.83 / Cr 22200 15,772.83 - sets 22200 to the card balance (6,270.35 owed per the Sep 6 2023 statement). After it, the Aug 31 2024 gap should drop to ~123.68.
- JE-2 (optional): Dr 22100 7,646.88 / Cr 39900 7,646.88.
- JE-3 (optional): Dr 39900 12,500.00 / Cr 22150 12,500.00.
Posting into FY2023 needs the closing-date password. Real books: accountant's decision.
""", amount=15772.83, priority=1)
post("dnt-prop-process", "dntl-cleanup", "dental_agent", "Proposal", "cleanup", "dntl", 2026,
     "Process from FY2026: accrue the split to 22100", """
At each sheet's month-end: Dr expense accounts (DPC portions), Dr 22200 (visa portion), **Cr 22100 Due to Hygiene** (C8). When the cheque is written: Dr 22100 / Cr 10000. Timing then no longer matters; 22100 shows the unpaid split at any date. Clear 22100's old 7,646.88 first.
""")
post("dnt-prop-final", "dntl-cleanup", "dental_agent", "Task", "cleanup", "dntl", None,
     "Final deliverable: all correcting journal entries", """
Collect into one set (IIF for the backup copy): the Aug 31 2023 entries; FY2024 corrections (wrong-sign charges; 9,525.04 August accrual); FY2025 corrections (19,613.76 booked-not-paid; -15,691.00 Dental visa expense; Align 3,534.52; Abeldent/Amazon; personal payments 1,936.41; GJ 61/63).
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
    print(f"✅ board seeded: {n_new} new, {n_upd} updated posts; {len(AGENTS)} agents")


if __name__ == "__main__":
    from sql_helper import ensure_schema, get_engine
    eng = get_engine()
    ensure_schema(eng)
    seed(eng)
