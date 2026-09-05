from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .config import Settings, settings
from .conversations import (
    append_answer_turn,
    build_conversation_context,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)
from .db import list_payloads
from .execution_gate import analyze_task
from .ingestion import (
    delete_source,
    get_index_status,
    get_sources,
    ingest_file,
    ingest_folder,
    reindex_sources,
)
from .memory import (
    MemoryError,
    delete_memory_item,
    export_memory,
    list_memory_items,
    remember_memory_item,
    set_memory_enabled,
)
from .permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)
from .product_audit import build_product_audit
from .retrieval import MAX_TOP_K, MIN_TOP_K, RETRIEVAL_STRATEGIES
from .security import SecurityProfileError, build_security_report, enforce_startup_security
from .services import answer_question
from .tools import (
    ToolRuntimeError,
    approve_tool_call,
    deny_tool_call,
    list_tool_audit_events,
    list_tool_calls,
    list_tools,
    request_tool_call,
    run_tool_call,
)
from .validation import validate_project_json


class MucaroCLIError(RuntimeError):
    def __init__(self, status: str, detail: str, *, exit_code: int = 2) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.exit_code = exit_code


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PORT_MUST_BE_INTEGER") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("PORT_RANGE:1-65535")
    return port


def package_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        try:
            return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
        except (KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return importlib.metadata.version("mucaro-ai")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.23-dev"


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("JSON_OBJECT_INVALID") from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("JSON_OBJECT_REQUIRED")
    return payload


def parse_top_k(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TOP_K_MUST_BE_INTEGER") from exc
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        raise argparse.ArgumentTypeError(f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}")
    return top_k


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mucaro",
        description="Command line interface for the local-first Mucaro AI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ask = subcommands.add_parser("ask", help="Ask a cited retrieval question.")
    ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    ask.add_argument("--provider", choices=["mock", "anthropic"], help="Model provider override.")
    ask.add_argument("--model", help="Model name override.")
    ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit mock fallback when a live provider is unavailable.")
    ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ask.add_argument("--no-persist", action="store_true", help="Do not write the run to SQLite.")
    ask.set_defaults(handler=run_ask)

    ingest = subcommands.add_parser("ingest", help="Ingest a supported file or folder.")
    ingest.add_argument("path", nargs="?", default="data/input", help="File or folder to ingest.")
    ingest.add_argument(
        "--grant-directory-access",
        action="store_true",
        help="Prompt to grant access when PATH is outside the project directory.",
    )
    ingest.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    ingest.set_defaults(handler=run_ingest)

    doctor = subcommands.add_parser("doctor", help="Check local assistant readiness.")
    doctor.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    doctor.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    doctor.set_defaults(handler=run_doctor)

    audit = subcommands.add_parser("audit", help="Audit product completion against the target objective.")
    audit_subcommands = audit.add_subparsers(dest="audit_command", required=True)

    audit_product = audit_subcommands.add_parser("product", help="Audit ChatGPT/Claude-like product gaps.")
    audit_product.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    audit_product.add_argument("--strict", action="store_true", help="Exit nonzero while product gaps remain open.")
    audit_product.set_defaults(handler=run_audit_product)

    certify = subcommands.add_parser("certify", help="Inspect release certification readiness.")
    certify_subcommands = certify.add_subparsers(dest="certify_command", required=True)

    certify_status = certify_subcommands.add_parser(
        "status",
        help="Show local release readiness before running full certification gates.",
    )
    certify_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_status.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    certify_status.add_argument(
        "--include-github",
        action="store_true",
        help="Include read-only GitHub branch, CI, and remote tag governance checks when gh is available.",
    )
    certify_status.add_argument(
        "--github-workflow",
        default="CI",
        help="GitHub Actions workflow name to use for hosted CI evidence with --include-github.",
    )
    certify_status.set_defaults(handler=run_certify_status)

    sources = subcommands.add_parser("sources", help="Inspect and manage ingested sources.")
    sources_subcommands = sources.add_subparsers(dest="sources_command", required=True)

    sources_list = sources_subcommands.add_parser("list", help="List ingested sources and index status.")
    sources_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_list.set_defaults(handler=run_sources_list)

    sources_reindex = sources_subcommands.add_parser("reindex", help="Rebuild the local persistent retrieval index.")
    sources_reindex.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_reindex.set_defaults(handler=run_sources_reindex)

    sources_delete = sources_subcommands.add_parser("delete", help="Delete one ingested source by source_id.")
    sources_delete.add_argument("source_id", help="Source identifier to remove from the local source library.")
    sources_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_delete.set_defaults(handler=run_sources_delete)

    conversations = subcommands.add_parser("conversations", help="Manage persistent local chat sessions.")
    conversations_subcommands = conversations.add_subparsers(dest="conversations_command", required=True)

    conversations_list = conversations_subcommands.add_parser("list", help="List saved conversations.")
    conversations_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_list.set_defaults(handler=run_conversations_list)

    conversations_create = conversations_subcommands.add_parser("create", help="Create a saved conversation.")
    conversations_create.add_argument("--title", default=None, help="Optional conversation title.")
    conversations_create.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_create.set_defaults(handler=run_conversations_create)

    conversations_show = conversations_subcommands.add_parser("show", help="Show one saved conversation.")
    conversations_show.add_argument("conversation_id", help="Conversation identifier.")
    conversations_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_show.set_defaults(handler=run_conversations_show)

    conversations_rename = conversations_subcommands.add_parser("rename", help="Rename one saved conversation.")
    conversations_rename.add_argument("conversation_id", help="Conversation identifier.")
    conversations_rename.add_argument("title", nargs="+", help="New conversation title.")
    conversations_rename.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_rename.set_defaults(handler=run_conversations_rename)

    conversations_delete = conversations_subcommands.add_parser("delete", help="Delete one saved conversation.")
    conversations_delete.add_argument("conversation_id", help="Conversation identifier.")
    conversations_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_delete.set_defaults(handler=run_conversations_delete)

    conversations_ask = conversations_subcommands.add_parser("ask", help="Ask inside a saved conversation.")
    conversations_ask.add_argument("conversation_id", help="Conversation identifier.")
    conversations_ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    conversations_ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    conversations_ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    conversations_ask.add_argument("--provider", choices=["mock", "anthropic"], help="Model provider override.")
    conversations_ask.add_argument("--model", help="Model name override.")
    conversations_ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit mock fallback when a live provider is unavailable.")
    conversations_ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_ask.set_defaults(handler=run_conversations_ask)

    runs = subcommands.add_parser("runs", help="Inspect persisted answer runs.")
    runs_subcommands = runs.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_subcommands.add_parser("list", help="List persisted answer runs.")
    runs_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_list.set_defaults(handler=run_runs_list)

    runs_summary = runs_subcommands.add_parser("summary", help="Summarize persisted answer runs.")
    runs_summary.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_summary.set_defaults(handler=run_runs_summary)

    runs_show = runs_subcommands.add_parser("show", help="Show one persisted answer run.")
    runs_show.add_argument("run_id", help="Run identifier.")
    runs_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_show.set_defaults(handler=run_runs_show)

    tools = subcommands.add_parser("tools", help="Request and audit approved local tool calls.")
    tools_subcommands = tools.add_subparsers(dest="tools_command", required=True)

    tools_list = tools_subcommands.add_parser("list", help="List locally allowed tools.")
    tools_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_list.set_defaults(handler=run_tools_list)

    tools_requests = tools_subcommands.add_parser("requests", help="List tool-call requests.")
    tools_requests.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_requests.set_defaults(handler=run_tools_requests)

    tools_request = tools_subcommands.add_parser("request", help="Create a pending tool-call request.")
    tools_request.add_argument("tool_name", help="Allowed tool name.")
    tools_request.add_argument("--arguments", type=parse_json_object, default={}, help="JSON object passed to the tool.")
    tools_request.add_argument("--reason", help="Optional reason recorded in the tool audit log.")
    tools_request.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_request.set_defaults(handler=run_tools_request)

    tools_approve = tools_subcommands.add_parser("approve", help="Approve one pending tool-call request.")
    tools_approve.add_argument("tool_call_id", help="Tool call identifier.")
    tools_approve.add_argument("--reason", help="Optional approval reason.")
    tools_approve.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_approve.set_defaults(handler=run_tools_approve)

    tools_deny = tools_subcommands.add_parser("deny", help="Deny one pending tool-call request.")
    tools_deny.add_argument("tool_call_id", help="Tool call identifier.")
    tools_deny.add_argument("--reason", help="Optional denial reason.")
    tools_deny.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_deny.set_defaults(handler=run_tools_deny)

    tools_run = tools_subcommands.add_parser("run", help="Execute one approved tool-call request.")
    tools_run.add_argument("tool_call_id", help="Tool call identifier.")
    tools_run.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_run.set_defaults(handler=run_tools_run)

    tools_audit = tools_subcommands.add_parser("audit", help="List tool runtime audit events.")
    tools_audit.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_audit.set_defaults(handler=run_tools_audit)

    memory = subcommands.add_parser("memory", help="Manage explicit local assistant memory.")
    memory_subcommands = memory.add_subparsers(dest="memory_command", required=True)

    memory_list = memory_subcommands.add_parser("list", help="List saved memory items.")
    memory_list.add_argument("--active-only", action="store_true", help="Only show enabled memory items.")
    memory_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_list.set_defaults(handler=run_memory_list)

    memory_remember = memory_subcommands.add_parser("remember", help="Save an explicit memory item.")
    memory_remember.add_argument("content", nargs="+", help="Memory content to save.")
    memory_remember.add_argument(
        "--kind",
        choices=["preference", "profile", "instruction", "fact"],
        default="preference",
        help="Memory item type.",
    )
    memory_remember.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_remember.set_defaults(handler=run_memory_remember)

    memory_enable = memory_subcommands.add_parser("enable", help="Enable one memory item.")
    memory_enable.add_argument("memory_id", help="Memory identifier.")
    memory_enable.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_enable.set_defaults(handler=run_memory_enable)

    memory_disable = memory_subcommands.add_parser("disable", help="Disable one memory item without deleting it.")
    memory_disable.add_argument("memory_id", help="Memory identifier.")
    memory_disable.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_disable.set_defaults(handler=run_memory_disable)

    memory_delete = memory_subcommands.add_parser("delete", help="Delete one memory item.")
    memory_delete.add_argument("memory_id", help="Memory identifier.")
    memory_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_delete.set_defaults(handler=run_memory_delete)

    memory_export = memory_subcommands.add_parser("export", help="Export all saved memory items.")
    memory_export.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_export.set_defaults(handler=run_memory_export)

    security = subcommands.add_parser("security", help="Inspect production security readiness.")
    security_subcommands = security.add_subparsers(dest="security_command", required=True)

    security_status = security_subcommands.add_parser("status", help="Show local security profile status.")
    security_status.add_argument("--host", default=None, help="Optional bind host to evaluate.")
    security_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    security_status.set_defaults(handler=run_security_status)

    index = subcommands.add_parser("index", help="Inspect retrieval index health.")
    index_subcommands = index.add_subparsers(dest="index_command", required=True)

    index_status = index_subcommands.add_parser("status", help="Show local persistent index status.")
    index_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    index_status.set_defaults(handler=run_index_status)

    permissions = subcommands.add_parser("permissions", help="Manage user-directory access grants.")
    permissions_subcommands = permissions.add_subparsers(dest="permission_command", required=True)

    permissions_grant = permissions_subcommands.add_parser("grant", help="Grant access to a file's containing folder or a folder.")
    permissions_grant.add_argument("path", help="File or directory path to authorize.")
    permissions_grant.add_argument("--yes", action="store_true", help="Confirm the grant without an interactive prompt.")
    permissions_grant.add_argument("--reason", help="Optional reason recorded in the local permission ledger.")
    permissions_grant.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_grant.set_defaults(handler=run_permissions_grant)

    permissions_list = permissions_subcommands.add_parser("list", help="List directory access grants.")
    permissions_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_list.set_defaults(handler=run_permissions_list)

    permissions_check = permissions_subcommands.add_parser("check", help="Check whether a path is currently accessible.")
    permissions_check.add_argument("path", help="File or directory path to check.")
    permissions_check.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_check.set_defaults(handler=run_permissions_check)

    permissions_revoke = permissions_subcommands.add_parser("revoke", help="Revoke an exact directory access grant.")
    permissions_revoke.add_argument("path", help="File or directory path whose containing grant should be revoked.")
    permissions_revoke.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_revoke.set_defaults(handler=run_permissions_revoke)

    plan = subcommands.add_parser("plan", help="Analyze a request with the Planning Gate.")
    plan.add_argument("request", nargs="+", help="Request to analyze.")
    plan.add_argument("--input", action="append", default=[], help="Available input, repeatable.")
    plan.add_argument("--approval-state", default="NOT_REQUESTED", help="Current approval state.")
    plan.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    plan.set_defaults(handler=run_plan)

    validate = subcommands.add_parser("validate", help="Validate project JSON artifacts.")
    validate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    validate.set_defaults(handler=run_validate)

    serve = subcommands.add_parser("serve", help="Start the local API server.")
    serve.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    serve.add_argument("--port", type=parse_port, default=8000, help="Port to bind.")
    serve.add_argument("--reload", action="store_true", help="Restart when source files change.")
    serve.set_defaults(handler=run_serve)

    return parser


def emit_payload(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    status = payload.get("status") or payload.get("citation_status") or "OK"
    print(status)
    if status in {"CERTIFICATION_READY", "CERTIFICATION_WARN", "CERTIFICATION_FAIL"}:
        print(f"version={payload.get('version')}")
        print(f"project_root={payload.get('project_root')}")
        git = payload.get("git", {})
        if isinstance(git, dict):
            print(f"branch={git.get('branch')}")
            print(f"head_sha={git.get('head_sha')}")
            print(f"tags_at_head={','.join(git.get('tags_at_head', []))}")
            print(f"worktree_clean={git.get('worktree_clean')}")
            print(f"upstream={git.get('upstream')}")
        github = payload.get("github", {})
        if isinstance(github, dict) and github.get("enabled"):
            print(f"github_repo={github.get('repo')}")
            print(f"github_branch_protected={github.get('branch_protected')}")
            print(f"github_workflow={github.get('workflow')}")
            latest_ci = github.get("latest_ci")
            if isinstance(latest_ci, dict):
                print(f"github_latest_ci={latest_ci.get('status')}:{latest_ci.get('conclusion')}:{latest_ci.get('head_sha')}")
        for gate in payload.get("gates", []):
            detail = str(gate.get("detail")).replace("\n", "\\n")
            print(f"{gate.get('status')} {gate.get('name')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"DOCTOR_OK", "DOCTOR_WARN", "DOCTOR_FAIL"}:
        summary = payload.get("summary", {})
        if isinstance(summary, dict):
            print(f"version={payload.get('version')}")
            print(f"sources={summary.get('source_count', 0)}")
            print(f"index_status={summary.get('index_status')}")
            print(f"semantic_store_status={summary.get('semantic_store_status')}")
            print(f"grants={summary.get('directory_grant_count', 0)}")
            print(f"conversations={summary.get('conversation_count', 0)}")
            print(f"runs={summary.get('run_count', 0)}")
            print(f"memories={summary.get('memory_count', 0)}")
            print(f"enabled_memories={summary.get('enabled_memory_count', 0)}")
        for check in payload.get("checks", []):
            print(f"{check.get('status')} {check.get('name')} {check.get('detail')}")
        for recommendation in payload.get("recommendations", []):
            print(f"recommendation={recommendation}")
    elif status in {"PRODUCT_AUDIT_OK", "PRODUCT_AUDIT_OPEN", "PRODUCT_AUDIT_BLOCKED"}:
        counts = payload.get("counts", {})
        print(f"readiness_percent={payload.get('readiness_percent')}")
        if isinstance(counts, dict):
            print(f"pass={counts.get('pass', 0)}")
            print(f"open={counts.get('open', 0)}")
            print(f"blocked={counts.get('blocked', 0)}")
            print(f"total={counts.get('total', 0)}")
        print(f"next_recommended_vector={payload.get('next_recommended_vector')}")
        for requirement in payload.get("requirements", []):
            print(f"{requirement.get('status')} {requirement.get('requirement_id')} {requirement.get('gap')}")
    elif "answer" in payload and "conversation" in payload:
        answer = payload.get("answer", {})
        conversation = payload.get("conversation", {})
        print(f"conversation_id={conversation.get('conversation_id')}")
        if isinstance(answer, dict):
            print(f"answer_status={answer.get('citation_status') or answer.get('status')}")
            if answer.get("run_id"):
                print(f"run_id={answer.get('run_id')}")
            if answer.get("answer"):
                print(answer.get("answer"))
    elif "run_id" in payload:
        print(f"run_id={payload.get('run_id')}")
        print(f"citation_status={payload.get('citation_status')}")
        print(f"retrieval_strategy={payload.get('retrieval_strategy')}")
        if payload.get("answer"):
            print(payload.get("answer"))
    elif "answer" in payload:
        print(payload["answer"])
    elif "files_ingested" in payload:
        print(f"files_ingested={payload['files_ingested']}")
    elif "source" in payload:
        source = payload["source"]
        print(f"source_id={source.get('source_id')} chunks_added={payload.get('chunks_added', 0)}")
    elif "conversations" in payload:
        print(f"conversations={len(payload.get('conversations', []))}")
        for conversation in payload.get("conversations", []):
            print(
                f"{conversation.get('conversation_id')} {conversation.get('title')} "
                f"turns={conversation.get('turn_count', 0)} status={conversation.get('last_status')}"
            )
    elif "conversation_id" in payload and "turns" in payload:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"title={payload.get('title')}")
        print(f"turns={len(payload.get('turns', []))}")
        for turn in payload.get("turns", []):
            print(f"{turn.get('role')} {turn.get('status')} {turn.get('run_id') or ''}".rstrip())
    elif "runs" in payload:
        print(f"runs={len(payload.get('runs', []))}")
        for run in payload.get("runs", []):
            print(f"{run.get('run_id')} {run.get('citation_status') or run.get('status')} {run.get('retrieval_strategy')}")
    elif "tools" in payload:
        print(f"tools={len(payload.get('tools', []))}")
        for tool in payload.get("tools", []):
            print(
                f"{tool.get('name')} approval={tool.get('requires_approval')} mutates={tool.get('mutates_state')}"
            )
    elif "tool_calls" in payload:
        print(f"tool_calls={len(payload.get('tool_calls', []))}")
        for call in payload.get("tool_calls", []):
            print(f"{call.get('tool_call_id')} {call.get('status')} {call.get('tool_name')}")
    elif "tool_call" in payload:
        call = payload.get("tool_call", {})
        if isinstance(call, dict):
            print(f"tool_call_id={call.get('tool_call_id')}")
            print(f"tool_name={call.get('tool_name')}")
            print(f"tool_status={call.get('status')}")
        result = payload.get("result")
        if isinstance(result, dict):
            print(f"result_status={result.get('status')}")
    elif "events" in payload:
        print(f"events={len(payload.get('events', []))}")
        for event in payload.get("events", []):
            print(f"{event.get('event_id')} {event.get('event')} {event.get('status')}")
    elif "memories" in payload:
        print(f"memories={len(payload.get('memories', []))}")
        print(f"enabled={payload.get('enabled_count', 0)}")
        for item in payload.get("memories", []):
            state = "enabled" if item.get("enabled") else "disabled"
            print(f"{item.get('memory_id')} {state} {item.get('kind')} {item.get('content')}")
    elif "memory" in payload:
        item = payload.get("memory", {})
        if isinstance(item, dict):
            state = "enabled" if item.get("enabled") else "disabled"
            print(f"memory_id={item.get('memory_id')}")
            print(f"memory_status={state}")
            print(f"kind={item.get('kind')}")
    elif payload.get("status") in {"MEMORY_DELETE_OK", "ERROR_MEMORY_NOT_FOUND"}:
        print(f"memory_id={payload.get('memory_id')}")
        print(f"removed={payload.get('removed', 0)}")
    elif payload.get("status") in {"SECURITY_OK", "SECURITY_WARN", "SECURITY_FAIL"}:
        print(f"deployment_profile={payload.get('deployment_profile')}")
        print(f"production_ready={payload.get('production_ready')}")
        for check in payload.get("checks", []):
            print(f"{check.get('status')} {check.get('name')} {check.get('detail')}")
        for recommendation in payload.get("recommendations", []):
            print(f"recommendation={recommendation}")
    elif payload.get("status") == "RUN_HISTORY_OK":
        print(f"run_count={payload.get('run_count', 0)}")
        print(f"citation_ok={payload.get('citation_ok', 0)}")
        print(f"latest_run_id={payload.get('latest_run_id')}")
    elif "sources" in payload:
        print(f"sources={len(payload.get('sources', []))}")
        index = payload.get("index", {})
        if isinstance(index, dict):
            print(f"index_status={index.get('status')}")
            print(f"indexed_chunk_count={index.get('indexed_chunk_count', 0)}")
            semantic_store = index.get("semantic_store", {})
            if isinstance(semantic_store, dict):
                print(f"semantic_store_status={semantic_store.get('status')}")
                print(f"semantic_store_indexed_chunk_count={semantic_store.get('indexed_chunk_count', 0)}")
        for source in payload.get("sources", []):
            print(f"{source.get('source_id')} {source.get('file_name')} chunks={source.get('chunk_count', 0)}")
    elif "removed_chunks" in payload:
        print(f"source_id={payload.get('source_id')}")
        print(f"removed_chunks={payload.get('removed_chunks', 0)}")
        print(f"remaining_sources={payload.get('remaining_sources', 0)}")
    elif "index_status" in payload:
        print(f"index_status={payload.get('index_status')}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
        if payload.get("semantic_store_status"):
            print(f"semantic_store_status={payload.get('semantic_store_status')}")
            print(f"semantic_store_indexed_chunk_count={payload.get('semantic_store_indexed_chunk_count', 0)}")
    elif payload.get("status") in {"INDEX_OK", "INDEX_STALE", "INDEX_MISSING"}:
        print(f"chunk_count={payload.get('chunk_count', 0)}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
        print(f"stale={payload.get('stale')}")
        semantic_store = payload.get("semantic_store", {})
        if isinstance(semantic_store, dict):
            print(f"semantic_store_status={semantic_store.get('status')}")
            print(f"semantic_store_stale={semantic_store.get('stale')}")
    elif "readiness_percent" in payload:
        print(f"active_vector={payload.get('active_vector')}")
        print(f"recommended_mode={payload.get('recommended_mode')}")
        print(f"readiness_percent={payload.get('readiness_percent')}")
    elif "checked" in payload:
        print(f"checked={len(payload.get('checked', []))}")
    elif "grants" in payload:
        print(f"grants={len(payload.get('grants', []))}")
        for grant in payload.get("grants", []):
            print(grant.get("canonical_root"))
    elif "canonical_root" in payload:
        print(f"canonical_root={payload.get('canonical_root')}")
    elif payload.get("status") in {"CONVERSATION_DELETE_OK", "ERROR_CONVERSATION_NOT_FOUND"}:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"removed={payload.get('removed')}")
    elif "path" in payload:
        print(f"path={payload.get('path')}")


def emit_error(status: str, detail: str, output_format: str = "json") -> None:
    payload = {"status": status, "detail": detail}
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return
    print(f"{status} {detail}", file=sys.stderr)


def run_ask(args: argparse.Namespace, cfg: Settings) -> int:
    result = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        cfg=cfg,
        persist=not args.no_persist,
    )
    emit_payload(result, args.format)
    if result.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if result.get("citation_status") == "CITATION_OK" else 3


def run_ingest(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.path)
    if not path.is_absolute():
        path = cfg.project_root / path
    if not path.exists():
        raise MucaroCLIError("ERROR_INPUT_PATH_NOT_FOUND", str(path))
    if not path.is_file() and not path.is_dir():
        raise MucaroCLIError("ERROR_INPUT_PATH_UNSUPPORTED", str(path))
    permission = check_directory_permission(path, cfg)
    if permission["status"] != "PERMISSION_OK":
        if not args.grant_directory_access:
            raise MucaroCLIError(str(permission["status"]), str(permission["detail"]))
        grant = prompt_for_directory_grant(Path(str(permission["canonical_root"])), cfg)
        permission = {"status": "PERMISSION_OK", "scope": "granted_directory", "grant": grant}
    result = ingest_folder(path, cfg) if path.is_dir() else ingest_file(path, cfg)
    result["directory_permission"] = permission
    emit_payload(result, args.format)
    return 0


def _doctor_check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _safe_payload_count(table: str, cfg: Settings) -> int:
    if not cfg.sqlite_path.exists():
        return 0
    return len(list_payloads(table, cfg.sqlite_path))


def build_doctor_report(cfg: Settings) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    recommendations: list[str] = []

    checks.append(
        _doctor_check(
            "project_root",
            "PASS" if cfg.project_root.exists() else "FAIL",
            str(cfg.project_root),
        )
    )
    missing_dirs = [
        str(path)
        for path in [cfg.data_dir, cfg.input_dir, cfg.processed_dir, cfg.export_dir, cfg.index_dir]
        if not path.exists()
    ]
    checks.append(
        _doctor_check(
            "data_directories",
            "PASS" if not missing_dirs else "WARN",
            "all present" if not missing_dirs else f"missing={len(missing_dirs)}",
        )
    )
    if missing_dirs:
        recommendations.append("Run `mucaro ingest data/input` or start the app once to initialize local data directories.")

    sources = get_sources(cfg)
    chunks = []
    try:
        from .ingestion import get_chunks

        chunks = get_chunks(cfg)
    except Exception:
        chunks = []
    checks.append(
        _doctor_check(
            "source_library",
            "PASS" if sources and chunks else "WARN",
            f"sources={len(sources)} chunks={len(chunks)}",
        )
    )
    if not sources or not chunks:
        recommendations.append("Ingest approved local files before asking source-grounded questions.")

    index = get_index_status(cfg)
    semantic_store = index.get("semantic_store", {})
    checks.append(
        _doctor_check(
            "persistent_index",
            "PASS" if index.get("status") == "INDEX_OK" else "WARN",
            str(index.get("status")),
        )
    )
    if index.get("status") != "INDEX_OK":
        recommendations.append("Run `mucaro sources reindex` after ingesting sources to repair the persistent index.")
    checks.append(
        _doctor_check(
            "semantic_store",
            "PASS" if isinstance(semantic_store, dict) and semantic_store.get("status") == "SEMANTIC_STORE_OK" else "WARN",
            str(semantic_store.get("status") if isinstance(semantic_store, dict) else "SEMANTIC_STORE_UNKNOWN"),
        )
    )
    if not isinstance(semantic_store, dict) or semantic_store.get("status") != "SEMANTIC_STORE_OK":
        recommendations.append("Run `mucaro sources reindex` after ingesting sources to repair the semantic store.")

    try:
        grants = load_directory_grants(cfg)
        checks.append(_doctor_check("directory_permissions", "PASS", f"grants={len(grants)}"))
    except DirectoryPermissionError as exc:
        grants = []
        checks.append(_doctor_check("directory_permissions", "FAIL", exc.status))
        recommendations.append("Repair or remove the directory permission ledger under data/processed.")

    live_mode = os.getenv("MUCARO_AI_LIVE_MODE") == "1"
    api_key_present = bool(os.getenv("ANTHROPIC_API_KEY"))
    anthropic_sdk_present = importlib.util.find_spec("anthropic") is not None
    if cfg.default_model_provider == "mock":
        model_status = "PASS"
        model_detail = "mock provider"
    elif cfg.default_model_provider == "anthropic":
        model_ready = live_mode and api_key_present and anthropic_sdk_present
        model_status = "PASS" if model_ready else "WARN"
        model_detail = (
            f"live_mode={live_mode} api_key_present={api_key_present} sdk_present={anthropic_sdk_present}"
        )
        if not model_ready:
            recommendations.append(
                "For live Anthropic mode, install `.[anthropic]`, set MUCARO_AI_LIVE_MODE=1, and set ANTHROPIC_API_KEY."
            )
    else:
        model_status = "FAIL"
        model_detail = f"unsupported provider={cfg.default_model_provider}"
        recommendations.append("Set MUCARO_AI_MODEL_PROVIDER to `mock` or `anthropic`.")
    checks.append(_doctor_check("model_provider", model_status, model_detail))

    checks.append(
        _doctor_check(
            "operator_auth",
            "PASS" if cfg.operator_token else "WARN",
            "enabled" if cfg.operator_token else "disabled",
        )
    )
    if not cfg.operator_token:
        recommendations.append("Set MUCARO_AI_OPERATOR_TOKEN when exposing the local API beyond a private machine.")
    security = build_security_report(cfg)
    security_check_status = "FAIL" if security["status"] == "SECURITY_FAIL" else "WARN" if security["status"] == "SECURITY_WARN" else "PASS"
    checks.append(_doctor_check("security_profile", security_check_status, security["status"]))
    recommendations.extend(security.get("recommendations", []))

    conversation_count = _safe_payload_count("conversations", cfg)
    run_count = _safe_payload_count("runs", cfg)
    memory_payload = list_memory_items(cfg)
    checks.append(_doctor_check("assistant_history", "PASS", f"conversations={conversation_count} runs={run_count}"))
    checks.append(
        _doctor_check(
            "assistant_memory",
            "PASS",
            f"memories={memory_payload.get('memory_count', 0)} enabled={memory_payload.get('enabled_count', 0)}",
        )
    )
    tools = list_tools().get("tools", [])
    checks.append(_doctor_check("tool_runtime", "PASS" if tools else "WARN", f"allowed_tools={len(tools)}"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "DOCTOR_FAIL" if failed else "DOCTOR_WARN" if warned else "DOCTOR_OK"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "summary": {
            "source_count": len(sources),
            "chunk_count": len(chunks),
            "index_status": index.get("status"),
            "semantic_store_status": semantic_store.get("status") if isinstance(semantic_store, dict) else "SEMANTIC_STORE_UNKNOWN",
            "directory_grant_count": len(grants),
            "conversation_count": conversation_count,
            "run_count": run_count,
            "memory_count": memory_payload.get("memory_count", 0),
            "enabled_memory_count": memory_payload.get("enabled_count", 0),
            "tool_count": len(tools),
            "model_provider": cfg.default_model_provider,
            "live_mode": live_mode,
            "auth_required": bool(cfg.operator_token),
            "deployment_profile": cfg.deployment_profile,
            "security_status": security.get("status"),
        },
        "checks": checks,
        "recommendations": sorted(set(recommendations)),
    }


def run_doctor(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_doctor_report(cfg)
    emit_payload(report, args.format)
    if args.strict and report["status"] != "DOCTOR_OK":
        return 2
    return 0


def run_audit_product(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_product_audit(cfg)
    emit_payload(report, args.format)
    if args.strict and report["status"] != "PRODUCT_AUDIT_OK":
        return 2
    return 0


def _git_output(cfg: Settings, args: list[str]) -> tuple[bool, str]:
    return _command_output(cfg, ["git", *args])


def _command_output(cfg: Settings, args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=cfg.project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    return proc.returncode == 0, proc.stdout.strip()


def _redact_remote_url(remote_url: str) -> str:
    if "://" not in remote_url:
        return remote_url
    scheme, rest = remote_url.split("://", 1)
    if "@" not in rest:
        return remote_url
    return f"{scheme}://<redacted>@{rest.split('@', 1)[1]}"


def _certification_gate(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _github_repo_from_origin(remote_url: str | None) -> str | None:
    if not remote_url:
        return None
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _gh_json(cfg: Settings, args: list[str]) -> tuple[bool, Any, str]:
    ok, output = _command_output(cfg, ["gh", *args])
    if not ok:
        return False, None, output
    try:
        return True, json.loads(output), output
    except json.JSONDecodeError as exc:
        return False, None, f"invalid gh JSON: {exc}"


def _find_remote_tag_commit(cfg: Settings, tag_name: str, head_sha: str | None) -> tuple[bool, str]:
    ok, output = _git_output(cfg, ["ls-remote", "origin", f"refs/tags/{tag_name}", f"refs/tags/{tag_name}^{{}}"])
    if not ok:
        return False, output or "remote tag lookup failed"
    dereferenced = None
    direct = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            dereferenced = sha
        else:
            direct = sha
    resolved = dereferenced or direct
    if not resolved:
        return False, "remote tag not found"
    if head_sha and resolved != head_sha:
        return False, f"{tag_name} -> {resolved}, expected {head_sha}"
    return True, f"{tag_name} -> {resolved}"


def build_github_governance_status(
    cfg: Settings,
    *,
    repo: str | None,
    branch: str | None,
    head_sha: str | None,
    tags_at_head: list[str],
    workflow_name: str = "CI",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    github: dict[str, Any] = {
        "enabled": True,
        "repo": repo,
        "branch": branch,
        "workflow": workflow_name,
        "branch_protected": None,
        "required_status_checks": None,
        "latest_ci": None,
        "remote_tag_bindings": [],
        "errors": [],
    }
    gates: list[dict[str, str]] = []

    if not repo:
        gates.append(_certification_gate("github_repo", "WARN", "origin is not a github.com repository"))
        return github, gates
    gates.append(_certification_gate("github_repo", "PASS", repo))

    if not branch:
        gates.append(_certification_gate("github_branch", "WARN", "branch unavailable"))
        return github, gates

    branch_ok, branch_payload, branch_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}"])
    if not branch_ok or not isinstance(branch_payload, dict):
        github["errors"].append(branch_error)
        gates.append(_certification_gate("github_branch_protection", "WARN", branch_error or "branch lookup failed"))
    else:
        protected = bool(branch_payload.get("protected"))
        github["branch_protected"] = protected
        protection = branch_payload.get("protection", {})
        required_checks = False
        if isinstance(protection, dict):
            required = protection.get("required_status_checks", {})
            if isinstance(required, dict):
                contexts = required.get("contexts") or []
                checks = required.get("checks") or []
                enforcement = required.get("enforcement_level")
                required_checks = bool(contexts or checks or enforcement == "non_admins")
        github["required_status_checks"] = required_checks
        detail = "protected" if protected else "unprotected"
        if protected and required_checks:
            detail = "protected with required status checks"
        gates.append(_certification_gate("github_branch_protection", "PASS" if protected else "WARN", detail))

    ci_ok, ci_payload, ci_error = _gh_json(
        cfg,
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow_name,
            "--branch",
            branch,
            "--limit",
            "1",
            "--json",
            "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
        ],
    )
    if not ci_ok or not isinstance(ci_payload, list) or not ci_payload:
        github["errors"].append(ci_error)
        gates.append(_certification_gate("github_latest_ci", "WARN", ci_error or "latest CI lookup failed"))
    else:
        latest = ci_payload[0]
        if isinstance(latest, dict):
            latest_ci = {
                "database_id": latest.get("databaseId"),
                "status": latest.get("status"),
                "conclusion": latest.get("conclusion"),
                "head_sha": latest.get("headSha"),
                "url": latest.get("url"),
            }
            github["latest_ci"] = latest_ci
            ci_matches_head = not head_sha or latest.get("headSha") == head_sha
            ci_passed = latest.get("status") == "completed" and latest.get("conclusion") == "success"
            if ci_matches_head and ci_passed:
                gates.append(_certification_gate("github_latest_ci", "PASS", f"success {latest.get('headSha')}"))
            elif ci_matches_head:
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "WARN",
                        f"{latest.get('status')} {latest.get('conclusion') or 'pending'} {latest.get('headSha')}",
                    )
                )
            else:
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "FAIL",
                        f"latest {latest.get('headSha')} does not match HEAD {head_sha}",
                    )
                )

    if not tags_at_head:
        gates.append(_certification_gate("github_tag_binding", "WARN", "no local tag at HEAD"))
    else:
        for tag_name in tags_at_head:
            tag_ok, tag_detail = _find_remote_tag_commit(cfg, tag_name, head_sha)
            github["remote_tag_bindings"].append({"tag": tag_name, "status": "PASS" if tag_ok else "FAIL", "detail": tag_detail})
            gates.append(_certification_gate("github_tag_binding", "PASS" if tag_ok else "FAIL", tag_detail))

    return github, gates


def build_certification_status(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
) -> dict[str, Any]:
    audit = build_product_audit(cfg)
    doctor = build_doctor_report(cfg)
    validation = validate_project_json(cfg)

    branch_ok, branch = _git_output(cfg, ["branch", "--show-current"])
    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    status_ok, worktree_status = _git_output(cfg, ["status", "--short"])
    tags_ok, tags_output = _git_output(cfg, ["tag", "--points-at", "HEAD"])
    upstream_ok, upstream = _git_output(cfg, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    remote_ok, remote_url = _git_output(cfg, ["remote", "get-url", "origin"])

    tags_at_head = [line for line in tags_output.splitlines() if line.strip()] if tags_ok else []
    worktree_clean = status_ok and not worktree_status
    repo = _github_repo_from_origin(remote_url if remote_ok else None)

    gates = [
        _certification_gate(
            "product_audit",
            "PASS" if audit.get("status") == "PRODUCT_AUDIT_OK" else "FAIL",
            str(audit.get("status")),
        ),
        _certification_gate(
            "doctor",
            "PASS" if doctor.get("status") == "DOCTOR_OK" else "WARN" if doctor.get("status") == "DOCTOR_WARN" else "FAIL",
            str(doctor.get("status")),
        ),
        _certification_gate(
            "json_validation",
            "PASS" if validation.get("status") == "JSON_OK" else "FAIL",
            str(validation.get("status")),
        ),
        _certification_gate(
            "git_repository",
            "PASS" if head_ok else "WARN",
            head_sha if head_ok else "git metadata unavailable",
        ),
        _certification_gate(
            "worktree_clean",
            "PASS" if worktree_clean else "FAIL" if status_ok else "WARN",
            "clean" if worktree_clean else worktree_status or "git status unavailable",
        ),
        _certification_gate(
            "baseline_tag",
            "PASS" if tags_at_head else "WARN",
            ",".join(tags_at_head) if tags_at_head else "no tag points at HEAD",
        ),
        _certification_gate(
            "upstream",
            "PASS" if upstream_ok else "WARN",
            upstream if upstream_ok else "no upstream configured",
        ),
    ]
    github: dict[str, Any] = {"enabled": False}
    if include_github:
        github, github_gates = build_github_governance_status(
            cfg,
            repo=repo,
            branch=branch if branch_ok else None,
            head_sha=head_sha if head_ok else None,
            tags_at_head=tags_at_head,
            workflow_name=github_workflow,
        )
        gates.extend(github_gates)

    failed = [gate for gate in gates if gate["status"] == "FAIL"]
    warned = [gate for gate in gates if gate["status"] == "WARN"]
    status = "CERTIFICATION_FAIL" if failed else "CERTIFICATION_WARN" if warned else "CERTIFICATION_READY"

    git_payload = {
        "branch": branch if branch_ok else None,
        "head_sha": head_sha if head_ok else None,
        "tags_at_head": tags_at_head,
        "worktree_clean": worktree_clean if status_ok else None,
        "upstream": upstream if upstream_ok else None,
        "origin": _redact_remote_url(remote_url) if remote_ok else None,
    }
    ref = tags_at_head[0] if tags_at_head else head_sha if head_ok else "main"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "git": git_payload,
        "gates": gates,
        "doctor_status": doctor.get("status"),
        "product_audit_status": audit.get("status"),
        "json_status": validation.get("status"),
        "github": github,
        "next_commands": [
            "python3 scripts/certify_release.py",
            f"python3 scripts/certify_fresh_clone.py --ref {ref}",
        ],
    }


def run_certify_status(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_certification_status(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "CERTIFICATION_FAIL":
        return 2
    if args.strict and report["status"] != "CERTIFICATION_READY":
        return 2
    return 0


def run_sources_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "SOURCES_OK", "sources": get_sources(cfg), "index": get_index_status(cfg)}
    emit_payload(result, args.format)
    return 0


def run_sources_reindex(args: argparse.Namespace, cfg: Settings) -> int:
    result = reindex_sources(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "REINDEX_OK" else 2


def run_sources_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_source(args.source_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "SOURCE_DELETE_OK" else 2


def run_conversations_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "CONVERSATIONS_OK", "conversations": list_conversations(cfg)}
    emit_payload(result, args.format)
    return 0


def run_conversations_create(args: argparse.Namespace, cfg: Settings) -> int:
    result = create_conversation(args.title, cfg)
    emit_payload(result, args.format)
    return 0


def run_conversations_show(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    emit_payload(conversation, args.format)
    return 0


def run_conversations_rename(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = rename_conversation(args.conversation_id, " ".join(args.title), cfg)
    except KeyError:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    except ValueError as exc:
        raise MucaroCLIError(str(exc), "Conversation title is required.") from exc
    emit_payload(result, args.format)
    return 0


def run_conversations_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_conversation(args.conversation_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "CONVERSATION_DELETE_OK" else 2


def run_conversations_ask(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    answer = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        conversation_context=build_conversation_context(conversation),
        cfg=cfg,
    )
    updated = append_answer_turn(args.conversation_id, " ".join(args.query), answer, cfg)
    emit_payload({"status": "CONVERSATION_ASK_OK", "answer": answer, "conversation": updated}, args.format)
    if answer.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if answer.get("citation_status") == "CITATION_OK" else 3


def run_runs_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "RUNS_OK", "runs": list_payloads("runs", cfg.sqlite_path)}
    emit_payload(result, args.format)
    return 0


def run_runs_summary(args: argparse.Namespace, cfg: Settings) -> int:
    rows = list_payloads("runs", cfg.sqlite_path)
    citation_ok = sum(1 for run in rows if run.get("citation_status") == "CITATION_OK")
    by_strategy: dict[str, int] = {}
    for run in rows:
        strategy = str(run.get("retrieval_strategy") or "unknown")
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
    result = {
        "status": "RUN_HISTORY_OK",
        "run_count": len(rows),
        "citation_ok": citation_ok,
        "citation_error": len(rows) - citation_ok,
        "by_retrieval_strategy": by_strategy,
        "latest_run_id": rows[-1].get("run_id") if rows else None,
    }
    emit_payload(result, args.format)
    return 0


def run_runs_show(args: argparse.Namespace, cfg: Settings) -> int:
    for run in list_payloads("runs", cfg.sqlite_path):
        if run.get("run_id") == args.run_id:
            emit_payload(run, args.format)
            return 0
    emit_payload({"status": "ERROR_RUN_NOT_FOUND", "run_id": args.run_id}, args.format)
    return 2


def run_tools_list(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    emit_payload(list_tools(), args.format)
    return 0


def run_tools_requests(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_tool_calls(cfg), args.format)
    return 0


def run_tools_request(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = request_tool_call(
            args.tool_name,
            args.arguments,
            cfg=cfg,
            requested_by="cli",
            reason=args.reason,
        )
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_approve(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = approve_tool_call(args.tool_call_id, cfg=cfg, approved_by="cli", reason=args.reason)
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_deny(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = deny_tool_call(args.tool_call_id, cfg=cfg, denied_by="cli", reason=args.reason)
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_run(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = run_tool_call(args.tool_call_id, cfg=cfg, executed_by="cli")
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_audit(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_tool_audit_events(cfg), args.format)
    return 0


def run_memory_list(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_memory_items(cfg, include_disabled=not args.active_only), args.format)
    return 0


def run_memory_remember(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = remember_memory_item(" ".join(args.content), cfg, kind=args.kind, source="cli")
    except MemoryError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_memory_enable(args: argparse.Namespace, cfg: Settings) -> int:
    result = set_memory_enabled(args.memory_id, True, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_UPDATED" else 2


def run_memory_disable(args: argparse.Namespace, cfg: Settings) -> int:
    result = set_memory_enabled(args.memory_id, False, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_UPDATED" else 2


def run_memory_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_memory_item(args.memory_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_DELETE_OK" else 2


def run_memory_export(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(export_memory(cfg), args.format)
    return 0


def run_security_status(args: argparse.Namespace, cfg: Settings) -> int:
    result = build_security_report(cfg, bind_host=args.host)
    emit_payload(result, args.format)
    return 0 if result.get("status") != "SECURITY_FAIL" else 2


def run_index_status(args: argparse.Namespace, cfg: Settings) -> int:
    result = get_index_status(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "INDEX_OK" else 2


def prompt_for_directory_grant(root: Path, cfg: Settings) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise MucaroCLIError(
            "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
            f"Run `mucaro permissions grant {root} --yes` before ingesting this path.",
        )
    print(f"Allow Mucaro AI to access files under: {root}? [y/N] ", end="", file=sys.stderr)
    answer = sys.stdin.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(root))
    try:
        return grant_directory(root, cfg, granted_by="cli_prompt", reason="ingest --grant-directory-access")
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc


def run_plan(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    result = analyze_task(
        " ".join(args.request),
        inputs_available=args.input,
        approval_state=args.approval_state,
    )
    emit_payload(result, args.format)
    return 0


def run_validate(args: argparse.Namespace, cfg: Settings) -> int:
    result = validate_project_json(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "JSON_OK" else 2


def run_permissions_grant(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        path = Path(args.path).expanduser().resolve()
        if not args.yes:
            if not sys.stdin.isatty():
                raise MucaroCLIError(
                    "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
                    f"Run `mucaro permissions grant {path} --yes` after confirming this directory should be accessible.",
                )
            print(f"Allow Mucaro AI to access files under: {path}? [y/N] ", end="", file=sys.stderr)
            if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
                raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(path))
        result = grant_directory(path, cfg, granted_by="cli", reason=args.reason)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_permissions_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "DIRECTORY_PERMISSIONS_OK", "grants": load_directory_grants(cfg)}
    emit_payload(result, args.format)
    return 0


def run_permissions_check(args: argparse.Namespace, cfg: Settings) -> int:
    result = check_directory_permission(args.path, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "PERMISSION_OK" else 2


def run_permissions_revoke(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = revoke_directory(args.path, cfg)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_serve(args: argparse.Namespace, cfg: Settings) -> int:
    import uvicorn

    try:
        enforce_startup_security(cfg, bind_host=args.host)
    except SecurityProfileError as exc:
        emit_error(exc.status, exc.detail)
        return 2
    uvicorn.run("backend.app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: Sequence[str] | None = None, cfg: Settings = settings) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, cfg)
    except MucaroCLIError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return exc.exit_code
    except DirectoryPermissionError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return 2
    except ValueError as exc:
        emit_error("ERROR_INVALID_INPUT", str(exc), getattr(args, "format", "json"))
        return 2
    except OSError as exc:
        emit_error("ERROR_FILESYSTEM", str(exc), getattr(args, "format", "json"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
