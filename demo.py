"""Run the agent's questions without an API key.

agent.py needs ANTHROPIC_API_KEY, because a model chooses the tools. This
script walks the same path — MCP handshake, tool discovery, tool invocation
over stdio — with a rule-based router standing in for the model, so the stack
can be seen working with nothing to configure.

What is real here: the MCP server, the protocol, the tools, the SQL guard,
the data. What is faked: only the tool choice.

    python demo.py
"""
import asyncio
import re
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# (pattern, tool, arguments) — the model's job, done with regexes.
#
# Order matters, and that is the point. "Which region has the worst SLA?"
# matches both the SLA rule and the region rule, so the more specific one has
# to be listed first; "which branches earn most" matches "value" as well as
# "branch". A model reads the whole question and picks once. These rules do
# not, which is why agent.py exists and this file is only a demo.
ROUTES = [
    # specific first
    (r"region",
     "sla_breakdown", {"group_by": "region"}),
    (r"branch counter|older customer|younger|age band|digital adoption|channel|mobile",
     "channel_by_age", {}),
    (r"complaint|sla|service delay|resolution|breach",
     "sla_breakdown", {"group_by": "category"}),
    (r"branch",
     "run_sql", {"query":
                 "SELECT b.branch_name, b.region, COUNT(*) AS txns, "
                 "ROUND(SUM(t.amount)/10000000.0, 2) AS value_cr "
                 "FROM transactions t JOIN branches b ON b.branch_id=t.branch_id "
                 "GROUP BY b.branch_name, b.region ORDER BY value_cr DESC LIMIT 5"}),
    # general after
    (r"segment|premium|gold|basic|value|worth|concentrat",
     "segment_summary", {}),
    (r"fail|status|decline",
     "run_sql", {"query":
                 "SELECT status, COUNT(*) AS transactions, "
                 "ROUND(100.0*COUNT(*)/(SELECT COUNT(*) FROM transactions),2) AS pct "
                 "FROM transactions GROUP BY status ORDER BY transactions DESC"}),
    (r"product",
     "run_sql", {"query":
                 "SELECT p.product_name, p.product_category, COUNT(*) AS txns, "
                 "ROUND(SUM(t.amount)/10000000.0, 2) AS value_cr "
                 "FROM transactions t JOIN products p ON p.product_id=t.product_id "
                 "GROUP BY p.product_name, p.product_category ORDER BY value_cr DESC"}),
]

QUESTIONS = [
    "What tables are available?",
    "Which complaint type is handled worst?",
    "What share of transaction value comes from our best customers?",
    "Do older customers still use branch counters?",
    "How many transactions failed?",
    "Which branches bring in the most value?",
    "Which region has the worst SLA performance?",
    "Can you delete the customers table?",
]


def route(question: str):
    """Pick a tool for a question. A model does this properly in agent.py."""
    q = question.lower()
    if "delete" in q or "drop" in q:
        return "run_sql", {"query": "DELETE FROM customers"}
    if "table" in q and "available" in q:
        return "list_tables", {}
    for pattern, tool, args in ROUTES:
        if re.search(pattern, q):
            return tool, args
    return "list_tables", {}


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def explain_startup_failure(exc: BaseException) -> None:
    """Turn an ExceptionGroup from the stdio transport into something readable.

    When server.py dies on startup its stderr is swallowed by the transport,
    so the user sees a wall of asyncio frames and no cause. Point them at the
    diagnostic, which runs the server directly and shows what it said.
    """
    print("\n" + "=" * 70)
    print("  The MCP server failed to start.")
    print("=" * 70)

    def leaves(e: BaseException):
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                yield from leaves(sub)
        else:
            yield e

    seen = set()
    for e in leaves(exc):
        line = f"{e.__class__.__name__}: {e}"
        if line not in seen:
            seen.add(line)
            print(f"  {line}")

    print("\n  Run this to find out why:\n")
    print("      python check_setup.py\n")
    print("  It starts the server directly and shows its real error message.")
    print("=" * 70)


async def main() -> None:
    if not (Path(__file__).parent / "banking.db").exists():
        sys.exit("banking.db not found.\nRun this first:  python build_db.py")

    params = StdioServerParameters(command=sys.executable, args=["server.py"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]

            print("=" * 74)
            print("  BANKING ANALYTICS MCP AGENT — demo (no API key required)")
            print("=" * 74)
            print(f"\nConnected over MCP/stdio. {len(names)} tools discovered:")
            for t in listed.tools:
                first_line = (t.description or "").strip().split("\n")[0]
                print(f"   {t.name:<18} {first_line}")

            for question in QUESTIONS:
                tool, args = route(question)
                shown = next(iter(args.values()), "") if args else ""
                shown = (str(shown)[:60] + "…") if len(str(shown)) > 60 else str(shown)

                print("\n" + "─" * 74)
                print(f"Q: {question}")
                print(f"   → {tool}({shown})\n")
                print(text_of(await session.call_tool(tool, args)))

            print("\n" + "=" * 74)
            print("  The last question shows the SQL guard refusing a write.")
            print("  For real answers in plain English, set ANTHROPIC_API_KEY")
            print("  and run:  python agent.py")
            print("=" * 74)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except* Exception as eg:          # noqa: B030  (3.11+ except* syntax)
        explain_startup_failure(eg)
        sys.exit(1)
