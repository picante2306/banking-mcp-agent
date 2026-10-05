"""Verify the MCP protocol layer: handshake, tool discovery, tool invocation.

Exercises the same path agent.py uses, but with assertions instead of a model,
so it needs no API key.

    python test_mcp.py
"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def schema_of(tool):
    """mcp 1.x: inputSchema. mcp 2.x: input_schema."""
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)


EXPECTED = {
    "list_tables",
    "describe_table",
    "run_sql",
    "segment_summary",
    "channel_by_age",
    "sla_breakdown",
}

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["server.py"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print("\nHandshake")
            check("server initialises", info is not None)
            # mcp 1.x spells this serverInfo; 2.x spells it server_info.
            server_info = getattr(info, "server_info", None) or info.serverInfo
            check("server identifies as banking-analytics",
                  server_info.name == "banking-analytics",
                  server_info.name)

            print("\nTool discovery")
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            check(f"all 6 tools advertised ({len(names)} found)", names == EXPECTED,
                  str(names ^ EXPECTED))
            check("every tool has a description",
                  all(t.description for t in listed.tools))
            check("every tool has an input schema",
                  all(schema_of(t) is not None for t in listed.tools))

            by_name = {t.name: t for t in listed.tools}
            check("run_sql declares a 'query' parameter",
                  "query" in schema_of(by_name["run_sql"]).get("properties", {}))
            check("sla_breakdown declares 'group_by'",
                  "group_by" in schema_of(by_name["sla_breakdown"]).get("properties", {}))

            print("\nTool invocation over the protocol")

            r = await session.call_tool("list_tables", {})
            text = "".join(c.text for c in r.content if hasattr(c, "text"))
            check("list_tables returns the tables", "transactions" in text, text[:80])

            r = await session.call_tool("channel_by_age", {})
            text = "".join(c.text for c in r.content if hasattr(c, "text"))
            check("channel_by_age returns 61.6 for 18-34", "61.6" in text, text[:120])

            r = await session.call_tool(
                "run_sql",
                {"query": "SELECT COUNT(*) AS n FROM complaints WHERE sla_breached=1"},
            )
            text = "".join(c.text for c in r.content if hasattr(c, "text"))
            check("run_sql returns 1924 breached complaints", "1924" in text, text[:120])

            r = await session.call_tool("run_sql", {"query": "DROP TABLE customers"})
            text = "".join(c.text for c in r.content if hasattr(c, "text"))
            check("guard blocks a write through the protocol",
                  "rejected" in text.lower(), text[:120])

            r = await session.call_tool("sla_breakdown", {"group_by": "region"})
            text = "".join(c.text for c in r.content if hasattr(c, "text"))
            check("sla_breakdown accepts an argument", "region" in text, text[:80])

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
