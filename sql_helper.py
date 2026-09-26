"""
sql_helper.py

Writes pipeline output to SQL Server instead of (or alongside) the
per-file Excel workbooks produced by excel_helper.py. Mirrors the same
Summary/Transactions structure, plus the Stage 2 reconciliation result,
into the fin.Accounts / fin.Statements / fin.Transactions / fin.Reconciliation
tables defined in sql/schema.sql.

Connection is controlled entirely by environment variables (see .env.example):

    DB_SERVER    e.g. "localhost\\SQLEXPRESS" or "myserver.database.windows.net"
    DB_NAME      e.g. "QbVisaAgentic"
    DB_TRUSTED   "yes" for Windows Authentication (default), "no" for SQL auth
    DB_USER      only needed when DB_TRUSTED=no
    DB_PASSWORD  only needed when DB_TRUSTED=no
    DB_DRIVER    ODBC driver name, default "ODBC Driver 18 for SQL Server"

First-time setup:
    1. Install the ODBC Driver for SQL Server on your machine.
    2. pip install -r requirements.txt   (adds pyodbc + SQLAlchemy)
    3. Fill in .env with your DB_* values.
    4. Run once:  python sql_helper.py --init-schema
       (or just run the pipeline - ensure_schema() runs automatically
       on first write and is a no-op after that).
"""

import os
import argparse

import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

from excel_helper import parse_extraction_output

load_dotenv()

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "sql", "schema.sql")

_engine = None  # module-level cache so we don't rebuild the pool every call


def get_engine():
    """Builds (and caches) a SQLAlchemy engine for SQL Server via pyodbc."""
    global _engine
    if _engine is not None:
        return _engine

    server = os.getenv("DB_SERVER")
    database = os.getenv("DB_NAME")
    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    trusted = os.getenv("DB_TRUSTED", "yes").lower() == "yes"

    if not server or not database:
        raise RuntimeError(
            "DB_SERVER and DB_NAME must be set (in your .env file or the "
            "environment) before sql_helper can connect."
        )

    if trusted:
        odbc_str = (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            f"Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
    else:
        user = os.getenv("DB_USER")
        password = os.getenv("DB_PASSWORD")
        if not user or not password:
            raise RuntimeError("DB_TRUSTED=no requires DB_USER and DB_PASSWORD.")
        odbc_str = (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            f"UID={user};PWD={password};TrustServerCertificate=yes;"
        )

    connection_url = f"mssql+pyodbc:///?odbc_connect={odbc_str}"
    _engine = create_engine(connection_url, fast_executemany=True)
    return _engine


_schema_ready = False


def ensure_schema(engine, schema_path=SCHEMA_PATH):
    """
    Runs sql/schema.sql. Every statement in that file is guarded with
    IF OBJECT_ID(...) IS NULL, so this is safe to call on every run -
    it only actually creates anything the first time.
    """
    global _schema_ready
    if _schema_ready:
        return

    with open(schema_path, "r", encoding="utf-8") as f:
        script = f.read()

    # SQL Server's GO batch separator isn't valid T-SQL for a single
    # execute() call, so split on it and run each batch separately.
    batches = [b.strip() for b in script.split("\nGO") if b.strip()]

    with engine.begin() as conn:
        for batch in batches:
            conn.execute(text(batch))

    _schema_ready = True
    print("✅ SQL schema verified/created (fin.* statement tables + qb.* QuickBooks ledger tables)")


def get_or_create_account(engine, account_name, account_type,
                           institution=None, last_four=None, currency="CAD"):
    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT AccountID FROM fin.Accounts
                WHERE AccountName = :name AND AccountType = :atype
            """),
            {"name": account_name, "atype": account_type},
        ).fetchone()

        if existing:
            return existing[0]

        result = conn.execute(
            text("""
                INSERT INTO fin.Accounts (AccountName, AccountType, Institution, LastFour, Currency)
                OUTPUT INSERTED.AccountID
                VALUES (:name, :atype, :inst, :last4, :curr)
            """),
            {
                "name": account_name, "atype": account_type,
                "inst": institution, "last4": last_four, "curr": currency,
            },
        ).fetchone()
        return result[0]


def upsert_statement(engine, account_id, summary_row, source_file):
    """
    Inserts (or, on a re-run of the same statement, updates) the one
    Statements row for this AccountID + StatementDate. Also clears any
    previously-loaded Transactions/Reconciliation rows for that statement
    so a re-run never duplicates data. Returns the StatementID.
    """
    params = {
        "aid": account_id,
        "sdate": summary_row["Statement Date"],
        "opening": float(summary_row["Opening Balance"]),
        "payments": float(summary_row["Total Payments"]),
        "purchases": float(summary_row["Total Purchases"]),
        "interest": float(summary_row["Total Interest"]),
        "ending": float(summary_row["Ending Balance"]),
        "src": source_file,
    }

    with engine.begin() as conn:
        existing = conn.execute(
            text("""
                SELECT StatementID FROM fin.Statements
                WHERE AccountID = :aid AND StatementDate = :sdate
            """),
            {"aid": account_id, "sdate": params["sdate"]},
        ).fetchone()

        if existing:
            statement_id = existing[0]
            conn.execute(
                text("""
                    UPDATE fin.Statements
                    SET OpeningBalance = :opening, TotalPayments = :payments,
                        TotalPurchases = :purchases, TotalInterest = :interest,
                        EndingBalance = :ending, SourceFile = :src
                    WHERE StatementID = :sid
                """),
                {**params, "sid": statement_id},
            )
            # Re-processing this statement: drop the old children so we
            # don't end up with duplicate transaction/reconciliation rows.
            conn.execute(text("DELETE FROM fin.Transactions WHERE StatementID = :sid"),
                         {"sid": statement_id})
            conn.execute(text("DELETE FROM fin.Reconciliation WHERE StatementID = :sid"),
                         {"sid": statement_id})
        else:
            result = conn.execute(
                text("""
                    INSERT INTO fin.Statements
                        (AccountID, StatementDate, OpeningBalance, TotalPayments,
                         TotalPurchases, TotalInterest, EndingBalance, SourceFile)
                    OUTPUT INSERTED.StatementID
                    VALUES (:aid, :sdate, :opening, :payments, :purchases, :interest, :ending, :src)
                """),
                params,
            ).fetchone()
            statement_id = result[0]

    return statement_id


def insert_transactions(engine, statement_id, account_id, trans_df, source_type="Visa"):
    if trans_df is None or trans_df.empty:
        return

    records = []
    for _, row in trans_df.iterrows():
        records.append({
            "sid": statement_id,
            "aid": account_id,
            "tdate": row["Transaction Date"],
            "pdate": row.get("Posting Date"),
            "desc": str(row["Description"]),
            "amount": float(row["Amount"]),
            "stype": source_type,
        })

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO fin.Transactions
                    (StatementID, AccountID, TransactionDate, PostingDate, Description, Amount, SourceType)
                VALUES (:sid, :aid, :tdate, :pdate, :desc, :amount, :stype)
            """),
            records,
        )
    print(f"   ✅ Inserted {len(records)} transaction row(s) into fin.Transactions")


