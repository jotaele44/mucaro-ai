from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
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
from .packaging import build_package_status, build_release_artifacts, verify_release_artifact
from .permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)
from .product_audit import build_product_audit
from .release_receipts import find_release_receipt, list_release_receipts, load_release_receipt, summarize_release_receipts
from .retrieval import MAX_TOP_K, MIN_TOP_K, RETRIEVAL_STRATEGIES
from .retrieval_eval import DEFAULT_FIXTURE, build_retrieval_eval_report, format_retrieval_eval_text
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


GOVERNANCE_REPORT_SCHEMA_VERSION = "mucaro_governance_report_v1"
GOVERNANCE_REPORT_DIR = Path("data") / "exports" / "governance_reports"


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

    eval_cmd = subcommands.add_parser("eval", help="Run measurable local quality evaluations.")
    eval_subcommands = eval_cmd.add_subparsers(dest="eval_command", required=True)

    eval_retrieval = eval_subcommands.add_parser("retrieval", help="Evaluate local retrieval strategies against a fixture.")
    eval_retrieval.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Retrieval evaluation fixture path.")
    eval_retrieval.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to score ({MIN_TOP_K}-{MAX_TOP_K}).")
    eval_retrieval.add_argument("--min-hybrid-recall", type=float, default=0.80, help="Minimum recall@k required for hybrid retrieval.")
    eval_retrieval.add_argument("--min-no-context-accuracy", type=float, default=1.00, help="Minimum no-context accuracy required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-semantic-recall", type=float, default=0.80, help="Minimum recall@k required for semantic retrieval.")
    eval_retrieval.add_argument("--min-semantic-no-context", type=float, default=1.00, help="Minimum no-context accuracy required for semantic retrieval.")
    eval_retrieval.add_argument("--min-hybrid-precision", type=float, default=0.20, help="Minimum context precision@k required for hybrid retrieval.")
    eval_retrieval.add_argument("--min-semantic-precision", type=float, default=0.20, help="Minimum context precision@k required for semantic retrieval.")
    eval_retrieval.add_argument("--min-expected-binding-recall", type=float, default=0.95, help="Minimum expected binding recall@k required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-multi-binding-full-hit", type=float, default=0.60, help="Minimum multi-binding full-hit rate required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-category-recall", type=float, default=0.0, help="Optional minimum recall@k required for every positive-query category.")
    eval_retrieval.add_argument(
        "--min-category-binding-recall",
        type=float,
        default=0.0,
        help="Optional minimum expected-binding recall@k required for every positive-query category.",
    )
    eval_retrieval.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    eval_retrieval.set_defaults(handler=run_eval_retrieval)

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

    certify_github = certify_subcommands.add_parser(
        "github-actions",
        help="Diagnose read-only GitHub Actions governance and hosted runner startup.",
    )
    certify_github.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_github.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    certify_github.add_argument("--workflow", default="CI", help="Primary GitHub Actions workflow name to inspect.")
    certify_github.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    certify_github.set_defaults(handler=run_certify_github_actions)

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

    package_cmd = subcommands.add_parser("package", help="Inspect packaging and distribution readiness.")
    package_subcommands = package_cmd.add_subparsers(dest="package_command", required=True)

    package_status = package_subcommands.add_parser("status", help="Show packaging readiness without writing artifacts.")
    package_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_status.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    package_status.set_defaults(handler=run_package_status)

    package_build = package_subcommands.add_parser("build", help="Build the governed source-release artifact bundle.")
    package_build.add_argument("--output-dir", type=Path, default=None, help="Directory for generated package artifacts.")
    package_build.add_argument("--allow-dirty", action="store_true", help="Allow artifact creation from a dirty worktree.")
    package_build.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_build.set_defaults(handler=run_package_build)

    package_verify = package_subcommands.add_parser("verify", help="Verify a governed source-release artifact manifest.")
    package_verify.add_argument("manifest", type=Path, help="Artifact manifest path to verify.")
    package_verify.add_argument("--require-current-head", action="store_true", help="Require manifest head_sha to match current Git HEAD.")
    package_verify.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_verify.set_defaults(handler=run_package_verify)

    release = subcommands.add_parser("release", help="Inspect bounded release-state progress.")
    release_subcommands = release.add_subparsers(dest="release_command", required=True)

    release_state = release_subcommands.add_parser("state", help="Classify the current release path into explicit states.")
    release_state.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_state.add_argument("--strict", action="store_true", help="Exit nonzero until the governed release state is ready.")
    release_state.add_argument(
        "--include-github",
        action="store_true",
        help="Include read-only GitHub governance evidence when gh is available.",
    )
    release_state.add_argument(
        "--github-workflow",
        default="CI",
        help="GitHub Actions workflow name to use for hosted CI evidence with --include-github.",
    )
    release_state.set_defaults(handler=run_release_state)

    release_governance = release_subcommands.add_parser(
        "governance-report",
        help="Build a current-head governance handoff report.",
    )
    release_governance.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_governance.add_argument("--strict", action="store_true", help="Exit nonzero until governed release state is ready.")
    release_governance.add_argument("--save", action="store_true", help="Write a JSON governance report artifact under data/exports/governance_reports.")
    release_governance.add_argument("--output", type=Path, help="Optional JSON artifact path. Implies --save.")
    release_governance.add_argument("--verify", type=Path, help="Verify a saved governance report artifact instead of building a new report.")
    release_governance.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require the saved report head_sha to match current Git HEAD.",
    )
    release_governance.add_argument(
        "--expected-sha256",
        help="With --verify, require the artifact file SHA-256 to match this value.",
    )
    release_governance.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_governance.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_governance.set_defaults(handler=run_release_governance_report)

    release_receipts = release_subcommands.add_parser("receipts", help="List local release certification receipts.")
    release_receipts.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_receipts.add_argument("--current-head", action="store_true", help="Only show receipts for the current Git HEAD.")
    release_receipts.set_defaults(handler=run_release_receipts)

    release_receipt = release_subcommands.add_parser("receipt", help="Show one local release certification receipt.")
    release_receipt.add_argument("receipt_id", help="Receipt file stem or JSON filename under data/exports/release_receipts.")
    release_receipt.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_receipt.set_defaults(handler=run_release_receipt_show)

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
    elif status in {"GITHUB_ACTIONS_DIAGNOSTIC_OK", "GITHUB_ACTIONS_DIAGNOSTIC_WARN", "GITHUB_ACTIONS_DIAGNOSTIC_FAIL"}:
        print(f"repo={payload.get('repo')}")
        print(f"branch={payload.get('branch')}")
        print(f"head_sha={payload.get('head_sha')}")
        startup_blocker = payload.get("startup_blocker", {})
        if isinstance(startup_blocker, dict):
            print(f"startup_blocker_status={startup_blocker.get('status')}")
            causes = startup_blocker.get("suspected_causes", [])
            if isinstance(causes, list):
                print(f"startup_blocker_causes={','.join(str(cause) for cause in causes)}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {"RETRIEVAL_EVAL_OK", "RETRIEVAL_EVAL_FAILED"}:
        print(f"fixture={payload.get('fixture')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(f"chunks={counts.get('chunks')}")
            print(f"queries={counts.get('queries')}")
            if "positive_queries" in counts:
                print(f"positive_queries={counts.get('positive_queries')}")
            if "no_context_queries" in counts:
                print(f"no_context_queries={counts.get('no_context_queries')}")
            if "expected_bindings" in counts:
                print(f"expected_bindings={counts.get('expected_bindings')}")
            if "source_ids" in counts:
                print(f"source_ids={counts.get('source_ids')}")
            if "multi_binding_queries" in counts:
                print(f"multi_binding_queries={counts.get('multi_binding_queries')}")
            if "query_categories" in counts:
                print(f"query_categories={counts.get('query_categories')}")
            print(f"strategies={counts.get('strategies')}")
        fixture_validation = payload.get("fixture_validation", {})
        if isinstance(fixture_validation, dict):
            print(f"fixture_validation={fixture_validation.get('status')}")
            for check in fixture_validation.get("checks", []):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
        print(f"min_hybrid_recall={payload.get('min_hybrid_recall')}")
        print(f"min_no_context_accuracy={payload.get('min_no_context_accuracy')}")
        print(f"min_semantic_recall={payload.get('min_semantic_recall')}")
        print(f"min_semantic_no_context={payload.get('min_semantic_no_context')}")
        print(f"min_hybrid_precision={payload.get('min_hybrid_precision')}")
        print(f"min_semantic_precision={payload.get('min_semantic_precision')}")
        print(f"min_expected_binding_recall={payload.get('min_expected_binding_recall')}")
        print(f"min_multi_binding_full_hit={payload.get('min_multi_binding_full_hit')}")
        print(f"min_category_recall={payload.get('min_category_recall')}")
        print(f"min_category_binding_recall={payload.get('min_category_binding_recall')}")
        for line in format_retrieval_eval_text(payload)[:-1]:
            print(line)
    elif status in {"PACKAGE_READY", "PACKAGE_WARN", "PACKAGE_FAIL"}:
        print(f"project_root={payload.get('project_root')}")
        python_package = payload.get("python_package", {})
        frontend_package = payload.get("frontend_package", {})
        if isinstance(python_package, dict):
            print(f"python_package={python_package.get('name')}:{python_package.get('version')}")
            print(f"cli_entrypoint={python_package.get('entrypoint')}")
        if isinstance(frontend_package, dict):
            print(f"frontend_package={frontend_package.get('name')}:{frontend_package.get('version')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"PACKAGE_ARTIFACT_READY", "PACKAGE_ARTIFACT_BLOCKED", "PACKAGE_ARTIFACT_VERIFIED", "PACKAGE_ARTIFACT_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        if payload.get("output_dir"):
            print(f"output_dir={payload.get('output_dir')}")
        if payload.get("manifest_path"):
            print(f"manifest_path={payload.get('manifest_path')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            if manifest.get("path"):
                print(f"manifest_path={manifest.get('path')}")
            if manifest.get("sha256"):
                print(f"manifest_sha256={manifest.get('sha256')}")
            print(f"manifest_member_count={manifest.get('member_count')}")
    elif status in {"RELEASE_STATE_READY", "RELEASE_STATE_OPEN"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"highest_local_state={payload.get('highest_local_state')}")
        print(f"include_github={payload.get('include_github')}")
        git_payload = payload.get("git", {})
        if isinstance(git_payload, dict):
            print(f"head_sha={git_payload.get('head_sha')}")
            print(f"worktree_clean={git_payload.get('worktree_clean')}")
            print(f"upstream={git_payload.get('upstream')}")
        for state in payload.get("states", []):
            detail = str(state.get("evidence")).replace("\n", "\\n")
            print(f"{state.get('status')} {state.get('state')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"GOVERNANCE_REPORT_READY", "GOVERNANCE_REPORT_OPEN"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tags_at_head', []))}")
        print(f"product_audit_status={payload.get('product_audit_status')}")
        print(f"package_status={payload.get('package_status')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        receipt_summary = payload.get("current_head_receipts_summary", {})
        if isinstance(receipt_summary, dict):
            print(f"receipt_count={receipt_summary.get('total')}")
            print(f"receipt_valid={receipt_summary.get('valid')}")
            print(f"receipt_invalid={receipt_summary.get('invalid')}")
        startup_blocker = payload.get("startup_blocker", {})
        if isinstance(startup_blocker, dict):
            print(f"startup_blocker_status={startup_blocker.get('status')}")
            causes = startup_blocker.get("suspected_causes", [])
            if isinstance(causes, list):
                print(f"startup_blocker_causes={','.join(str(cause) for cause in causes)}")
        github_evidence = payload.get("github_governance_evidence")
        if github_evidence:
            github_evidence_text = str(github_evidence).replace("\n", "\\n")
            print(f"github_governance_evidence={github_evidence_text}")
        for receipt in payload.get("current_head_receipts", []):
            if isinstance(receipt, dict):
                print(
                    f"receipt={receipt.get('receipt_id')} {receipt.get('receipt_type')} "
                    f"{receipt.get('status')} {receipt.get('file_sha256')}"
                )
        for command in payload.get("next_commands", []):
            print(f"command={command}")
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {"GOVERNANCE_REPORT_VERIFIED", "GOVERNANCE_REPORT_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
            print(f"artifact_schema_version={artifact.get('schema_version')}")
            print(f"artifact_created_utc={artifact.get('created_utc')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {"RELEASE_RECEIPTS_OK", "RELEASE_RECEIPT_OK"}:
        if payload.get("receipt_id"):
            print(f"receipt_id={payload.get('receipt_id')}")
        if payload.get("head_sha"):
            print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        if "count" in payload:
            print(f"count={payload.get('count')}")
        summary_counts = payload.get("summary")
        if isinstance(summary_counts, dict) and "total" in summary_counts:
            print(f"valid={summary_counts.get('valid')}")
            print(f"invalid={summary_counts.get('invalid')}")
            print(f"by_type={json.dumps(summary_counts.get('by_type', {}), sort_keys=True)}")
            print(f"by_status={json.dumps(summary_counts.get('by_status', {}), sort_keys=True)}")
        rows = payload.get("receipts", [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    print(
                        f"{'VALID' if row.get('valid') else 'INVALID'} {row.get('receipt_id')} "
                        f"{row.get('receipt_type')} {row.get('status')} {row.get('head_sha')} {row.get('file_sha256')}"
                    )
        summary = payload.get("summary")
        if status == "RELEASE_RECEIPT_OK" and isinstance(summary, dict):
            print(
                f"{'VALID' if summary.get('valid') else 'INVALID'} {summary.get('receipt_id')} "
                f"{summary.get('receipt_type')} {summary.get('status')} {summary.get('head_sha')} {summary.get('file_sha256')}"
            )
            if summary.get("validation_error"):
                print(f"validation_error={summary.get('validation_error')}")
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            print(f"created_utc={receipt.get('created_utc')}")
            print(f"command={' '.join(str(part) for part in receipt.get('command', []))}")
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


def run_eval_retrieval(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    report = build_retrieval_eval_report(
        fixture_path=args.fixture,
        top_k=args.top_k,
        min_hybrid_recall=args.min_hybrid_recall,
        min_no_context_accuracy=args.min_no_context_accuracy,
        min_semantic_recall=args.min_semantic_recall,
        min_semantic_no_context=args.min_semantic_no_context,
        min_hybrid_precision=args.min_hybrid_precision,
        min_semantic_precision=args.min_semantic_precision,
        min_expected_binding_recall=args.min_expected_binding_recall,
        min_multi_binding_full_hit=args.min_multi_binding_full_hit,
        min_category_recall=args.min_category_recall,
        min_category_binding_recall=args.min_category_binding_recall,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == "RETRIEVAL_EVAL_OK" else 2


def run_package_status(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_package_status(cfg.project_root)
    emit_payload(report, args.format)
    if report["status"] == "PACKAGE_FAIL":
        return 2
    if args.strict and report["status"] != "PACKAGE_READY":
        return 2
    return 0


def run_package_build(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_artifacts(cfg.project_root, output_dir=args.output_dir, allow_dirty=args.allow_dirty)
    emit_payload(report, args.format)
    return 0 if report["status"] == "PACKAGE_ARTIFACT_READY" else 2


def run_package_verify(args: argparse.Namespace, cfg: Settings) -> int:
    report = verify_release_artifact(args.manifest, root=cfg.project_root, require_current_head=args.require_current_head)
    emit_payload(report, args.format)
    return 0 if report["status"] == "PACKAGE_ARTIFACT_VERIFIED" else 2


def run_release_state(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_state_report(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
    )
    emit_payload(report, args.format)
    if args.strict and report["status"] != "RELEASE_STATE_READY":
        return 2
    return 0


def build_release_governance_report(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    release_state = build_release_state_report(
        cfg,
        include_github=True,
        github_workflow=github_workflow,
    )
    github_actions = build_github_actions_diagnostic_status(
        cfg,
        workflow_name=github_workflow,
        probe_workflow_name=probe_workflow,
    )
    product_audit = build_product_audit(cfg)
    package = build_package_status(cfg.project_root)
    git_payload = release_state.get("git", {}) if isinstance(release_state.get("git"), dict) else {}
    head_sha = git_payload.get("head_sha")
    receipts = list_release_receipts(cfg.project_root)
    current_head_receipts = [row for row in receipts if row.get("head_sha") == head_sha]
    states = release_state.get("states", [])
    github_state = next(
        (state for state in states if isinstance(state, dict) and state.get("state") == "GITHUB_GOVERNANCE_OPEN"),
        {},
    )
    governed_state = next(
        (state for state in states if isinstance(state, dict) and state.get("state") == "GOVERNED_RELEASE_READY"),
        {},
    )
    startup_blocker = github_actions.get("startup_blocker", {})
    next_commands = [str(command) for command in release_state.get("next_commands", [])]
    next_actions: list[str] = []
    for action in github_actions.get("next_actions", []):
        next_actions.append(str(action))
    governed_ready = release_state.get("status") == "RELEASE_STATE_READY" and governed_state.get("status") == "PASS"
    return {
        "status": "GOVERNANCE_REPORT_READY" if governed_ready else "GOVERNANCE_REPORT_OPEN",
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "current_state": release_state.get("current_state"),
        "head_sha": head_sha,
        "tags_at_head": git_payload.get("tags_at_head", []),
        "release_state_status": release_state.get("status"),
        "product_audit_status": product_audit.get("status"),
        "product_audit_counts": product_audit.get("counts"),
        "package_status": package.get("status"),
        "certification_status": release_state.get("certification_status"),
        "github_actions_status": github_actions.get("status"),
        "github_governance_evidence": github_state.get("evidence"),
        "governed_release_evidence": governed_state.get("evidence"),
        "startup_blocker": startup_blocker,
        "git": git_payload,
        "release_states": release_state.get("states", []),
        "github_actions": github_actions,
        "current_head_receipts_summary": summarize_release_receipts(current_head_receipts),
        "current_head_receipts": current_head_receipts,
        "next_commands": sorted(set(next_commands)),
        "next_actions": sorted(set(next_actions)),
    }


def write_release_governance_report(
    cfg: Settings,
    report: dict[str, Any],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    head_sha = str(report.get("head_sha") or "unknown")
    path = output_path or cfg.project_root / GOVERNANCE_REPORT_DIR / f"governance-report-{head_sha[:12]}.json"
    if not path.is_absolute():
        path = cfg.project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": GOVERNANCE_REPORT_SCHEMA_VERSION,
        "created_utc": _utc_now(),
        "report": report,
    }
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": GOVERNANCE_REPORT_SCHEMA_VERSION,
    }


def _parse_utc_timestamp(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str) or not value:
        return False, "missing created_utc"
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False, f"invalid created_utc={value}"
    if parsed.tzinfo is None:
        return False, "created_utc missing timezone"
    return True, value


def _is_hex_sha(value: Any, *, length: int = 40) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def verify_release_governance_report(
    cfg: Settings,
    artifact_path: Path,
    *,
    require_current_head: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    path = artifact_path if artifact_path.is_absolute() else cfg.project_root / artifact_path
    checks: list[dict[str, str]] = []
    artifact: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None

    if path.exists() and path.is_file():
        checks.append(_certification_gate("artifact_exists", "PASS", str(path)))
        artifact_sha256 = _sha256_file(path)
        artifact_size_bytes = path.stat().st_size
        checks.append(_certification_gate("artifact_sha256", "PASS", artifact_sha256))
        if expected_sha256:
            expected = expected_sha256.strip().lower()
            checks.append(
                _certification_gate(
                    "expected_sha256",
                    "PASS" if artifact_sha256 == expected else "FAIL",
                    f"expected={expected} actual={artifact_sha256}",
                )
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            checks.append(_certification_gate("artifact_json", "FAIL", str(exc)))
            loaded = None
        if isinstance(loaded, dict):
            artifact = loaded
            checks.append(_certification_gate("artifact_json", "PASS", "valid JSON object"))
        elif loaded is not None:
            checks.append(_certification_gate("artifact_json", "FAIL", "top-level JSON value is not an object"))
    else:
        checks.append(_certification_gate("artifact_exists", "FAIL", str(path)))

    report: dict[str, Any] | None = None
    if artifact is not None:
        schema_version = artifact.get("schema_version")
        checks.append(
            _certification_gate(
                "schema_version",
                "PASS" if schema_version == GOVERNANCE_REPORT_SCHEMA_VERSION else "FAIL",
                str(schema_version),
            )
        )
        created_ok, created_detail = _parse_utc_timestamp(artifact.get("created_utc"))
        checks.append(_certification_gate("created_utc", "PASS" if created_ok else "FAIL", created_detail))
        raw_report = artifact.get("report")
        if isinstance(raw_report, dict):
            report = raw_report
            checks.append(_certification_gate("report_payload", "PASS", "report object present"))
        else:
            checks.append(_certification_gate("report_payload", "FAIL", "report object missing"))

    head_sha: str | None = None
    if report is not None:
        raw_head_sha = report.get("head_sha")
        if _is_hex_sha(raw_head_sha):
            head_sha = str(raw_head_sha)
            checks.append(_certification_gate("report_head_sha", "PASS", head_sha))
        else:
            checks.append(_certification_gate("report_head_sha", "FAIL", str(raw_head_sha)))

        status = report.get("status")
        checks.append(
            _certification_gate(
                "report_status",
                "PASS" if status in {"GOVERNANCE_REPORT_READY", "GOVERNANCE_REPORT_OPEN"} else "FAIL",
                str(status),
            )
        )
        current_state = report.get("current_state")
        checks.append(
            _certification_gate(
                "current_state",
                "PASS" if isinstance(current_state, str) and bool(current_state) else "FAIL",
                str(current_state),
            )
        )
        receipt_summary = report.get("current_head_receipts_summary")
        checks.append(
            _certification_gate(
                "receipt_summary",
                "PASS" if isinstance(receipt_summary, dict) else "FAIL",
                "present" if isinstance(receipt_summary, dict) else "missing",
            )
        )
        startup_blocker = report.get("startup_blocker")
        checks.append(
            _certification_gate(
                "startup_blocker",
                "PASS" if isinstance(startup_blocker, dict) else "FAIL",
                "present" if isinstance(startup_blocker, dict) else "missing",
            )
        )

    current_head: str | None = None
    if require_current_head:
        head_ok, current_head_output = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = current_head_output if head_ok else None
        checks.append(
            _certification_gate(
                "current_head_binding",
                "PASS" if head_ok and head_sha == current_head_output else "FAIL",
                f"report_head={head_sha} current_head={current_head_output if head_ok else current_head_output or 'unavailable'}",
            )
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "GOVERNANCE_REPORT_VERIFIED" if not failed else "GOVERNANCE_REPORT_VERIFY_FAILED",
        "project_root": str(cfg.project_root),
        "artifact": {
            "path": str(path),
            "sha256": artifact_sha256,
            "size_bytes": artifact_size_bytes,
            "schema_version": artifact.get("schema_version") if artifact else None,
            "created_utc": artifact.get("created_utc") if artifact else None,
        },
        "head_sha": head_sha,
        "current_head": current_head,
        "checks": checks,
    }


def run_release_governance_report(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify:
        report = verify_release_governance_report(
            cfg,
            args.verify,
            require_current_head=args.require_current_head,
            expected_sha256=args.expected_sha256,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "GOVERNANCE_REPORT_VERIFIED" else 2
    if args.require_current_head or args.expected_sha256:
        raise MucaroCLIError("ERROR_GOVERNANCE_REPORT_VERIFY_OPTION", "--require-current-head and --expected-sha256 require --verify")
    report = build_release_governance_report(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    if args.save or args.output:
        report = {**report, "export": write_release_governance_report(cfg, report, output_path=args.output)}
    emit_payload(report, args.format)
    if args.strict and report["status"] != "GOVERNANCE_REPORT_READY":
        return 2
    return 0


def run_release_receipts(args: argparse.Namespace, cfg: Settings) -> int:
    receipts = list_release_receipts(cfg.project_root)
    current_head = None
    if args.current_head:
        head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = head_sha if head_ok else None
        receipts = [row for row in receipts if row.get("head_sha") == current_head]
    payload = {
        "status": "RELEASE_RECEIPTS_OK",
        "project_root": str(cfg.project_root),
        "current_head": current_head,
        "count": len(receipts),
        "summary": summarize_release_receipts(receipts),
        "receipts": receipts,
    }
    emit_payload(payload, args.format)
    return 0


def run_release_receipt_show(args: argparse.Namespace, cfg: Settings) -> int:
    payload = find_release_receipt(cfg.project_root, args.receipt_id)
    if payload is None:
        raise MucaroCLIError("ERROR_RELEASE_RECEIPT_NOT_FOUND", args.receipt_id)
    payload = {
        "status": "RELEASE_RECEIPT_OK",
        "project_root": str(cfg.project_root),
        "receipt_id": args.receipt_id,
        **payload,
    }
    emit_payload(payload, args.format)
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_gh_error(output: str) -> str:
    if not output:
        return "gh command failed"
    try:
        payload, _end = json.JSONDecoder().raw_decode(output)
    except json.JSONDecodeError:
        return output
    if not isinstance(payload, dict):
        return output
    message = payload.get("message")
    status = payload.get("status")
    if not message:
        return output
    prefix = f"HTTP {status}: " if status else ""
    return f"{prefix}{message}"


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
        return False, None, _format_gh_error(output)
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


def _run_failed_before_jobs(latest: dict[str, Any], jobs_count: int | None) -> bool:
    return (
        latest.get("status") == "completed"
        and latest.get("conclusion") == "startup_failure"
        and jobs_count == 0
    )


def _github_startup_blocker(
    *,
    ci_latest: dict[str, Any] | None,
    ci_jobs_count: int | None,
    probe_latest: dict[str, Any] | None = None,
    probe_jobs_count: int | None = None,
    branch_protection_error: str | None = None,
    billing_error: str | None = None,
    self_hosted_runner_count: int | None = None,
) -> dict[str, Any]:
    ci_before_jobs = bool(ci_latest and _run_failed_before_jobs(ci_latest, ci_jobs_count))
    probe_before_jobs = bool(probe_latest and _run_failed_before_jobs(probe_latest, probe_jobs_count))
    isolated = ci_before_jobs and (probe_latest is None or probe_before_jobs)
    suspected_causes: list[str] = []
    next_actions: list[str] = []
    if isolated:
        suspected_causes.extend(["actions_billing_or_spending_limit", "hosted_runner_availability", "account_or_repository_policy"])
        next_actions.append("Inspect repository Settings -> Actions -> General and Settings -> Actions -> Runners.")
    if branch_protection_error and ("Upgrade to GitHub Pro" in branch_protection_error or "make this repository public" in branch_protection_error):
        suspected_causes.append("private_branch_protection_plan_gate")
        next_actions.append("Upgrade GitHub plan or make the repository public before enforcing private-repo branch protection.")
    if billing_error:
        suspected_causes.append("actions_billing_visibility_unverified")
        next_actions.append("Run `gh auth refresh -h github.com -s user` and inspect GitHub Actions billing/minutes.")
    return {
        "status": "FAIL" if isolated else "OPEN" if suspected_causes else "PASS",
        "ci_before_jobs": ci_before_jobs,
        "probe_before_jobs": probe_before_jobs,
        "isolated_before_jobs": isolated,
        "self_hosted_runner_count": self_hosted_runner_count,
        "branch_protection_error": branch_protection_error,
        "billing_error": billing_error,
        "suspected_causes": sorted(set(suspected_causes)),
        "next_actions": sorted(set(next_actions)),
    }


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
        "branch_protection_api": None,
        "startup_blocker": None,
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

    protection_error_for_blocker: str | None = None
    protection_api_ok, protection_api_payload, protection_api_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}/protection"])
    github["branch_protection_api"] = {"available": protection_api_ok}
    if protection_api_ok and isinstance(protection_api_payload, dict):
        github["branch_protection_api"]["required_status_checks"] = protection_api_payload.get("required_status_checks")
        gates.append(_certification_gate("github_branch_protection_api", "PASS", "branch protection endpoint readable"))
    else:
        protection_error_for_blocker = protection_api_error
        github["branch_protection_api"]["error"] = protection_api_error
        gates.append(_certification_gate("github_branch_protection_api", "WARN", protection_api_error or "branch protection endpoint unavailable"))

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
            jobs_count: int | None = None
            jobs_error: str | None = None
            if latest.get("status") == "completed" and latest.get("conclusion") == "startup_failure":
                jobs_count, jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=latest.get("databaseId"))
            latest_ci = {
                "database_id": latest.get("databaseId"),
                "status": latest.get("status"),
                "conclusion": latest.get("conclusion"),
                "head_sha": latest.get("headSha"),
                "url": latest.get("url"),
                "jobs_count": jobs_count,
                "startup_layer": "before_jobs" if _run_failed_before_jobs(latest, jobs_count) else None,
            }
            github["latest_ci"] = latest_ci
            ci_matches_head = not head_sha or latest.get("headSha") == head_sha
            ci_passed = latest.get("status") == "completed" and latest.get("conclusion") == "success"
            if ci_matches_head and ci_passed:
                gates.append(_certification_gate("github_latest_ci", "PASS", f"success {latest.get('headSha')}"))
            elif ci_matches_head and latest.get("status") == "completed":
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "FAIL",
                        f"{latest.get('status')} {latest.get('conclusion') or 'unknown'} {latest.get('headSha')}",
                    )
                )
                if jobs_error:
                    gates.append(_certification_gate("github_ci_jobs_lookup", "WARN", jobs_error))
                elif _run_failed_before_jobs(latest, jobs_count):
                    gates.append(
                        _certification_gate(
                            "github_hosted_runner_startup",
                            "FAIL",
                            "workflow failed before GitHub created jobs; inspect Actions billing, spending limit, runner availability, and account policy",
                        )
                    )
                    github["startup_blocker"] = _github_startup_blocker(
                        ci_latest=latest,
                        ci_jobs_count=jobs_count,
                        branch_protection_error=protection_error_for_blocker,
                    )
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


def _latest_workflow_run(
    cfg: Settings,
    *,
    repo: str,
    workflow_name: str,
    branch: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow_name,
        "--limit",
        "1",
        "--json",
        "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
    ]
    if branch:
        args.extend(["--branch", branch])
    ok, payload, error = _gh_json(cfg, args)
    if not ok or not isinstance(payload, list) or not payload:
        return None, error or "workflow run lookup failed"
    latest = payload[0]
    if not isinstance(latest, dict):
        return None, "workflow run payload invalid"
    return latest, None


def _workflow_run_jobs_count(cfg: Settings, *, repo: str, run_id: Any) -> tuple[int | None, str | None]:
    if not run_id:
        return None, "run id unavailable"
    ok, payload, error = _gh_json(cfg, ["api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
    if not ok or not isinstance(payload, dict):
        return None, error or "workflow jobs lookup failed"
    total_count = payload.get("total_count")
    return total_count if isinstance(total_count, int) else None, None


def _run_summary(latest: dict[str, Any] | None, jobs_count: int | None = None) -> dict[str, Any] | None:
    if latest is None:
        return None
    return {
        "database_id": latest.get("databaseId"),
        "status": latest.get("status"),
        "conclusion": latest.get("conclusion"),
        "head_sha": latest.get("headSha"),
        "url": latest.get("url"),
        "jobs_count": jobs_count,
    }


def _classify_latest_run(
    *,
    name: str,
    latest: dict[str, Any] | None,
    error: str | None,
    head_sha: str | None,
    jobs_count: int | None,
) -> dict[str, str]:
    if latest is None:
        return _certification_gate(name, "WARN", error or "latest workflow run unavailable")
    run_head = latest.get("headSha")
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    jobs_detail = f" jobs={jobs_count}" if jobs_count is not None else ""
    if head_sha and run_head != head_sha:
        return _certification_gate(name, "FAIL", f"latest {run_head} does not match HEAD {head_sha}{jobs_detail}")
    if status == "completed" and conclusion == "success":
        return _certification_gate(name, "PASS", f"success {run_head}{jobs_detail}")
    if status == "completed":
        return _certification_gate(name, "FAIL", f"{status} {conclusion or 'unknown'} {run_head}{jobs_detail}")
    return _certification_gate(name, "WARN", f"{status} {conclusion or 'pending'} {run_head}{jobs_detail}")


def build_github_actions_diagnostic_status(
    cfg: Settings,
    *,
    workflow_name: str = "CI",
    probe_workflow_name: str = "CI Startup Probe",
) -> dict[str, Any]:
    branch_ok, branch = _git_output(cfg, ["branch", "--show-current"])
    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    remote_ok, remote_url = _git_output(cfg, ["remote", "get-url", "origin"])
    repo = _github_repo_from_origin(remote_url if remote_ok else None)
    checks: list[dict[str, str]] = []
    next_actions: list[str] = []

    if not repo:
        checks.append(_certification_gate("github_repo", "FAIL", "origin is not a github.com repository"))
        return {
            "status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "repo": None,
            "branch": branch if branch_ok else None,
            "head_sha": head_sha if head_ok else None,
            "checks": checks,
            "workflows": {},
            "next_actions": ["Configure origin to a GitHub repository before running hosted governance diagnostics."],
        }
    checks.append(_certification_gate("github_repo", "PASS", repo))

    actions_ok, actions_payload, actions_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/permissions"])
    if actions_ok and isinstance(actions_payload, dict):
        enabled = actions_payload.get("enabled") is True
        allowed_actions = actions_payload.get("allowed_actions")
        checks.append(_certification_gate("actions_enabled", "PASS" if enabled else "FAIL", f"enabled={enabled} allowed_actions={allowed_actions}"))
        if not enabled:
            next_actions.append("Enable GitHub Actions in repository Settings -> Actions -> General.")
    else:
        checks.append(_certification_gate("actions_enabled", "WARN", actions_error or "actions permission lookup failed"))

    token_ok, token_payload, token_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/permissions/workflow"])
    if token_ok and isinstance(token_payload, dict):
        default_permissions = token_payload.get("default_workflow_permissions")
        checks.append(_certification_gate("workflow_token_permissions", "PASS", f"default_workflow_permissions={default_permissions}"))
    else:
        checks.append(_certification_gate("workflow_token_permissions", "WARN", token_error or "workflow token permission lookup failed"))

    branch_protection: dict[str, Any] = {"available": False}
    branch_protection_error_for_blocker: str | None = None
    if not branch_ok:
        branch_protection["error"] = "branch unavailable"
        branch_protection_error_for_blocker = "branch unavailable"
        checks.append(_certification_gate("branch_protection_api", "WARN", "branch unavailable"))
    else:
        protection_ok, protection_payload, protection_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}/protection"])
        branch_protection["available"] = protection_ok
        if protection_ok and isinstance(protection_payload, dict):
            branch_protection["required_status_checks"] = protection_payload.get("required_status_checks")
            checks.append(_certification_gate("branch_protection_api", "PASS", "branch protection endpoint readable"))
        else:
            branch_protection["error"] = protection_error
            branch_protection_error_for_blocker = protection_error
            checks.append(_certification_gate("branch_protection_api", "WARN", protection_error or "branch protection endpoint unavailable"))
            if protection_error and ("Upgrade to GitHub Pro" in protection_error or "make this repository public" in protection_error):
                next_actions.append("Upgrade GitHub plan or make the repository public before enforcing private-repo branch protection.")

    runners_ok, runners_payload, runners_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/runners"])
    runner_inventory: dict[str, Any] = {"available": runners_ok}
    self_hosted_runner_count: int | None = None
    if runners_ok and isinstance(runners_payload, dict):
        total_count = runners_payload.get("total_count")
        self_hosted_runner_count = total_count if isinstance(total_count, int) else None
        runner_inventory["self_hosted_total_count"] = total_count
        checks.append(
            _certification_gate(
                "self_hosted_runner_inventory",
                "PASS",
                f"self_hosted_total_count={total_count}; ubuntu-latest uses GitHub-hosted runner capacity",
            )
        )
    else:
        runner_inventory["error"] = runners_error
        checks.append(_certification_gate("self_hosted_runner_inventory", "WARN", runners_error or "runner inventory lookup failed"))

    workflows_ok, workflows_payload, workflows_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/workflows"])
    workflows: dict[str, Any] = {}
    if workflows_ok and isinstance(workflows_payload, dict):
        workflow_rows = workflows_payload.get("workflows", [])
        if isinstance(workflow_rows, list):
            for row in workflow_rows:
                if isinstance(row, dict):
                    workflows[str(row.get("name"))] = {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "path": row.get("path"),
                        "state": row.get("state"),
                    }
        for expected in [workflow_name, probe_workflow_name]:
            row = workflows.get(expected)
            if row and row.get("state") == "active":
                checks.append(_certification_gate(f"workflow_registered:{expected}", "PASS", f"{row.get('path')} active"))
            else:
                checks.append(_certification_gate(f"workflow_registered:{expected}", "WARN", "workflow not registered active"))
    else:
        checks.append(_certification_gate("workflow_registry", "WARN", workflows_error or "workflow registry lookup failed"))

    ci_latest, ci_error = _latest_workflow_run(cfg, repo=repo, workflow_name=workflow_name, branch=branch if branch_ok else None)
    ci_jobs_count, ci_jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=ci_latest.get("databaseId") if ci_latest else None)
    if ci_jobs_error and ci_latest:
        checks.append(_certification_gate("ci_jobs_lookup", "WARN", ci_jobs_error))
    checks.append(
        _classify_latest_run(
            name="latest_ci",
            latest=ci_latest,
            error=ci_error,
            head_sha=head_sha if head_ok else None,
            jobs_count=ci_jobs_count,
        )
    )

    probe_latest, probe_error = _latest_workflow_run(
        cfg,
        repo=repo,
        workflow_name=probe_workflow_name,
        branch=branch if branch_ok else None,
    )
    probe_jobs_count, probe_jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=probe_latest.get("databaseId") if probe_latest else None)
    if probe_jobs_error and probe_latest:
        checks.append(_certification_gate("probe_jobs_lookup", "WARN", probe_jobs_error))
    checks.append(
        _classify_latest_run(
            name="startup_probe",
            latest=probe_latest,
            error=probe_error,
            head_sha=head_sha if head_ok else None,
            jobs_count=probe_jobs_count,
        )
    )

    owner = repo.split("/", 1)[0]
    billing_ok, billing_payload, billing_error = _gh_json(cfg, ["api", f"users/{owner}/settings/billing/actions"])
    billing: dict[str, Any] = {"available": billing_ok}
    if billing_ok and isinstance(billing_payload, dict):
        billing.update(billing_payload)
        checks.append(_certification_gate("actions_billing", "PASS", "billing endpoint readable"))
    else:
        billing["error"] = billing_error
        checks.append(_certification_gate("actions_billing", "WARN", billing_error or "billing endpoint unavailable"))
        next_actions.append("Run `gh auth refresh -h github.com -s user` and inspect GitHub Actions billing/minutes.")

    startup_blocker = _github_startup_blocker(
        ci_latest=ci_latest,
        ci_jobs_count=ci_jobs_count,
        probe_latest=probe_latest,
        probe_jobs_count=probe_jobs_count,
        branch_protection_error=branch_protection_error_for_blocker,
        billing_error=billing_error,
        self_hosted_runner_count=self_hosted_runner_count,
    )
    if (
        ci_latest
        and probe_latest
        and ci_latest.get("status") == "completed"
        and probe_latest.get("status") == "completed"
        and ci_latest.get("conclusion") == "startup_failure"
        and probe_latest.get("conclusion") == "startup_failure"
        and _run_failed_before_jobs(ci_latest, ci_jobs_count)
        and _run_failed_before_jobs(probe_latest, probe_jobs_count)
    ):
        checks.append(
            _certification_gate(
                "startup_layer_isolated",
                "FAIL",
                "CI and startup probe both failed before jobs; inspect account billing, spending limit, and runner availability",
            )
        )
        next_actions.append("Inspect repository Settings -> Actions -> General and Settings -> Actions -> Runners.")
    next_actions.extend(startup_blocker["next_actions"])

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "GITHUB_ACTIONS_DIAGNOSTIC_FAIL" if failed else "GITHUB_ACTIONS_DIAGNOSTIC_WARN" if warned else "GITHUB_ACTIONS_DIAGNOSTIC_OK"
    return {
        "status": status,
        "repo": repo,
        "branch": branch if branch_ok else None,
        "head_sha": head_sha if head_ok else None,
        "checks": checks,
        "branch_protection": branch_protection,
        "runner_inventory": runner_inventory,
        "startup_blocker": startup_blocker,
        "workflows": workflows,
        "runs": {
            "ci": _run_summary(ci_latest, ci_jobs_count),
            "startup_probe": _run_summary(probe_latest, probe_jobs_count),
        },
        "billing": billing,
        "next_actions": sorted(set(next_actions)),
    }


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


def _gate_by_name(gates: list[dict[str, str]], name: str) -> dict[str, str] | None:
    return next((gate for gate in gates if gate.get("name") == name), None)


def _requirement_status(audit: dict[str, Any], requirement_id: str) -> str | None:
    for row in audit.get("requirements", []):
        if isinstance(row, dict) and row.get("requirement_id") == requirement_id:
            status = row.get("status")
            return status if isinstance(status, str) else None
    return None


def _release_state(state: str, status: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "state": state,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
    }


def _receipt_state(receipt: dict[str, Any] | None, ok_status: str) -> tuple[str, str]:
    if receipt is None:
        return "UNVERIFIED", "No exact-HEAD receipt found."
    receipt_status = str(receipt.get("status"))
    evidence = f"receipt={receipt.get('created_utc')} status={receipt_status}"
    if receipt_status == ok_status:
        return "PASS", evidence
    return "FAIL", evidence


def build_release_state_report(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
) -> dict[str, Any]:
    audit = build_product_audit(cfg)
    package = build_package_status(cfg.project_root)
    certification = build_certification_status(
        cfg,
        include_github=include_github,
        github_workflow=github_workflow,
    )
    gates = certification.get("gates", [])
    gate_rows = [gate for gate in gates if isinstance(gate, dict)]

    git_repository = _gate_by_name(gate_rows, "git_repository")
    worktree_clean = _gate_by_name(gate_rows, "worktree_clean")
    upstream = _gate_by_name(gate_rows, "upstream")
    github_gate_names = {
        "github_branch",
        "github_branch_protection",
        "github_branch_protection_api",
        "github_ci_jobs_lookup",
        "github_hosted_runner_startup",
        "github_latest_ci",
        "github_tag_binding",
    }

    package_not_failed = package.get("status") != "PACKAGE_FAIL"
    head_sha = certification.get("git", {}).get("head_sha") if isinstance(certification.get("git"), dict) else None
    local_release_receipt = load_release_receipt(cfg.project_root, receipt_type="local_release", head_sha=head_sha)
    fresh_clone_receipt = load_release_receipt(cfg.project_root, receipt_type="fresh_clone", head_sha=head_sha)
    source_artifact_receipt = load_release_receipt(cfg.project_root, receipt_type="source_artifact", head_sha=head_sha)
    python_dist_receipt = load_release_receipt(cfg.project_root, receipt_type="python_dist", head_sha=head_sha)
    local_release_status, local_release_evidence = _receipt_state(local_release_receipt, "CERTIFY_RELEASE_OK")
    fresh_clone_status, fresh_clone_evidence = _receipt_state(fresh_clone_receipt, "FRESH_CLONE_CERT_OK")
    source_artifact_status, source_artifact_evidence = _receipt_state(source_artifact_receipt, "SOURCE_ARTIFACT_CERT_OK")
    python_dist_status, python_dist_evidence = _receipt_state(python_dist_receipt, "PYTHON_DIST_CERT_OK")
    canonical_local_pass = (
        git_repository is not None
        and git_repository.get("status") == "PASS"
        and worktree_clean is not None
        and worktree_clean.get("status") == "PASS"
        and upstream is not None
        and upstream.get("status") == "PASS"
        and package_not_failed
    )
    source_artifact_pass = _requirement_status(audit, "source_artifact_packaging") == "PASS"
    source_artifact_certified = source_artifact_status == "PASS"
    github_requirement_open = _requirement_status(audit, "github_release_governance") == "OPEN"
    github_gate_blockers = [gate for gate in gate_rows if gate.get("name") in github_gate_names and gate.get("status") != "PASS"]
    github_open = include_github and (github_requirement_open or bool(github_gate_blockers))
    source_artifact_open = include_github and not source_artifact_certified
    governed_ready = include_github and certification.get("status") == "CERTIFICATION_READY" and source_artifact_certified

    highest_local_state = "CANONICAL_LOCAL" if canonical_local_pass else "PROTOTYPE"
    if governed_ready:
        current_state = "GOVERNED_RELEASE_READY"
    elif github_open:
        current_state = "GITHUB_GOVERNANCE_OPEN"
    elif source_artifact_open:
        current_state = "SOURCE_ARTIFACT_CERTIFICATION_OPEN"
    else:
        current_state = highest_local_state

    states = [
        _release_state(
            "CANONICAL_LOCAL",
            "PASS" if canonical_local_pass else "OPEN",
            (
                "git repository, clean worktree, upstream, and package inputs are present"
                if canonical_local_pass
                else "one or more canonical local gates are missing or failing"
            ),
            "Repair local Git/package gates before release certification." if not canonical_local_pass else "Keep local canonical state clean.",
        ),
        _release_state(
            "LOCAL_RELEASE_CERTIFIED",
            local_release_status,
            local_release_evidence,
            "Run python3 scripts/certify_release.py on the exact current HEAD.",
        ),
        _release_state(
            "FRESH_CLONE_CERTIFIED",
            fresh_clone_status,
            fresh_clone_evidence,
            f"Run python3 scripts/certify_fresh_clone.py --ref {head_sha or 'main'}.",
        ),
        _release_state(
            "SOURCE_ARTIFACT_PACKAGING",
            "PASS" if source_artifact_pass and package_not_failed else "OPEN",
            str(package.get("status")),
            "Run mucaro package build --format text from a clean worktree.",
        ),
        _release_state(
            "SOURCE_ARTIFACT_CERTIFIED",
            source_artifact_status,
            source_artifact_evidence,
            "Run python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json.",
        ),
        _release_state(
            "PYTHON_DIST_CERTIFIED",
            python_dist_status,
            python_dist_evidence,
            "Run python3 scripts/certify_python_distribution.py.",
        ),
        _release_state(
            "GITHUB_GOVERNANCE_OPEN",
            "OPEN" if github_open else "UNVERIFIED" if not include_github else "PASS",
            (
                "; ".join(f"{gate.get('name')}={gate.get('status')}:{gate.get('detail')}" for gate in github_gate_blockers)
                if github_gate_blockers
                else "GitHub checks were not requested." if not include_github else "GitHub governance gates passed."
            ),
            "Run mucaro release state --include-github after hosted CI, branch protection, and tag binding are ready.",
        ),
        _release_state(
            "GOVERNED_RELEASE_READY",
            "PASS" if governed_ready else "OPEN",
            str(certification.get("status")),
            "Do not tag until mucaro certify status --include-github reports CERTIFICATION_READY.",
        ),
        _release_state(
            "PRODUCTION_READY",
            "OPEN",
            "Production readiness also needs model/provider hardening, larger-corpus retrieval evidence, packaging distribution, and sustained user workflow validation.",
            "Continue development beyond the governed local-first release baseline.",
        ),
    ]

    next_commands = [
        "python3 scripts/certify_release.py",
        f"python3 scripts/certify_fresh_clone.py --ref {certification.get('git', {}).get('head_sha') or 'main'}",
        "python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json",
        "python3 scripts/certify_python_distribution.py",
    ]
    if not include_github:
        next_commands.insert(0, "mucaro release state --include-github --format text")
    if github_open:
        next_commands.extend(
            [
                "mucaro certify github-actions --format text",
                "gh auth refresh -h github.com -s user",
            ]
        )

    return {
        "status": "RELEASE_STATE_READY" if governed_ready else "RELEASE_STATE_OPEN",
        "current_state": current_state,
        "highest_local_state": highest_local_state,
        "include_github": include_github,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "git": certification.get("git"),
        "receipts": {
            "local_release": local_release_receipt,
            "fresh_clone": fresh_clone_receipt,
            "source_artifact": source_artifact_receipt,
            "python_dist": python_dist_receipt,
        },
        "product_audit_status": audit.get("status"),
        "package_status": package.get("status"),
        "certification_status": certification.get("status"),
        "states": states,
        "next_commands": sorted(set(next_commands)),
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


def run_certify_github_actions(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_github_actions_diagnostic_status(
        cfg,
        workflow_name=args.workflow,
        probe_workflow_name=args.probe_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "GITHUB_ACTIONS_DIAGNOSTIC_FAIL":
        return 2
    if args.strict and report["status"] != "GITHUB_ACTIONS_DIAGNOSTIC_OK":
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
