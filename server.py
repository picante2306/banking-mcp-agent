"""MCP server exposing a retail banking database as agent tools.

Six tools: three generic (schema discovery + guarded SQL) and three domain
analyses that encode business logic an agent should not have to rediscover.

Run standalone:   python server.py
Used by an agent: see agent.py
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

# The SDK renamed FastMCP to MCPServer in mcp 2.0. Support both, so this runs
# on whichever version is installed.
try:                                                    # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:                             # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

DB = Path(__file__).parent / "banking.db"
MAX_ROWS = 200

mcp = _Server("banking-analytics")


# ─────────────────────────── SQL safety guard ───────────────────────────
# The database is read-only to the agent. An LLM will occasionally emit a
# write, either from a misread question or a prompt-injected one, so the
# guard is enforced here in the server rather than trusted to the prompt.

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|ATTACH|"
    r"DETACH|PRAGMA|VACUUM|REINDEX|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class UnsafeQuery(ValueError):
    """Raised when a query is rejected before it reaches the database."""


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def check_query(sql: str) -> str:
    """Validate a query is a single read-only statement. Returns it cleaned.

    Rejects: non-SELECT statements, DDL/DML keywords, and multiple statements.
    Comments are stripped first so they cannot hide a keyword.
    """
    cleaned = _strip_sql_comments(sql).strip().rstrip(";").strip()

    if not cleaned:
        raise UnsafeQuery("Empty query.")

    if ";" in cleaned:
        raise UnsafeQuery("Multiple statements are not allowed; send one SELECT.")

    head = cleaned.lstrip("( \n\t")
    if not re.match(r"^(SELECT|WITH)\b", head, re.IGNORECASE):
        raise UnsafeQuery("Only SELECT and WITH queries are allowed.")

    if (found := _FORBIDDEN.search(cleaned)) is not None:
        raise UnsafeQuery(
            f"'{found.group(0).upper()}' is not permitted. This database is read-only."
        )

    return cleaned


def _connect() -> sqlite3.Connection:
    if not DB.exists():
        raise FileNotFoundError(f"{DB.name} not found. Run: python build_db.py")
    # Opened read-only at the driver level as a second line of defence.
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows_to_table(rows: list[sqlite3.Row], truncated: bool) -> str:
    """Render rows as a compact text table. LLMs read these more reliably
    than JSON, and they cost fewer tokens."""
    if not rows:
        return "No rows returned."

    cols = list(rows[0].keys())
    data = [[("" if r[c] is None else str(r[c])) for c in cols] for r in rows]
    widths = [
        max(len(c), *(len(row[i]) for row in data)) for i, c in enumerate(cols)
    ]

    out = [
        " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)),
        "-+-".join("-" * w for w in widths),
    ]
    out += [
        " | ".join(row[i].ljust(widths[i]) for i in range(len(cols))) for row in data
    ]
    out.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
    if truncated:
        out.append(f"Truncated to {MAX_ROWS} rows — add LIMIT or aggregate.")
    return "\n".join(out)


def _query(sql: str, params: tuple = ()) -> str:
    con = _connect()
    try:
        rows = con.execute(sql, params).fetchmany(MAX_ROWS + 1)
    finally:
        con.close()
    truncated = len(rows) > MAX_ROWS
    return _rows_to_table(rows[:MAX_ROWS], truncated)


# ─────────────────────────── Generic tools ───────────────────────────

@mcp.tool()
def list_tables() -> str:
    """List every table in the banking database with its row count.

    Call this first to discover what data is available.
    """
    con = _connect()
    try:
        names = [
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        lines = ["table            rows", "---------------- --------"]
        for n in names:
            count = con.execute(f"SELECT COUNT(*) AS c FROM {n}").fetchone()["c"]
            lines.append(f"{n:<16} {count:>8,}")
    finally:
        con.close()
    return "\n".join(lines)


@mcp.tool()
def describe_table(table: str) -> str:
    """Show the columns, types and three sample rows for one table.

    Call this before writing SQL against a table you have not queried yet.

    Args:
        table: table name, e.g. 'transactions'
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        return "Invalid table name."

    con = _connect()
    try:
        cols = con.execute(f"PRAGMA table_info({table})").fetchall()
        if not cols:
            return f"No table named '{table}'. Use list_tables() to see what exists."
        lines = [f"{table}", "", "column                type", "--------------------- ---------"]
        lines += [f"{c['name']:<21} {c['type'] or 'TEXT'}" for c in cols]
        sample = con.execute(f"SELECT * FROM {table} LIMIT 3").fetchall()
    finally:
        con.close()

    return "\n".join(lines) + "\n\nSample rows:\n" + _rows_to_table(sample, False)


