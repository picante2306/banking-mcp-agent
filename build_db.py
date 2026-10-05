"""Build banking.db from the CSV source tables.

Run once before starting the MCP server:
    python build_db.py
"""
import sqlite3
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "banking.db"
DATA = ROOT / "data"

TABLES = {
    "customers":    ["customer_id"],
    "branches":     ["branch_id"],
    "products":     ["product_id"],
    "transactions": ["customer_id", "branch_id", "product_id", "transaction_date"],
    "complaints":   ["customer_id", "branch_id", "raised_date"],
}


def main() -> None:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)

    for table, index_cols in TABLES.items():
        df = pd.read_csv(DATA / f"{table}.csv")
        df.to_sql(table, con, index=False)
        for col in index_cols:
            con.execute(f"CREATE INDEX idx_{table}_{col} ON {table}({col})")
        print(f"  {table:<14} {len(df):>6,} rows")

    con.commit()
    con.close()
    print(f"\nBuilt {DB} ({DB.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    print("Building banking.db\n")
    main()