def save_reconciliation(engine, statement_id, recon_row):
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO fin.Reconciliation
                    (StatementID, NetChange, CalculatedEndingBalance, ActualEndingBalance, Variance)
                VALUES (:sid, :net, :calc, :actual, :var)
            """),
            {
                "sid": statement_id,
                "net": float(recon_row["Net Transaction Change"]),
                "calc": float(recon_row["Calculated Ending Balance"]),
                "actual": float(recon_row["Actual Ending Balance"]),
                "var": float(recon_row["Difference (Variance)"]),
            },
        )


def save_pipeline_output_to_sql(raw_output, source_file, recon_df,
                                 account_name, account_type="Visa"):
    """
    High-level entry point, called from reflect0.py's validation_node
    right alongside save_to_excel(). Parses the same raw LLM output,
    then writes Account -> Statement -> Transactions -> Reconciliation.

    Returns the StatementID written, or None if parsing failed.
    """
    engine = get_engine()
    ensure_schema(engine)

    summary_df, trans_df = parse_extraction_output(raw_output)
    if summary_df is None:
        print("❌ Skipping SQL write - could not parse LLM output")
        return None

    account_id = get_or_create_account(engine, account_name, account_type)

    summary_row = summary_df.iloc[0]
    statement_id = upsert_statement(engine, account_id, summary_row, source_file)

    insert_transactions(engine, statement_id, account_id, trans_df, source_type=account_type)

    if recon_df is not None and not recon_df.empty:
        save_reconciliation(engine, statement_id, recon_df.iloc[0])

    print(f"🏁 Statement {summary_row['Statement Date']} saved to SQL Server (StatementID={statement_id})")
    return statement_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQL Server helper for QbVisaAgenticSupport")
    parser.add_argument("--init-schema", action="store_true",
                         help="Create fin.Accounts/Statements/Transactions/Reconciliation if missing, then exit.")
    args = parser.parse_args()

    if args.init_schema:
        eng = get_engine()
        ensure_schema(eng)
        print("Schema check complete.")
    else:
        parser.print_help()