@mcp.tool()
def run_sql(query: str) -> str:
    """Run a read-only SQL query against the banking database and return the rows.

    Only SELECT and WITH statements are accepted. Writes, DDL and multiple
    statements are rejected. Results are capped at 200 rows, so aggregate
    rather than selecting raw rows when the answer is a summary.

    SQLite dialect. Dates are TEXT in 'YYYY-MM-DD' form — use
    strftime('%Y-%m', transaction_date) to group by month.

    Args:
        query: a single SELECT or WITH statement
    """
    try:
        safe = check_query(query)
    except UnsafeQuery as e:
        return f"Query rejected: {e}"

    try:
        return _query(safe)
    except sqlite3.Error as e:
        return f"SQL error: {e}\n\nCheck column names with describe_table()."


# ─────────────────────────── Domain tools ───────────────────────────
# These encode analysis the business already agreed on. Exposing them as
# tools means the agent returns the same number every time, instead of
# re-deriving the definition of "customer value" on each question.

@mcp.tool()
def segment_summary() -> str:
    """Customer value concentration: share of customers vs share of transaction
    value by segment, with average transaction size.

    Use this for any question about which customers are worth most, value
    concentration, or segment profitability.
    """
    return _query(
        """
        WITH seg AS (
            SELECT c.segment,
                   COUNT(DISTINCT c.customer_id) AS customers,
                   SUM(t.amount)                 AS value,
                   AVG(t.amount)                 AS avg_txn
            FROM customers c
            JOIN transactions t ON t.customer_id = c.customer_id
            GROUP BY c.segment
        )
        SELECT segment,
               customers,
               ROUND(100.0 * customers / (SELECT COUNT(*) FROM customers), 1)
                   AS pct_of_customers,
               ROUND(100.0 * value / (SELECT SUM(value) FROM seg), 1)
                   AS pct_of_value,
               ROUND(avg_txn)                    AS avg_transaction
        FROM seg
        ORDER BY value DESC
        """
    )


@mcp.tool()
def channel_by_age() -> str:
    """Transaction channel mix (% of transactions) broken down by customer age band.

    Use this for questions about digital adoption, mobile vs branch usage, or
    which customers still use physical branches.
    """
    return _query(
        """
        WITH banded AS (
            SELECT CASE WHEN c.age <= 34 THEN '18-34'
                        WHEN c.age <= 44 THEN '35-44'
                        WHEN c.age <= 54 THEN '45-54'
                        ELSE '55+' END AS age_band,
                   t.channel
            FROM transactions t
            JOIN customers c ON c.customer_id = t.customer_id
        )
        SELECT age_band,
               ROUND(100.0 * SUM(channel='Mobile')      / COUNT(*), 1) AS mobile_pct,
               ROUND(100.0 * SUM(channel='Branch')      / COUNT(*), 1) AS branch_pct,
               ROUND(100.0 * SUM(channel='ATM')         / COUNT(*), 1) AS atm_pct,
               ROUND(100.0 * SUM(channel='Net Banking') / COUNT(*), 1) AS netbanking_pct,
               COUNT(*) AS transactions
        FROM banded
        GROUP BY age_band
        ORDER BY age_band
        """
    )


@mcp.tool()
def sla_breakdown(group_by: str = "category") -> str:
    """Complaint volume, average resolution days and SLA breach rate.

    Use this for questions about service quality, SLA performance, or which
    complaint types are handled worst.

    Args:
        group_by: 'category' (default) or 'region'
    """
    if group_by not in ("category", "region"):
        return "group_by must be 'category' or 'region'."

    if group_by == "category":
        return _query(
            """
            SELECT category,
                   COUNT(*)                                  AS complaints,
                   ROUND(AVG(resolution_days), 1)            AS avg_days,
                   ROUND(100.0 * AVG(sla_breached), 1)       AS sla_breach_pct
            FROM complaints
            GROUP BY category
            ORDER BY sla_breach_pct DESC
            """
        )
    return _query(
        """
        SELECT b.region,
               COUNT(*)                                      AS complaints,
               ROUND(AVG(x.resolution_days), 1)              AS avg_days,
               ROUND(100.0 * AVG(x.sla_breached), 1)         AS sla_breach_pct
        FROM complaints x
        JOIN branches b ON b.branch_id = x.branch_id
        GROUP BY b.region
        ORDER BY sla_breach_pct DESC
        """
    )


if __name__ == "__main__":
    mcp.run()
