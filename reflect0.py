import os
import pathlib
import pymupdf4llm
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

# Must happen before the SAVE_TO_SQL/ACCOUNT_NAME os.getenv() calls below -
# without this, .env is never actually read and SAVE_TO_SQL silently stays
# false no matter what your .env says (sql_helper.py/qb_loader.py load
# their own .env too, but only once imported - which itself depends on
# SAVE_TO_SQL already having been read correctly).
load_dotenv()

# Import your custom modules
from accountant_brain import ask_brain
from stage2_reconciliation import run_stage2_reconciliation
from excel_helper import save_to_excel
from debug_helper import save_debug_log
from statement_preprocessor import build_filtered_statement

import shutil

# --- 1. DIRECTORY CONFIGURATION ---
INPUT_DIR = "input_pdfs"
DEBUG_DIR = "debug_logs"
OUTPUT_DIR = "output_reports"

# --- SQL SERVER OUTPUT (opt-in) ---
# Set SAVE_TO_SQL=true in your .env once sql/schema.sql has been reviewed
# and your DB_* connection variables are filled in. Until then the pipeline
# behaves exactly as before (Excel only).
SAVE_TO_SQL = os.getenv("SAVE_TO_SQL", "false").lower() == "true"

# Every statement processed by this file today is this one Visa account.
# When the Bank/QuickBooks pipelines are added, this becomes a per-source
# value instead of a constant - fin.Accounts already supports that.
ACCOUNT_NAME = os.getenv("ACCOUNT_NAME", "TD Aeroplan Visa Infinite Privilege")
ACCOUNT_TYPE = "Visa"

if SAVE_TO_SQL:
    from sql_helper import save_pipeline_output_to_sql

# WIPE AND RECREATE: Ensures no stale data remains from previous failed runs
for folder in [DEBUG_DIR, OUTPUT_DIR]:
    os.makedirs(folder, exist_ok=True) # Recreates the empty folder

# Ensure Input exists (but don't delete this one, obviously!)
os.makedirs(INPUT_DIR, exist_ok=True)

def safe_clear_folders():
    for folder in [DEBUG_DIR, OUTPUT_DIR]:
        if os.path.exists(folder):
            print(f"🧹 Attempting to clear {folder}...")
            # We iterate through files instead of deleting the folder
            # This avoids the "Access Denied" error on the folder itself
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    print(f"⚠️ Skipping locked file: {filename} (Close Excel!)")
        else:
            os.makedirs(folder, exist_ok=True)

# Run the safe clear
safe_clear_folders()


# --- 2. THE STATE ---
class AccountantState(TypedDict):
    pdf_path: str
    raw_md_text: str
    filtered_text: str
    extraction_output: str
    failed_extraction: str
    variance: float
    iteration: int
    report_path: str
    explanation: str


# --- 3. NODE 1: PDF Extractor ---
def pdf_extractor_node(state: AccountantState):
    print(f"\n📄 Node 1: Extracting PDF -> {state['pdf_path']}")
    md_text = pymupdf4llm.to_markdown(state['pdf_path'])
    save_debug_log(md_text, state['pdf_path'], suffix="_raw_md")
    return {"raw_md_text": md_text}

# --- 3.1 NODE 1: PDF Filter ---
def statement_filter_node(state: AccountantState):
    print("🧹 Node 1B: Filtering statement data")

    filtered_text = build_filtered_statement(
        state["raw_md_text"]
    )

    save_debug_log(
        filtered_text,
        state["pdf_path"],
        suffix="_FILTERED_STATEMENT"
    )

    print(
        f"   Raw text size: "
        f"{len(state['raw_md_text']):,} characters"
    )

    print(
        f"   Filtered size: "
        f"{len(filtered_text):,} characters"
    )

    return {
        "filtered_text": filtered_text
    }


# --- 4. NODE 2: Hardened LLM Clerk ---
def llm_extraction_node(state: AccountantState):
    iteration_label = f"Attempt_{state['iteration'] + 1}"
    print(f"🧠 Node 2: AI Analysis ({iteration_label})")

    # PRIMARY FIX: Instruction Primacy
    # We put the error feedback at the very TOP of the prompt so it's the first thing the LLM sees.
    if state['variance'] != 0:
        instruction_header = (
            f"### RE-EXTRACTION REQUIRED\n"
            f"Your previous attempt failed with a math variance of ${state['variance']}.\n"
            f"FIX: You may have missed a transaction.\n"
            f"Please re-scan the entire document and ensure the math balances.\n\n"
            f"### DATA:\n"
        )
        prompt = instruction_header + state['filtered_text']
    else:
        instruction_header = (
            f"### DATA:\n"
        )
        prompt = instruction_header + state['filtered_text']

    save_debug_log(prompt, state['pdf_path'], suffix=f"_{iteration_label}_INPUT")

    # Call DeepSeek-8b
    result = ask_brain(prompt, "4")

    save_debug_log(result, state['pdf_path'], suffix=f"_{iteration_label}_OUTPUT")

    updates = {
        "extraction_output": result,
        "iteration": state['iteration'] + 1,
        "variance": 0.0  # Reset variance for the next validation check
    }

    # Store only the VERY FIRST failure for the reflection node
    if state['iteration'] == 0:
        updates["failed_extraction"] = result

    return updates


