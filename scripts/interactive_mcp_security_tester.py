#!/usr/bin/env python3
"""Interactive MCP Security Tester & Local Acceptance Tool.

Provides both automated and interactive testing capabilities for validating
MyAgent's multi-host security boundary, IDOR defense, and workspace isolation.

Usage:
    # Run full automated dual-subprocess security test:
    python scripts/interactive_mcp_security_tester.py --auto

    # Run interactive REPL session against a bounded workspace:
    python scripts/interactive_mcp_security_tester.py --workspace /path/to/repo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def run_repl(workspace_path: Path) -> None:
    repo_root = str(Path(__file__).resolve().parents[1])
    python_bin = sys.executable

    env = {
        **os.environ,
        "MYAGENT_WORKSPACE": str(workspace_path.resolve()),
        "PYTHONPATH": repo_root,
    }

    params = StdioServerParameters(
        command=python_bin,
        args=["-m", "my_agent_mcp.server"],
        env=env,
        cwd=str(workspace_path.resolve()),
    )

    print("=" * 70)
    print("MYAGENT MCP INTERACTIVE TEST REPL")
    print(f"Bound Workspace: {workspace_path.resolve()}")
    print("Commands:")
    print("  status                       -> call my_agent_status")
    print("  call <tool_name> <json_args> -> call arbitrary MCP tool")
    print("  list                         -> list all available MCP tools")
    print("  exit / quit                  -> exit REPL")
    print("=" * 70)

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("Connected to MyAgent MCP Server successfully.\n")

            while True:
                try:
                    user_input = input("mcp> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nExiting.")
                    break

                if not user_input or user_input in ("exit", "quit", "q"):
                    break

                if user_input == "status":
                    res = await session.call_tool("my_agent_status", {})
                    print(res.content[0].text)
                elif user_input == "list":
                    tool_list = await session.list_tools()
                    for t in tool_list.tools:
                        print(f"  - {t.name}: {t.description[:60] if t.description else ''}")
                elif user_input.startswith("call "):
                    parts = user_input[5:].strip().split(" ", 1)
                    tool_name = parts[0]
                    args = {}
                    if len(parts) > 1:
                        try:
                            args = json.loads(parts[1])
                        except Exception as e:
                            print(f"Invalid JSON args: {e}")
                            continue
                    try:
                        res = await session.call_tool(tool_name, args)
                        print(f"[Result is_error={res.is_error}]")
                        for c in res.content:
                            print(c.text)
                    except Exception as e:
                        print(f"Tool call failed: {e}")
                else:
                    print("Unknown command. Type 'status', 'list', 'call <tool> <args_json>', or 'exit'.")


def main() -> None:
    parser = argparse.ArgumentParser(description="MyAgent MCP Security Tester & Local Acceptance Tool")
    parser.add_argument("--auto", action="store_true", help="Run automated 7-scenario dual-process test")
    parser.add_argument("--verify", action="store_true", help="Run compliance & port verification audit")
    parser.add_argument("--inspector", action="store_true", help="Launch MCP Inspector on dedicated ports (18674 & 18675)")
    parser.add_argument("--workspace", type=str, default=str(Path.cwd()), help="Workspace directory for REPL")

    args = parser.parse_args()

    if args.auto:
        from scripts.verify_two_mcp_subprocesses import run_scenario_tests
        success = asyncio.run(run_scenario_tests())
        sys.exit(0 if success else 1)
    elif args.verify:
        from scripts.verify_mcp_inspector_compliance import main as verify_main
        sys.exit(verify_main())
    elif args.inspector:
        import subprocess
        launcher = Path(__file__).resolve().parent / "launch_mcp_inspector.sh"
        res = subprocess.run(["bash", str(launcher)])
        sys.exit(res.returncode)
    else:
        ws = Path(args.workspace)
        if not ws.exists():
            print(f"Workspace path does not exist: {ws}")
            sys.exit(1)
        asyncio.run(run_repl(ws))


if __name__ == "__main__":
    main()
