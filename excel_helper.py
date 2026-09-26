import re
import pandas as pd
import io
import os


def parse_extraction_output(raw_output):
    """
    Shared parser for the LLM's raw CSV output (accountant_brain.ask_brain).

    Returns (summary_df, transactions_df), both already numeric/date-cleaned,
    or (None, None) if either section can't be found. Used by both
    save_to_excel() and sql_helper.save_pipeline_output_to_sql() so the two
    output paths never drift apart on parsing logic.
    """
    # 1. Strip DeepSeek thinking if present
    clean_text = raw_output.split("</think>")[-1].strip()

    # 2. Remove markdown code fences completely
    clean_text = re.sub(r"```(?:csv|text)?", "", clean_text, flags=re.IGNORECASE)
    clean_text = clean_text.replace("```", "").strip()

    summary_header = (
        "Statement Date,Opening Balance,Total Payments,"
        "Total Purchases,Total Interest,Ending Balance"
    )

    transaction_header = (
        "Statement Date,Transaction Date,Posting Date,"
        "Description,Amount"
    )

    # 3. Find the two sections by their headers
    summary_start = clean_text.find(summary_header)
    transactions_start = clean_text.find(transaction_header)

    if summary_start == -1:
        print("❌ Error: Summary section not found in LLM output")
        return None, None

    if transactions_start == -1:
        print("❌ Error: Transactions section not found in LLM output")
        return None, None

    summary_text = clean_text[summary_start:transactions_start].strip()
    transactions_text = clean_text[transactions_start:].strip()

    summary_df = pd.read_csv(io.StringIO(summary_text))
    transactions_df = pd.read_csv(io.StringIO(transactions_text))

    # Clean currency symbols if present
    summary_df = summary_df.replace(
        {r'\$': '', r'(?<=\d),(?=\d)': ''},
        regex=True
    )
    transactions_df = transactions_df.replace(
        {r'\$': '', r'(?<=\d),(?=\d)': ''},
        regex=True
    )

    # Numeric coercion for money columns (parity with stage2_reconciliation cleaning)
    for col in ["Opening Balance", "Total Payments", "Total Purchases", "Total Interest", "Ending Balance"]:
        if col in summary_df.columns:
            summary_df[col] = pd.to_numeric(summary_df[col], errors="coerce").fillna(0.0)
    if "Amount" in transactions_df.columns:
        transactions_df["Amount"] = pd.to_numeric(transactions_df["Amount"], errors="coerce").fillna(0.0)

    # Date coercion
    if "Statement Date" in summary_df.columns:
        summary_df["Statement Date"] = pd.to_datetime(summary_df["Statement Date"], errors="coerce").dt.date
    for col in ["Statement Date", "Transaction Date", "Posting Date"]:
        if col in transactions_df.columns:
            transactions_df[col] = pd.to_datetime(transactions_df[col], errors="coerce").dt.date

    return summary_df, transactions_df


def save_to_excel(raw_output, output_path):
    summary_df, transactions_df = parse_extraction_output(raw_output)

    if summary_df is None or transactions_df is None:
        print(f"❌ Skipping Excel write for {output_path} (parse failure above)")
        return

    try:
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            summary_df.to_excel(writer, sheet_name="Summary", index=False)
            print("   ✅ Created Sheet: Summary")

            transactions_df.to_excel(writer, sheet_name="Transactions", index=False)
            print("   ✅ Created Sheet: Transactions")

        print(f"🏁 Final Excel saved: {output_path}")

    except Exception as e:
        print(f"❌ Failed to create Excel report: {e}")

def save_to_excel1(raw_output, output_path):
    # 1. Strip the DeepSeek thinking process
    clean_text = raw_output.split("</think>")[-1].strip()

    # 2. Extract every block of text inside ```csv ... ``` or ``` ... ```
    # This regex is the most reliable way to grab the actual data
    blocks = re.findall(r'```(?:csv|text|)?\n(.*?)\n```', clean_text, re.DOTALL)

    sheet_names = ["Summary", "Transactions"]

    if not blocks:
        print(f"❌ Error: No CSV blocks found in the output for {output_path}")
        return

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        for i, block_content in enumerate(blocks):
            if i < len(sheet_names):
                try:
                    # Read the block. We use sep=None so pandas can detect
                    # if the AI used commas or semicolons automatically.
                    df = pd.read_csv(io.StringIO(block_content.strip()), sep=None, engine='python')

                    # Remove any stray $ or , in the numbers so they are math-ready
                    df = df.replace({r'\$': '', r'(?<=\d),(?=\d)': ''}, regex=True)

                    df.to_excel(writer, sheet_name=sheet_names[i], index=False)
                    print(f"   ✅ Created Sheet: {sheet_names[i]}")
                except Exception as e:
                    print(f"   ⚠️ Failed to parse {sheet_names[i]}: {e}")

    print(f"🏁 Final Excel saved: {output_path}")


def create_master_report(output_dir, master_filename="Master_Accounting_Report.xlsx"):
    print("in master report")
    all_summaries = []
    all_transactions = []

    for filename in os.listdir(output_dir):
        if filename.endswith(".xlsx") and not filename.startswith("Master_"):
            file_path = os.path.join(output_dir, filename)
            try:
                summary_df = pd.read_excel(file_path, sheet_name="Summary")
                trans_df = pd.read_excel(file_path, sheet_name="Transactions")

                summary_df['Source File'] = filename
                trans_df['Source File'] = filename

                all_summaries.append(summary_df)
                all_transactions.append(trans_df)
            except Exception as e:
                print(f"⚠️ Skipping {filename} due to read error: {e}")

    if not all_summaries:
        print("❌ No files found to merge.")
        return

    master_summary = pd.concat(all_summaries, ignore_index=True)
    master_trans = pd.concat(all_transactions, ignore_index=True)

    # --- THE FIX STARTS HERE ---
    # Convert 'Statement Date' to datetime. 'errors=coerce' turns bad data into NaT
    if 'Statement Date' in master_summary.columns:
        master_summary['Statement Date'] = pd.to_datetime(master_summary['Statement Date'], errors='coerce')
        master_summary = master_summary.dropna(subset=['Statement Date']) # Remove rows with invalid dates
        master_summary = master_summary.sort_values(by='Statement Date')

    if 'Transaction Date' in master_trans.columns:
        master_trans['Transaction Date'] = pd.to_datetime(master_trans['Transaction Date'], errors='coerce')
        master_trans = master_trans.dropna(subset=['Transaction Date'])
        master_trans = master_trans.sort_values(by='Transaction Date')
    # --- THE FIX ENDS HERE ---

    master_path = os.path.join(output_dir, master_filename)
    with pd.ExcelWriter(master_path, engine='openpyxl') as writer:
        master_summary.to_excel(writer, sheet_name="Master Summary", index=False)
        master_trans.to_excel(writer, sheet_name="Master Transactions", index=False)

    print(f"\n🏆 MASTER REPORT CREATED: {master_path}")