# --- 5. NODE 3: The Auditor ---
def validation_node(state: AccountantState):
    print("🔢 Node 3: Mathematical Validation")

    file_stem = pathlib.Path(state['pdf_path']).stem
    target_excel_path = os.path.join(OUTPUT_DIR, f"{file_stem}_Analysis.xlsx")

    save_to_excel(state['extraction_output'], target_excel_path)

    # Perform Stage 2 Logic (Opening + Change = Ending)
    recon_df = run_stage2_reconciliation(target_excel_path)
    total_variance = recon_df['Difference (Variance)'].abs().sum()

    if SAVE_TO_SQL:
        try:
            save_pipeline_output_to_sql(
                raw_output=state['extraction_output'],
                source_file=os.path.basename(state['pdf_path']),
                recon_df=recon_df,
                account_name=ACCOUNT_NAME,
                account_type=ACCOUNT_TYPE,
            )
        except Exception as e:
            # Never let a DB hiccup take down the Excel pipeline that
            # already succeeded above.
            print(f"⚠️ SQL write failed (Excel report is still saved): {e}")

    return {
        "variance": round(total_variance, 2),
        "report_path": target_excel_path
    }


# --- 6. NODE 4: Isolated Reflection (The "Post-Mortem") ---
def reflection_node(state: AccountantState):
    print("🕵️ Node 4: Diagnostic Reflection (Analyzing the gap)")

    # We provide a clean, dedicated prompt for the diagnostic task
    diagnostic_prompt = f"""
    ### DIAGNOSTIC AUDIT TASK
    Your first attempt at this PDF failed by ${state['variance']}. 
    Your subsequent attempt was 100% mathematically correct.

    ### DATA COMPARISON:
    - FAILED DATA: {state['failed_extraction']}
    - CORRECT DATA: {state['extraction_output']}

    ### ASSIGNMENT:
    Identify the specific line item or sign error that caused the initial failure. 
    Explain why it was missed initially and why it was captured later.
    """

    explanation = "N/A"
        ###ask_brain(diagnostic_prompt, "1"))
    save_debug_log(explanation, state['pdf_path'], suffix="_DIAGNOSTIC_WHY")

    return {"explanation": explanation}


# --- 7. ROUTING LOGIC ---
def should_continue(state: AccountantState):
    if state['variance'] == 0:
        # Move to reflection ONLY if a correction actually occurred
        if state['iteration'] > 1:
            return "reflect"
        return END
    if state['iteration'] >= 2:
        print("⚠️ Max retries reached. Ending with variance.")
        print("🛑 MAX RETRIES REACHED. Running Failure Diagnostic...")
        return "reflect"  # Force it to explain the failure!

    return "llm_extract"


# --- BUILD GRAPH ---
workflow = StateGraph(AccountantState)
workflow.add_node("pdf_extract", pdf_extractor_node)
workflow.add_node("llm_extract", llm_extraction_node)
workflow.add_node("validate", validation_node)
workflow.add_node("reflect", reflection_node)

workflow.add_node(
    "statement_filter",
    statement_filter_node
)

workflow.add_edge(START, "pdf_extract")
workflow.add_edge("pdf_extract", "statement_filter")

workflow.add_edge("statement_filter", "llm_extract")

workflow.add_edge("llm_extract", "validate")
workflow.add_conditional_edges("validate", should_continue)
workflow.add_edge("reflect", END)

app = workflow.compile()

# --- 8. EXECUTION ---
if __name__ == "__main__":
    pdf_filename = "TD_AEROPLAN_VISA_INFINITE_PRIVILEGE_6761_Apr_07-2025.pdf"
    full_path = os.path.join(INPUT_DIR, pdf_filename)

    if os.path.exists(full_path):
        initial_inputs = {
            "pdf_path": full_path,
            "raw_md_text": "",
            "filtered_text": "",
            "extraction_output": "",
            "failed_extraction": "",
            "variance": 0.0,
            "iteration": 0,
            "report_path": "",
            "explanation": ""
        }

        final_state = app.invoke(initial_inputs)
        print(f"\n🏆 Final Status: {'Balanced' if final_state['variance'] == 0 else 'Unbalanced'}")
        print(f"📊 Final Variance: ${final_state['variance']}")
        print(f"🔄 Total Tries: {final_state['iteration']}")
    else:
        print(f"❌ File not found: {full_path}")