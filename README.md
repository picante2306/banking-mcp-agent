# Banking Analytics MCP Agent

An AI agent that answers questions about a retail banking database in plain English.

Ask *"which complaint type is handled worst?"* and it picks the right tool, runs the query, and answers with the number and what it means.

Built on the **Model Context Protocol (MCP)** — the database is exposed as a set of tools over a standard interface, so any MCP-compatible agent can use it, not just this one.

```
$ python agent.py "which complaint type is handled worst?"

Connected to MCP server. 6 tools: list_tables, describe_table, run_sql,
segment_summary, channel_by_age, sla_breakdown

  → sla_breakdown category

Loan Query, at an 80.0% SLA breach rate and 17.8 days average resolution.

It is only the fourth largest category by volume (495 complaints), so the
service problem is not where the volume is — the worst-performing queue is
one of the smaller ones.
```

---

## Architecture

```
  agent.py                      server.py                   banking.db
  ┌──────────────┐  MCP/stdio   ┌──────────────────┐        ┌──────────┐
  │ Claude       │ ───────────► │ 6 tools          │ ─────► │ SQLite   │
  │ tool-calling │ ◄─────────── │ SQL safety guard │ ◄───── │ read-only│
  │ loop         │   results    │ domain logic     │        │ 68k rows │
  └──────────────┘              └──────────────────┘        └──────────┘
```

The agent hardcodes nothing about banking. It discovers the available tools at startup through MCP, so adding a tool to `server.py` extends the agent with no change to `agent.py`.

## The tools

**Generic — schema discovery and open query**

| Tool | What it does |
|---|---|
| `list_tables()` | Every table with its row count |
| `describe_table(table)` | Columns, types, and three sample rows |
| `run_sql(query)` | Runs a guarded read-only query |

**Domain — business logic the agent should not re-derive**

| Tool | What it does |
|---|---|
| `segment_summary()` | Customer value concentration by segment |
| `channel_by_age()` | Channel mix by customer age band |
| `sla_breakdown(group_by)` | Complaint volume, resolution time, SLA breach |

The split is deliberate. `run_sql` handles anything, but an LLM asked "what share of value comes from our best customers?" will invent its own definition of *best* and return a different number each time. The domain tools encode the definition once, so the answer is stable and matches what the business already reports.

## The SQL guard

`run_sql` accepts a query written by a language model, so it is treated as untrusted input. Before anything reaches the database:

- Comments are stripped first, so a keyword cannot hide behind `--` or `/* */`
- Only `SELECT` and `WITH` are allowed
- `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `PRAGMA`, `ATTACH` and others are rejected by keyword
- Multiple statements are rejected, which blocks `SELECT 1; DROP TABLE customers`
- Results are capped at 200 rows

The connection is additionally opened read-only at the driver level (`mode=ro`), so a query that somehow passed the guard still could not write.

`test_tools.py` includes ten rejection cases covering stacked queries, comment-hidden keywords, and `ATTACH`-based file access.

## Setup

```bash
pip install -r requirements.txt
python build_db.py          # CSVs → banking.db
python demo.py              # runs the whole stack, no API key needed
```

`demo.py` walks eight questions through the real MCP server — handshake, tool
discovery, invocation over stdio, the SQL guard refusing a write. A rule-based
router stands in for the model, so everything except the tool *choice* is real.

For answers written in plain English, the model does the choosing:

```bash
export ANTHROPIC_API_KEY=sk-...
python agent.py                         # interactive
python agent.py "your question here"    # one-shot
```

The gap between the two is instructive. The demo's rules match on keywords, so
"which region has the worst SLA?" trips both the SLA rule and the region rule
and the order has to be hand-tuned. A model reads the whole question and picks
once. That is the argument for the agent in one line.

## Tests

No API key needed — the tools are plain functions and the protocol layer is exercised directly.

```bash
python test_tools.py     # 33 tests: SQL guard, tool outputs, error handling
python test_mcp.py       # 12 tests: handshake, discovery, invocation over stdio
```

Tool outputs are asserted against known values, so a change that breaks a number fails the suite rather than silently returning a wrong answer.

## The data

A retail banking network: 5,000 customers, 25 branches, 8 products, 60,000 transactions and 3,200 complaints, spanning January 2025 to September 2026. Star schema — `transactions` is the fact table.

Synthetic, generated to model realistic retail banking patterns. No real customer data.

## Things it can answer

- Which complaint type is handled worst?
- What share of transaction value comes from Premium and Gold customers?
- Do older customers still use branches?
- How many transactions failed, and is that connected to our complaints?
- Which region has the worst SLA performance?
- Which branch has the highest transaction value per customer?

The first five map onto a domain tool. The last one does not, so the agent writes SQL for it — which is the point of having both.

## Related

The same dataset, analysed and visualised: **[retail-banking-analytics](https://github.com/picante2306/retail-banking-analytics)**

---

**Yashasvi Kumar** — [LinkedIn](https://linkedin.com/in/yashasvi-kumar-400623145)
