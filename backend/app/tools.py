from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from .coding_mutation import CodingMutationError, apply_patch_transaction
from .coding_patch import validate_patch_proposal
from .coding_workspace import CodingWorkspaceError, inspect_repository, inspect_repository_diff, read_repository_file
from .config import Settings, settings
from .ingestion import get_chunks, get_index_status, get_sources
from .memory import list_memory_items
from .product_audit import build_product_audit
from .utils import read_json, stable_id, utc_now, write_json
from .web_browsing import (
    WebBrowsingError,
    build_web_browse_session_policy,
    build_web_fetch_policy,
    fetch_web_browse_session,
    fetch_web_snapshot,
)
from .workspaces import list_workspace_profiles

TOOL_CALLS_NAME = "tool_calls.json"
TOOL_AUDIT_NAME = "tool_audit_log.json"


class ToolRuntimeError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    mutates_state: bool
    requires_approval: bool
    handler: Callable[[dict[str, Any], Settings], dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mutates_state": self.mutates_state,
            "requires_approval": self.requires_approval,
        }


def _source_library_status(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del arguments
    sources = get_sources(cfg)
    chunks = get_chunks(cfg)
    return {
        "status": "SOURCE_LIBRARY_STATUS_OK",
        "source_count": len(sources),
        "chunk_count": len(chunks),
        "index": get_index_status(cfg),
    }


def _product_audit(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del arguments
    return build_product_audit(cfg)


def _workspace_profiles(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del arguments
    return {"status": "WORKSPACE_PROFILES_OK", "workspaces": list_workspace_profiles(cfg)}


def _memory_list(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    include_disabled = bool(arguments.get("include_disabled", True))
    return list_memory_items(cfg, include_disabled=include_disabled)


def _coding_repo_status(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.repo_status requires a non-empty repo_root argument.")
    try:
        return inspect_repository(repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_read_file(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    path = arguments.get("path")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.read_file requires a non-empty repo_root argument.")
    if not isinstance(path, str) or not path.strip():
        raise ToolRuntimeError("ERROR_CODING_FILE_PATH_REQUIRED", "coding.read_file requires a non-empty repository-relative path argument.")
    try:
        return read_repository_file(repo_root, path, cfg=cfg, max_bytes=arguments.get("max_bytes"))
    except CodingWorkspaceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_diff(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.diff requires a non-empty repo_root argument.")
    base = arguments.get("base", "HEAD")
    if not isinstance(base, str):
        raise ToolRuntimeError("ERROR_CODING_BASE_REF_INVALID", "coding.diff base must be a string.")
    try:
        return inspect_repository_diff(repo_root, cfg=cfg, base=base, max_bytes=arguments.get("max_bytes"))
    except CodingWorkspaceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_patch_check(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    patch = arguments.get("patch")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.patch_check requires a non-empty repo_root argument.")
    if not isinstance(patch, str) or not patch:
        raise ToolRuntimeError("ERROR_CODING_PATCH_REQUIRED", "coding.patch_check requires a non-empty patch string.")
    try:
        return validate_patch_proposal(repo_root, patch, cfg=cfg, max_bytes=arguments.get("max_bytes"))
    except CodingWorkspaceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_patch_apply(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    patch = arguments.get("patch")
    expected_head_sha = arguments.get("expected_head_sha")
    expected_patch_sha256 = arguments.get("expected_patch_sha256")
    expected_preimages = arguments.get("expected_preimages")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.patch_apply requires a non-empty repo_root argument.")
    if not isinstance(patch, str) or not patch:
        raise ToolRuntimeError("ERROR_CODING_PATCH_REQUIRED", "coding.patch_apply requires a non-empty patch string.")
    if not isinstance(expected_head_sha, str) or not expected_head_sha:
        raise ToolRuntimeError("ERROR_CODING_MUTATION_HEAD_REQUIRED", "coding.patch_apply requires expected_head_sha.")
    if not isinstance(expected_patch_sha256, str) or len(expected_patch_sha256) != 64:
        raise ToolRuntimeError("ERROR_CODING_MUTATION_PATCH_HASH_REQUIRED", "coding.patch_apply requires a 64-character expected_patch_sha256.")
    if not isinstance(expected_preimages, list) or not expected_preimages:
        raise ToolRuntimeError("ERROR_CODING_MUTATION_PREIMAGES_REQUIRED", "coding.patch_apply requires expected_preimages from the approved proposal receipt.")
    try:
        return apply_patch_transaction(
            repo_root,
            patch,
            expected_head_sha=expected_head_sha,
            expected_patch_sha256=expected_patch_sha256,
            expected_preimages=expected_preimages,
            cfg=cfg,
        )
    except CodingMutationError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _web_fetch(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    url = arguments.get("url")
    if not isinstance(url, str):
        raise ToolRuntimeError("ERROR_WEB_URL_REQUIRED", "web.fetch requires a string url argument.")
    try:
        return fetch_web_snapshot(url, cfg=cfg, policy=build_web_fetch_policy(arguments))
    except WebBrowsingError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _web_fetch_session(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    urls = arguments.get("urls")
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ToolRuntimeError("ERROR_WEB_SESSION_URLS_REQUIRED", "web.fetch_session requires a urls array of strings.")
    purpose = arguments.get("purpose")
    try:
        return fetch_web_browse_session(
            urls,
            purpose=str(purpose or ""),
            cfg=cfg,
            fetch_policy=build_web_fetch_policy(arguments),
            session_policy=build_web_browse_session_policy(arguments),
        )
    except WebBrowsingError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "source_library.status": ToolDefinition(
        name="source_library.status",
        description="Read local source, chunk, persistent-index, and semantic-store status.",
        mutates_state=False,
        requires_approval=True,
        handler=_source_library_status,
    ),
    "product.audit": ToolDefinition(
        name="product.audit",
        description="Run the local product-readiness audit.",
        mutates_state=False,
        requires_approval=True,
        handler=_product_audit,
    ),
    "workspace_profiles.list": ToolDefinition(
        name="workspace_profiles.list",
        description="List local workspace profiles.",
        mutates_state=False,
        requires_approval=True,
        handler=_workspace_profiles,
    ),
    "memory.list": ToolDefinition(
        name="memory.list",
        description="List explicit local memory items.",
        mutates_state=False,
        requires_approval=True,
        handler=_memory_list,
    ),
    "coding.repo_status": ToolDefinition(
        name="coding.repo_status",
        description="Inspect an approved local Git repository, exact HEAD, branch, and dirty-state candidate sets without mutation.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_repo_status,
    ),
    "coding.read_file": ToolDefinition(
        name="coding.read_file",
        description="Read one bounded repository-relative file with byte SHA-256 and explicit text/binary classification.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_read_file,
    ),
    "coding.diff": ToolDefinition(
        name="coding.diff",
        description="Inspect a bounded read-only Git diff and report staged, unstaged, and untracked residue explicitly.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_diff,
    ),
    "coding.patch_check": ToolDefinition(
        name="coding.patch_check",
        description="Validate a bounded unified-diff proposal with path, preimage, dirty-overlap, and git-apply checks without mutation.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_patch_check,
    ),
    "coding.patch_apply": ToolDefinition(
        name="coding.patch_apply",
        description="Apply only an explicitly approved patch after exact HEAD, patch-hash, candidate-set, preimage, and dirty-overlap revalidation.",
        mutates_state=True,
        requires_approval=True,
        handler=_coding_patch_apply,
    ),
    "web.fetch": ToolDefinition(
        name="web.fetch",
        description="Fetch one HTTP(S) URL through governed policy and save a byte-pinned local snapshot.",
        mutates_state=True,
        requires_approval=True,
        handler=_web_fetch,
    ),
    "web.fetch_session": ToolDefinition(
        name="web.fetch_session",
        description="Fetch an approved operator-supplied URL set and save a replayable multi-page browsing receipt.",
        mutates_state=True,
        requires_approval=True,
        handler=_web_fetch_session,
    ),
}


def _tool_calls_path(cfg: Settings) -> Any:
    return cfg.processed_dir / TOOL_CALLS_NAME


def _tool_audit_path(cfg: Settings) -> Any:
    return cfg.processed_dir / TOOL_AUDIT_NAME


def _load_tool_calls(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_tool_calls_path(cfg), [])


def _write_tool_calls(calls: list[dict[str, Any]], cfg: Settings) -> None:
    write_json(_tool_calls_path(cfg), calls)


def _load_audit_events(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_tool_audit_path(cfg), [])


def _append_audit_event(event: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    events = _load_audit_events(cfg)
    payload = {
        "event_id": stable_id("toolevent", utc_now(), event.get("tool_call_id", ""), event.get("event", "")),
        "created_at": utc_now(),
        **event,
    }
    events.append(payload)
    write_json(_tool_audit_path(cfg), events)
    return payload


def _find_tool_call(tool_call_id: str, calls: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    for index, call in enumerate(calls):
        if call.get("tool_call_id") == tool_call_id:
            return index, call
    raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_FOUND", f"Tool call not found: {tool_call_id}")


def list_tools() -> dict[str, Any]:
    return {"status": "TOOLS_OK", "tools": [tool.as_dict() for tool in TOOL_REGISTRY.values()]}


def list_tool_calls(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "TOOL_CALLS_OK", "tool_calls": _load_tool_calls(cfg)}


def list_tool_audit_events(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "TOOL_AUDIT_OK", "events": _load_audit_events(cfg)}


def request_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    cfg: Settings = settings,
    requested_by: str = "operator",
    reason: str | None = None,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ToolRuntimeError("ERROR_TOOL_ARGUMENTS_NOT_OBJECT", "Tool arguments must be a JSON object.")
    if tool_name not in TOOL_REGISTRY:
        _append_audit_event(
            {
                "event": "TOOL_REQUEST_REJECTED",
                "tool_name": tool_name,
                "status": "ERROR_TOOL_NOT_ALLOWED",
                "actor": requested_by,
                "reason": reason,
            },
            cfg,
        )
        raise ToolRuntimeError("ERROR_TOOL_NOT_ALLOWED", f"Tool is not in the local allowlist: {tool_name}")

    created_at = utc_now()
    tool_call_id = stable_id("toolcall", tool_name, json.dumps(arguments, sort_keys=True), created_at)
    call = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "status": "PENDING_APPROVAL",
        "requested_by": requested_by,
        "reason": reason,
        "created_at": created_at,
        "approved_by": None,
        "approved_at": None,
        "denied_by": None,
        "denied_at": None,
        "executed_at": None,
        "result": None,
    }
    calls = _load_tool_calls(cfg)
    calls.append(call)
    _write_tool_calls(calls, cfg)
    _append_audit_event(
        {
            "event": "TOOL_CALL_REQUESTED",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "PENDING_APPROVAL",
            "actor": requested_by,
            "reason": reason,
        },
        cfg,
    )
    return {"status": "TOOL_CALL_REQUESTED", "tool_call": call}


def approve_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    approved_by: str = "operator",
    reason: str | None = None,
) -> dict[str, Any]:
    calls = _load_tool_calls(cfg)
    index, call = _find_tool_call(tool_call_id, calls)
    if call.get("status") != "PENDING_APPROVAL":
        raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_PENDING", f"Tool call is not pending approval: {tool_call_id}")
    call = {**call, "status": "APPROVED", "approved_by": approved_by, "approved_at": utc_now()}
    calls[index] = call
    _write_tool_calls(calls, cfg)
    _append_audit_event(
        {
            "event": "TOOL_CALL_APPROVED",
            "tool_call_id": tool_call_id,
            "tool_name": call.get("tool_name"),
            "status": "APPROVED",
            "actor": approved_by,
            "reason": reason,
        },
        cfg,
    )
    return {"status": "TOOL_CALL_APPROVED", "tool_call": call}


def deny_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    denied_by: str = "operator",
    reason: str | None = None,
) -> dict[str, Any]:
    calls = _load_tool_calls(cfg)
    index, call = _find_tool_call(tool_call_id, calls)
    if call.get("status") != "PENDING_APPROVAL":
        raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_PENDING", f"Tool call is not pending approval: {tool_call_id}")
    call = {**call, "status": "DENIED", "denied_by": denied_by, "denied_at": utc_now()}
    calls[index] = call
    _write_tool_calls(calls, cfg)
    _append_audit_event(
        {
            "event": "TOOL_CALL_DENIED",
            "tool_call_id": tool_call_id,
            "tool_name": call.get("tool_name"),
            "status": "DENIED",
            "actor": denied_by,
            "reason": reason,
        },
        cfg,
    )
    return {"status": "TOOL_CALL_DENIED", "tool_call": call}


def run_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    executed_by: str = "operator",
) -> dict[str, Any]:
    calls = _load_tool_calls(cfg)
    index, call = _find_tool_call(tool_call_id, calls)
    if call.get("status") == "PENDING_APPROVAL":
        raise ToolRuntimeError("ERROR_TOOL_APPROVAL_REQUIRED", f"Approve tool call before execution: {tool_call_id}")
    if call.get("status") == "DENIED":
        raise ToolRuntimeError("ERROR_TOOL_CALL_DENIED", f"Denied tool call cannot execute: {tool_call_id}")
    if call.get("status") == "EXECUTED":
        raise ToolRuntimeError("ERROR_TOOL_CALL_ALREADY_EXECUTED", f"Tool call already executed: {tool_call_id}")
    if call.get("status") != "APPROVED":
        raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_APPROVED", f"Tool call is not approved: {tool_call_id}")
    tool_name = str(call.get("tool_name", ""))
    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        raise ToolRuntimeError("ERROR_TOOL_NOT_ALLOWED", f"Tool is not in the local allowlist: {tool_name}")

    try:
        result = tool.handler(dict(call.get("arguments") or {}), cfg)
    except ToolRuntimeError as exc:
        result = {"status": exc.status, "detail": exc.detail}
        call = {**call, "status": "FAILED", "executed_at": utc_now(), "executed_by": executed_by, "result": result}
        calls[index] = call
        _write_tool_calls(calls, cfg)
        _append_audit_event(
            {
                "event": "TOOL_CALL_FAILED",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": exc.status,
                "actor": executed_by,
                "result_status": exc.status,
            },
            cfg,
        )
        raise
    call = {**call, "status": "EXECUTED", "executed_at": utc_now(), "executed_by": executed_by, "result": result}
    calls[index] = call
    _write_tool_calls(calls, cfg)
    _append_audit_event(
        {
            "event": "TOOL_CALL_EXECUTED",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "EXECUTED",
            "actor": executed_by,
            "result_status": result.get("status"),
        },
        cfg,
    )
    return {"status": "TOOL_CALL_EXECUTED", "tool_call": call, "result": result}
