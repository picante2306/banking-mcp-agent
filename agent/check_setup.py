"""Diagnose a broken setup.

demo.py and agent.py launch server.py as a subprocess and talk to it over
stdin/stdout. If the server dies on startup, its error message goes nowhere
and you get an unreadable ExceptionGroup instead. This script runs the same
server directly and shows you what it actually said.

    python check_setup.py
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
problems: list[str] = []


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def bad(msg: str, fix: str) -> None:
    print(f"  [FAIL] {msg}")
    problems.append(fix)


print("\n1. Python")
v = sys.version_info
if v >= (3, 10):
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
else:
    bad(f"Python {v.major}.{v.minor} is too old",
        "Install Python 3.10 or newer from python.org")

print("\n2. Packages")
for name, pip_name in [("mcp", "mcp"), ("anthropic", "anthropic"), ("pandas", "pandas")]:
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "?")
        ok(f"{name} {ver}")
    except ImportError:
        bad(f"{name} is not installed",
            f"Run: pip install {pip_name}")

# The server class was renamed in mcp 2.0. Either name is fine.
try:
    from mcp.server.mcpserver import MCPServer  # noqa: F401
    ok("server class found (mcp 2.x: MCPServer)")
except ModuleNotFoundError:
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        ok("server class found (mcp 1.x: FastMCP)")
    except Exception as e:
        bad(f"no usable server class ({e.__class__.__name__})",
            "Run: pip install --upgrade mcp")

print("\n3. Files")
for f in ["server.py", "demo.py", "agent.py", "build_db.py"]:
    if (HERE / f).exists():
        ok(f)
    else:
        bad(f"{f} is missing",
            "Run this from inside the folder that holds server.py")

db = HERE / "banking.db"
if db.exists():
    ok(f"banking.db ({db.stat().st_size / 1e6:.1f} MB)")
else:
    bad("banking.db has not been built",
        "Run: python build_db.py")

data = HERE / "data"
csvs = sorted(p.name for p in data.glob("*.csv")) if data.exists() else []
if len(csvs) == 5:
    ok(f"data/ has all 5 CSVs")
else:
    bad(f"data/ has {len(csvs)} CSVs, expected 5",
        "Re-extract the zip; the data folder is incomplete")

print("\n4. Starting the server directly (this is the step that usually fails)")
try:
    proc = subprocess.run(
        [sys.executable, "server.py"],
        cwd=HERE,
        input="",                 # empty stdin: server reads nothing and exits
        capture_output=True,
        text=True,
        timeout=25,
    )
    err = (proc.stderr or "").strip()

    if "Traceback" in err:
        bad("server.py crashed on startup. Its error:", "See the traceback below")
        print("\n" + "-" * 68)
        print(err[-2000:])
        print("-" * 68)
    elif proc.returncode not in (0, 1):
        bad(f"server.py exited with code {proc.returncode}",
            "Send this output to Claude")
        if err:
            print(f"\n{err[-800:]}\n")
    else:
        ok("server.py starts and exits cleanly")
except subprocess.TimeoutExpired:
    ok("server.py started and stayed running (expected)")
except Exception as e:
    bad(f"could not start server.py: {e.__class__.__name__}: {e}",
        "Send this output to Claude")

print("\n" + "=" * 68)
if problems:
    print(f"{len(problems)} problem(s) found. Do these in order:\n")
    for i, fix in enumerate(dict.fromkeys(problems), 1):
        print(f"  {i}. {fix}")
else:
    print("Everything checks out. Run:  python demo.py")
print("=" * 68)
