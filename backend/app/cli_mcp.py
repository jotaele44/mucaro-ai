"""`mucaro mcp` command group: operator-declared MCP stdio servers."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .mcp_client import McpError, call_tool, list_server_names, list_tools


def _run(action: Any) -> int:
    try:
        print(json.dumps(action(), indent=2, ensure_ascii=False))
        return 0
    except McpError as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2), file=sys.stderr)
        return 2


def _approve(args: argparse.Namespace, summary: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise McpError("ERROR_MCP_CONFIRMATION_REQUIRED", f"{summary}. Re-run with --yes to approve launching the server.")
    print(f"{summary}. Proceed? [y/N] ", end="", file=sys.stderr)
    if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
        raise McpError("ERROR_MCP_APPROVAL_DENIED", "Operator declined.")


def register_mcp_commands(subcommands: Any) -> None:
    mcp = subcommands.add_parser("mcp", help="List and call tools on operator-declared MCP stdio servers.")
    sub = mcp.add_subparsers(dest="mcp_command", required=True)

    servers = sub.add_parser("servers", help="List servers declared in data/mcp_servers.json.")
    servers.set_defaults(handler=lambda args, cfg: _run(lambda: list_server_names(cfg)))

    tools = sub.add_parser("tools", help="Launch a server (approval) and list its tools.")
    tools.add_argument("server")
    tools.add_argument("--yes", action="store_true", help="Approve launching the server.")

    def run_tools(args: argparse.Namespace, cfg: Settings) -> int:
        def action() -> dict[str, Any]:
            _approve(args, f"Launch MCP server {args.server}")
            return list_tools(cfg, args.server)

        return _run(action)

    tools.set_defaults(handler=run_tools)

    call = sub.add_parser("call", help="Launch a server (approval) and call one tool.")
    call.add_argument("server")
    call.add_argument("tool")
    call.add_argument("--args", default="{}", help="Tool arguments as a JSON object.")
    call.add_argument("--yes", action="store_true", help="Approve launching the server and the call.")

    def run_call(args: argparse.Namespace, cfg: Settings) -> int:
        def action() -> dict[str, Any]:
            try:
                arguments = json.loads(args.args)
            except ValueError as exc:
                raise McpError("ERROR_MCP_CALL_INVALID", f"--args is not valid JSON: {exc}") from exc
            _approve(args, f"Launch MCP server {args.server} and call {args.tool}")
            return call_tool(cfg, args.server, args.tool, arguments)

        return _run(action)

    call.set_defaults(handler=run_call)
