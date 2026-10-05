"""Tests for the MCP server's tools and SQL guard.

Runs without an API key — the tools are plain functions, so they can be
verified independently of any model.

    python test_tools.py
"""
import sys

import server
from server import UnsafeQuery, check_query

passed = failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def rejects(sql: str) -> bool:
    try:
        check_query(sql)
        return False
    except UnsafeQuery:
        return True


print("\nSQL guard — must reject")
for q in [
    "DROP TABLE customers",
    "DELETE FROM transactions WHERE amount > 0",
    "UPDATE customers SET segment='Premium'",
    "INSERT INTO branches VALUES (26,'x','y','z',2020)",
    "SELECT 1; DROP TABLE customers",
    "SELECT * FROM customers -- ; DROP TABLE customers\n; DROP TABLE branches",
    "/* comment */ DROP TABLE customers",
    "PRAGMA table_info(customers)",
    "ATTACH DATABASE '/etc/passwd' AS leak",
    "",
]:
    check(repr(q[:45]), rejects(q))

print("\nSQL guard — must allow")
for q in [
    "SELECT COUNT(*) FROM customers",
    "select segment, count(*) from customers group by segment",
    "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
    "SELECT * FROM customers LIMIT 5;",
    "SELECT * FROM customers -- trailing comment",
]:
    try:
        check_query(q)
        check(repr(q[:45]), True)
    except UnsafeQuery as e:
        check(repr(q[:45]), False, str(e))

print("\nTools return correct values")

tables = server.list_tables()
check("list_tables names all five tables",
      all(t in tables for t in
          ["customers", "branches", "products", "transactions", "complaints"]))
check("list_tables shows 60,000 transactions", "60,000" in tables)

desc = server.describe_table("transactions")
check("describe_table lists key columns",
      all(c in desc for c in ["transaction_id", "amount", "channel", "status"]))
check("describe_table rejects unknown table",
      "No table named" in server.describe_table("nope"))
check("describe_table rejects injection",
      "Invalid table name" in server.describe_table("customers; DROP TABLE x"))

seg = server.segment_summary()
check("segment_summary: Premium avg transaction 80160", "80160" in seg, seg[:120])
check("segment_summary: Premium is 29.0% of value", "29.0" in seg)
check("segment_summary: Basic is 39.3% of customers", "39.3" in seg)

age = server.channel_by_age()
check("channel_by_age: 18-34 mobile 61.6%", "61.6" in age, age[:200])
check("channel_by_age: 55+ mobile 12.5%", "12.5" in age)
check("channel_by_age: 55+ branch 45.9%", "45.9" in age)

sla = server.sla_breakdown()
check("sla_breakdown: Loan Query worst at 80.0%",
      "Loan Query" in sla and "80.0" in sla, sla[:200])
check("sla_breakdown by region works",
      "region" in server.sla_breakdown("region"))
check("sla_breakdown rejects bad argument",
      "must be" in server.sla_breakdown("branch"))

print("\nrun_sql end to end")
res = server.run_sql("SELECT COUNT(*) AS n FROM transactions WHERE status='Failed'")
check("counts 1561 failed transactions", "1561" in res, res[:120])
check("run_sql blocks a write",
      "rejected" in server.run_sql("DELETE FROM customers").lower())
check("run_sql reports a bad column cleanly",
      "SQL error" in server.run_sql("SELECT nonexistent FROM customers"))
check("run_sql caps result size",
      "Truncated" in server.run_sql("SELECT * FROM transactions"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
