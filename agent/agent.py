"""A banking analytics agent that answers questions in plain English.

The agent connects to the MCP server over stdio, discovers whatever tools it
exposes, and runs a tool-calling loop until it can answer. It knows nothing
about banking at startup — the schema and the business logic arrive through
MCP, so adding a tool to server.py extends the agent with no change here.

    export ANTHROPIC_API_KEY=sk-...
    python agent.py                          # interactive
    python agent.py "which branch earns most?"  # one question
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-4-5"
MAX_TURNS = 8          # tool round-trips before giving up on one question

SYSTEM = """You are a data analyst for a retail bank. You answer questions \
about the bank's operations using the tools provided.

How to work:
- Start from the purpose-built tools (segment_summary, channel_by_age, \
sla_breakdown) when the question matches one. They encode the definitions the \
business has agreed, so they are more reliable than SQL you write yourself.
- Use run_sql for anything else. Call describe_table first if you are unsure \
of a column name rather than guessing.
- The database is read-only. Never attempt a write.

How to answer:
- Lead with the number, then the explanation. No preamble.
- Quote figures exactly as the tools return them. Never estimate or round a \
figure the tool gave you precisely.
- Say what the finding means for the business in one line.
- If the data cannot answer the question, say so plainly rather than \
substituting something adjacent.
"""


class BankingAgent:
    def __init__(self) -> None:
        self.client = Anthropic()
        self.session: ClientSession | None = None
        self.stack = AsyncExitStack()
        self.tools: list[dict] = []

    async def connect(self, server_script: str = "server.py") -> None:
        """Start the MCP server as a subprocess and discover its tools."""
        params = StdioServerParameters(command=sys.executable, args=[server_script])
        read, write = await self.stack.enter_async_context(stdio_client(params))
        self.session = await self.stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()

        listed = await self.session.list_tools()
        # The SDK renamed this attribute in mcp 2.0; accept either spelling.
        self.tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": (getattr(t, "input_schema", None)
                                 or getattr(t, "inputSchema", None)),
            }
            for t in listed.tools
        ]
        names = ", ".join(t["name"] for t in self.tools)
        print(f"Connected to MCP server. {len(self.tools)} tools: {names}\n")

    async def ask(self, question: str, verbose: bool = True) -> str:
        """Run the tool-calling loop until the model produces a final answer."""
        messages: list[dict] = [{"role": "user", "content": question}]

        for _ in range(MAX_TURNS):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=SYSTEM,
                tools=self.tools,
                messages=messages,
            )

            if response.stop_reason != "tool_use":
                return "".join(
                    b.text for b in response.content if b.type == "text"
                ).strip()

            messages.append({"role": "assistant", "content": response.content})

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if verbose:
                    arg = next(iter(block.input.values()), "") if block.input else ""
                    detail = f" {str(arg)[:70]}" if arg else ""
                    print(f"  → {block.name}{detail}")

                outcome = await self.session.call_tool(block.name, block.input)
                text = "\n".join(
                    c.text for c in outcome.content if hasattr(c, "text")
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                    }
                )

            messages.append({"role": "user", "content": results})

        return "Stopped after too many tool calls without reaching an answer."

    async def close(self) -> None:
        await self.stack.aclose()


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first:  export ANTHROPIC_API_KEY=sk-...")

    agent = BankingAgent()
    try:
        await agent.connect()

        if len(sys.argv) > 1:
            question = " ".join(sys.argv[1:])
            print(f"Q: {question}\n")
            print(await agent.ask(question))
            return

        print("Ask a question about the bank's data. Ctrl-C or 'exit' to quit.\n")
        print("Try:  which complaint type is handled worst?")
        print("      what share of value comes from Premium customers?")
        print("      how many transactions failed last quarter?\n")

        while True:
            try:
                question = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if question.lower() in {"exit", "quit", ""}:
                break
            print()
            print(await agent.ask(question))
            print()
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
