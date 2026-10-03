"""`mucaro mcp` command group: operator-declared MCP stdio servers."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .mcp_client import McpError, call_tool, list_server_names, list_tools
from .operator_audit import CLI_ACTOR, OperatorAuditError, approval_mode, audit_decision, run_audited


def _run(action: Any) -> int:
    try:
        print(json.dumps(action(), indent=2, ensure_ascii=False))
        return 0
    except (McpError, OperatorAuditError) as exc:
        payload = {"status": exc.status, "detail": exc.detail}
        tail = getattr(exc, "stderr_tail", "")
        if tail:
            payload["server_stderr_tail"] = tail  # untrusted server output, shown to the operator only
        print(json.dumps(payload, indent=2), file=sys.stderr)
        return 2


def _confirm(args: argparse.Namespace, summary: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise McpError("ERROR_MCP_CONFIRMATION_REQUIRED", f"{summary}. Re-run with --yes to approve launching the server.")
    print(f"{summary}. Proceed? [y/N] ", end="", file=sys.stderr)
    if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
        raise McpError("ERROR_MCP_APPROVAL_DENIED", "Operator declined.")


def _approve(args: argparse.Namespace, cfg: Settings, tool_name: str, tool_args: dict[str, Any], summary: str) -> str:
    """Ask the operator, audit the decision (including a refusal) in the hash-chained tool log, and return the call id."""
    mode = approval_mode(args.yes)
    try:
        _confirm(args, summary)
    except McpError as exc:
        refused_mode = "denied_non_tty" if exc.status == "ERROR_MCP_CONFIRMATION_REQUIRED" else mode
        audit_decision(cfg, tool_name, tool_args, approved=False, requested_by=CLI_ACTOR, mode=refused_mode)
        raise
    return audit_decision(cfg, tool_name, tool_args, approved=True, requested_by=CLI_ACTOR, mode=mode)


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
            call_id = _approve(args, cfg, "mcp.tools", {"server": args.server}, f"Launch MCP server {args.server}")
            return run_audited(cfg, "mcp.tools", call_id, CLI_ACTOR, lambda: list_tools(cfg, args.server))

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
            tool_args = {"server": args.server, "tool": args.tool, "args": arguments}
            call_id = _approve(args, cfg, "mcp.call", tool_args, f"Launch MCP server {args.server} and call {args.tool}")
            return run_audited(cfg, "mcp.call", call_id, CLI_ACTOR, lambda: call_tool(cfg, args.server, args.tool, arguments))

        return _run(action)

    call.set_defaults(handler=run_call)
