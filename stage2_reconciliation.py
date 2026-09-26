import pandas as pd
import os

# --- CONFIGURATION ---
OUTPUT_DIR = "output_reports"


def run_stage2_reconciliation(input_excel):
    print("🔬 Starting Stage 2: Reconciliation Analysis...")

    # 1. Load Data
    try:
        # Using the sheet names as you defined them
        summary_df = pd.read_excel(input_excel, sheet_name="Summary", nrows=1)

        trans_df = pd.read_excel(input_excel, sheet_name="Transactions")
    except Exception as e:
        print(f"❌ Could not find sheets. Did Stage 1 finish? Error: {e}")
        return

    # 2. Clean Currency and Dates
    for df in [summary_df, trans_df]:
        money_cols = [c for c in df.columns if any(k in c.lower() for k in ['balance', 'amount', 'total'])]
        for col in money_cols:
            df[col] = pd.to_numeric(df[col].astype(str).replace({r'\$': '', ',': ''}, regex=True),
                                    errors='coerce').fillna(0.0)

        df['Statement Date'] = pd.to_datetime(df['Statement Date'], errors='coerce')

    # 3. Aggregate Transactions per Statement
    net_changes = trans_df.groupby('Statement Date')['Amount'].sum().reset_index()
    net_changes.rename(columns={'Amount': 'Net_Change'}, inplace=True)

    # 4. Reconciliation Math
    summary_df = summary_df.sort_values(by='Statement Date')
    merged = pd.merge(summary_df, net_changes, on='Statement Date', how='left').fillna(0)

    recon_rows = []

    # --- UPDATED LOGIC START ---
    for _, row in merged.iterrows():
        # Dynamically pull the Opening Balance from the PDF summary data
        current_opening = row['Opening Balance']
        actual_ending = row['Ending Balance']
        net_change = row['Net_Change']

        # The math check: Opening + Activity should = Ending
        calculated_ending = current_opening + net_change
        variance = calculated_ending - actual_ending

        recon_rows.append({
            "Statement Date": row['Statement Date'].strftime('%Y-%m-%d'),
            "Opening Balance": round(current_opening, 2),
            "Net Transaction Change": round(net_change, 2),
            "Calculated Ending Balance": round(calculated_ending, 2),
            "Actual Ending Balance": round(actual_ending, 2),
            "Difference (Variance)": round(variance, 2)
        })
    # --- UPDATED LOGIC END ---

    # 5. Save and Report
    recon_df = pd.DataFrame(recon_rows)
    output_path = input_excel.replace(".xlsx", "_Reconciled.xlsx")
    recon_df.to_excel(output_path, index=False)

    print(f"✅ Stage 2 Complete. Report saved: {output_path}")

    # Check for math errors
    total_variance = recon_df['Difference (Variance)'].abs().sum()
    if total_variance > 0:
        print(f"⚠️ Warning: Total Variance of ${total_variance:.2f} detected!")
    else:
        print("💎 Books balance perfectly (0.00 difference).")

    return recon_df


# Execution (standalone): reconcile an existing *_Analysis.xlsx
#   python stage2_reconciliation.py output_reports/<statement>_Analysis.xlsx
# Normally this runs inside reflect0.py's validation node instead.
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python stage2_reconciliation.py <path to *_Analysis.xlsx>")
        sys.exit(1)

    master_file = sys.argv[1]
    if not os.path.exists(master_file):
        # Allow just the file name, looked up in output_reports/
        master_file = os.path.join(OUTPUT_DIR, sys.argv[1])

    if os.path.exists(master_file):
        run_stage2_reconciliation(master_file)
    else:
        print(f"❌ Error: {sys.argv[1]} not found.")
        sys.exit(1)
