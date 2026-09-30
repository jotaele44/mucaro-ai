from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_mutation import CodingMutationError, apply_patch_transaction
from .coding_patch import validate_patch_proposal
from .coding_workspace import CodingWorkspaceError, inspect_repository, inspect_repository_diff, read_repository_file
from .config import Settings, settings
from .contracts import validate_contract
from .gis_processing import GISProcessingError, inspect_geojson
from .ingestion import get_chunks, get_index_status, get_sources
from .memory import list_memory_items
from .ocr_analysis import OCRAnalysisError, analyze_image_ocr
from .permission_engine import PermissionEngineError, PermissionRequest, require as require_permission
from .product_audit import build_product_audit
from .utils import canonical_sha256, read_json, stable_id, update_json, utc_now
from .web_browsing import (
    WebBrowsingError,
    build_web_browse_session_policy,
    build_web_fetch_policy,
    fetch_web_browse_session,
    fetch_web_snapshot,
)
from .web_render import WebRenderError, render_web_page
from .web_search import WebSearchError, search_web_brave
from .workspaces import list_workspace_profiles

TOOL_CALLS_NAME = "tool_calls.json"
TOOL_AUDIT_NAME = "tool_audit_log.json"
TOOL_CALL_STATUSES = ("PENDING_APPROVAL", "APPROVED", "DENIED", "CANCELLED", "RUNNING", "EXECUTED", "FAILED", "INTERRUPTED")
# Wall-clock bound for one in-process handler (MATRIX-011 TIMEOUT). Remote tools also carry their own
# network timeouts; this bound covers everything else (slow disks, huge repositories, stuck parsers).
DEFAULT_TOOL_TIMEOUT_SECONDS = 120.0


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
    # "none": local only; "remote": contacts an external service (TGT-019 offline classification).
    network: str = "none"
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mutates_state": self.mutates_state,
            "requires_approval": self.requires_approval,
            "network": self.network,
            "timeout_seconds": self.timeout_seconds,
            "offline_availability": "AVAILABLE_LOCALLY" if self.network == "none" else "REQUIRES_REMOTE_SERVICE",
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


def _resolve_tool_input_path(value: str, cfg: Settings) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (cfg.project_root / path).resolve()


def _require_tool_filesystem_read(path: Path, cfg: Settings, *, subject: str) -> None:
    try:
        require_permission(
            PermissionRequest(
                capability="filesystem.read",
                subject=subject,
                target=str(path),
                origin="operator",
            ),
            cfg=cfg,
        )
    except PermissionEngineError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _gis_inspect(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    value = arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ToolRuntimeError("ERROR_GIS_TOOL_PATH_REQUIRED", "gis.inspect requires a non-empty path argument.")
    path = _resolve_tool_input_path(value, cfg)
    _require_tool_filesystem_read(path, cfg, subject="tools.gis.inspect")
    try:
        return inspect_geojson(path, cfg=cfg)
    except GISProcessingError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _ocr_analyze_image(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    value = arguments.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ToolRuntimeError("ERROR_OCR_TOOL_PATH_REQUIRED", "ocr.analyze_image requires a non-empty path argument.")
    path = _resolve_tool_input_path(value, cfg)
    _require_tool_filesystem_read(path, cfg, subject="tools.ocr.analyze_image")
    language = arguments.get("language", "eng")
    layout_mode = arguments.get("layout_mode", "auto")
    timeout_seconds = arguments.get("timeout_seconds", 30)
    if not isinstance(language, str):
        raise ToolRuntimeError("ERROR_OCR_LANGUAGE_INVALID", "OCR language must be a string.")
    if not isinstance(layout_mode, str):
        raise ToolRuntimeError("ERROR_OCR_LAYOUT_INVALID", "OCR layout_mode must be a string.")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0 or timeout_seconds > 120:
        raise ToolRuntimeError("ERROR_OCR_TIMEOUT_INVALID", "OCR timeout_seconds must be an integer from 1 through 120.")
    try:
        return analyze_image_ocr(
            path,
            cfg=cfg,
            language=language,
            layout_mode=layout_mode,
            timeout_seconds=timeout_seconds,
        )
    except OCRAnalysisError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


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


def _web_render(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ToolRuntimeError("ERROR_WEB_RENDER_URL_REQUIRED", "web.render requires a non-empty url string.")
    try:
        return render_web_page(
            url,
            cfg=cfg,
            allow_rendered_browser=arguments.get("allow_rendered_browser") is True,
            timeout_ms=arguments.get("timeout_ms", 20_000),
            max_dom_bytes=arguments.get("max_dom_bytes", 2_000_000),
        )
    except WebRenderError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _web_search(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolRuntimeError("ERROR_WEB_SEARCH_QUERY_REQUIRED", "web.search requires a non-empty query string.")
    try:
        return search_web_brave(
            query,
            cfg=cfg,
            count=arguments.get("count", 10),
            offset=arguments.get("offset", 0),
            country=arguments.get("country", "US"),
            search_lang=arguments.get("search_lang", "en"),
            safesearch=arguments.get("safesearch", "moderate"),
            freshness=arguments.get("freshness"),
            allow_live_search=arguments.get("allow_live_search") is True,
        )
    except WebSearchError as exc:
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
    "gis.inspect": ToolDefinition(
        name="gis.inspect",
        description="Inspect one explicitly approved local GeoJSON FeatureCollection and emit a provenance-bound geometry/topology report without transforming the source.",
        mutates_state=True,
        requires_approval=True,
        handler=_gis_inspect,
    ),
    "ocr.analyze_image": ToolDefinition(
        name="ocr.analyze_image",
        description="Run explicitly approved local image OCR through the governed filesystem and subprocess permission boundaries and emit a provenance-bound OCR report.",
        mutates_state=True,
        requires_approval=True,
        handler=_ocr_analyze_image,
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
    "web.render": ToolDefinition(
        name="web.render",
        description="Render one explicitly approved public HTTP(S) page in an optional headless browser and save DOM/screenshot provenance; rendered output is not citation-ready by default.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_render,
    ),
    "web.search": ToolDefinition(
        name="web.search",
        description="Run an explicitly approved live web search and save a credential-free search receipt; result snippets are discovery evidence, not cited page evidence.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_search,
    ),
    "web.fetch": ToolDefinition(
        name="web.fetch",
        description="Fetch one HTTP(S) URL through governed policy and save a byte-pinned local snapshot.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_fetch,
    ),
    "web.fetch_session": ToolDefinition(
        name="web.fetch_session",
        description="Fetch an approved operator-supplied URL set and save a replayable multi-page browsing receipt.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_fetch_session,
    ),
}


def _tool_calls_path(cfg: Settings) -> Any:
    return cfg.processed_dir / TOOL_CALLS_NAME


def _tool_audit_path(cfg: Settings) -> Any:
    return cfg.processed_dir / TOOL_AUDIT_NAME


def _load_tool_calls(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_tool_calls_path(cfg), [])


def _load_audit_events(cfg: Settings) -> list[dict[str, Any]]:
    return read_json(_tool_audit_path(cfg), [])


def arguments_digest(arguments: dict[str, Any]) -> str:
    return canonical_sha256(arguments)


def _append_audit_event(event: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Append a hash-chained audit event under an exclusive lock."""
    cfg.ensure_dirs()

    def append(events: list[dict[str, Any]]) -> dict[str, Any]:
        previous = next((row.get("event_sha256") for row in reversed(events) if row.get("event_sha256")), None)
        created_at = utc_now()
        payload = {
            "event_id": stable_id("toolevent", created_at, event.get("tool_call_id", ""), event.get("event", ""), str(len(events))),
            "created_at": created_at,
            "tool_name": event.get("tool_name") or "",
            **event,
            "previous_event_sha256": previous,
        }
        payload["event_sha256"] = canonical_sha256(payload)
        validate_contract("tool_audit_event", payload)
        events.append(payload)
        return payload

    return update_json(_tool_audit_path(cfg), [], append)


def verify_tool_audit_chain(cfg: Settings = settings) -> dict[str, Any]:
    """Detect edited, removed or reordered audit events (TGT-035)."""
    events = _load_audit_events(cfg)
    previous: str | None = None
    chained = 0
    for index, event in enumerate(events):
        digest = event.get("event_sha256")
        if not digest:
            if chained:
                return {"status": "TOOL_AUDIT_CHAIN_BROKEN", "index": index, "reason": "UNHASHED_EVENT_AFTER_CHAIN_START"}
            continue
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        if canonical_sha256(body) != digest:
            return {"status": "TOOL_AUDIT_CHAIN_BROKEN", "index": index, "reason": "EVENT_HASH_MISMATCH"}
        if event.get("previous_event_sha256") != previous:
            return {"status": "TOOL_AUDIT_CHAIN_BROKEN", "index": index, "reason": "PREVIOUS_HASH_MISMATCH"}
        previous = digest
        chained += 1
    return {"status": "TOOL_AUDIT_CHAIN_OK", "event_count": len(events), "chained_event_count": chained}


def _find_tool_call(tool_call_id: str, calls: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    for index, call in enumerate(calls):
        if call.get("tool_call_id") == tool_call_id:
            return index, call
    raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_FOUND", f"Tool call not found: {tool_call_id}")


def _transition(
    tool_call_id: str,
    cfg: Settings,
    change: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Atomically read, check and update one call; change() raises to refuse the transition."""

    def mutate(calls: list[dict[str, Any]]) -> dict[str, Any]:
        index, call = _find_tool_call(tool_call_id, calls)
        updated = validate_contract("tool_call", change(dict(call)))
        calls[index] = updated
        return updated

    return update_json(_tool_calls_path(cfg), [], mutate)


def list_tools() -> dict[str, Any]:
    return {"status": "TOOLS_OK", "tools": [tool.as_dict() for tool in TOOL_REGISTRY.values()]}


def list_tool_calls(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "TOOL_CALLS_OK", "tool_calls": _load_tool_calls(cfg)}


def list_tool_audit_events(cfg: Settings = settings) -> dict[str, Any]:
    return {"status": "TOOL_AUDIT_OK", "events": _load_audit_events(cfg)}


def summarize_tool_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts from call records; every call lands in exactly one bucket (F08)."""
    buckets = {status: 0 for status in TOOL_CALL_STATUSES}
    for call in calls:
        buckets[str(call.get("status"))] = buckets.get(str(call.get("status")), 0) + 1
    total = len(calls)
    return {"total": total, "by_status": buckets, "arithmetic_closes": sum(buckets.values()) == total}


def request_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    cfg: Settings = settings,
    requested_by: str = "operator",
    reason: str | None = None,
    run_id: str | None = None,
    retry_of: str | None = None,
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
    digest = arguments_digest(arguments)
    tool_call_id = stable_id("toolcall", tool_name, digest, created_at)
    call = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "arguments": arguments,
        "arguments_sha256": digest,
        "status": "PENDING_APPROVAL",
        "requested_by": requested_by,
        "reason": reason,
        "run_id": run_id,
        "retry_of": retry_of,
        "created_at": created_at,
        "approved_by": None,
        "approved_at": None,
        "denied_by": None,
        "denied_at": None,
        "executed_at": None,
        "result": None,
    }
    validate_contract("tool_call", call)
    update_json(_tool_calls_path(cfg), [], lambda calls: calls.append(call))
    _append_audit_event(
        {
            "event": "TOOL_CALL_REQUESTED",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": "PENDING_APPROVAL",
            "actor": requested_by,
            "reason": reason,
            "arguments_sha256": digest,
        },
        cfg,
    )
    return {"status": "TOOL_CALL_REQUESTED", "tool_call": call}


def _require_pending(call: dict[str, Any], expected_arguments_sha256: str | None) -> None:
    if call.get("status") != "PENDING_APPROVAL":
        raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_PENDING", f"Tool call is not pending approval: {call.get('tool_call_id')}")
    # A decision binds to the exact arguments the decider saw (F07); a stale view is refused.
    if expected_arguments_sha256 is not None and expected_arguments_sha256 != call.get("arguments_sha256"):
        raise ToolRuntimeError(
            "ERROR_TOOL_DECISION_STALE",
            f"Decision was made against different arguments than tool call {call.get('tool_call_id')} holds.",
        )


def approve_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    approved_by: str = "operator",
    reason: str | None = None,
    expected_arguments_sha256: str | None = None,
) -> dict[str, Any]:
    def change(call: dict[str, Any]) -> dict[str, Any]:
        _require_pending(call, expected_arguments_sha256)
        return {**call, "status": "APPROVED", "approved_by": approved_by, "approved_at": utc_now()}

    call = _transition(tool_call_id, cfg, change)
    _append_audit_event(
        {
            "event": "TOOL_CALL_APPROVED",
            "tool_call_id": tool_call_id,
            "tool_name": call.get("tool_name"),
            "status": "APPROVED",
            "actor": approved_by,
            "reason": reason,
            "arguments_sha256": call.get("arguments_sha256"),
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
    expected_arguments_sha256: str | None = None,
) -> dict[str, Any]:
    def change(call: dict[str, Any]) -> dict[str, Any]:
        _require_pending(call, expected_arguments_sha256)
        return {**call, "status": "DENIED", "denied_by": denied_by, "denied_at": utc_now()}

    call = _transition(tool_call_id, cfg, change)
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


def cancel_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    cancelled_by: str = "operator",
    reason: str | None = None,
) -> dict[str, Any]:
    """Cancel a call that has not started. A RUNNING in-process handler cannot be interrupted safely."""

    def change(call: dict[str, Any]) -> dict[str, Any]:
        status = call.get("status")
        if status == "RUNNING":
            raise ToolRuntimeError("ERROR_TOOL_CALL_RUNNING_NOT_CANCELLABLE", f"Tool call is already running: {tool_call_id}")
        if status not in {"PENDING_APPROVAL", "APPROVED"}:
            raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_CANCELLABLE", f"Tool call is {status}: {tool_call_id}")
        return {**call, "status": "CANCELLED", "cancelled_by": cancelled_by, "cancelled_at": utc_now()}

    call = _transition(tool_call_id, cfg, change)
    _append_audit_event(
        {
            "event": "TOOL_CALL_CANCELLED",
            "tool_call_id": tool_call_id,
            "tool_name": call.get("tool_name"),
            "status": "CANCELLED",
            "actor": cancelled_by,
            "reason": reason,
        },
        cfg,
    )
    return {"status": "TOOL_CALL_CANCELLED", "tool_call": call}


def reconcile_interrupted_tool_calls(cfg: Settings = settings) -> dict[str, Any]:
    """After a restart, a call still RUNNING was interrupted mid-execution.

    Its side effects are unknown, so it is recorded as INTERRUPTED and never
    retried implicitly; a retry needs a new request and a new approval.
    """
    if not _tool_calls_path(cfg).exists():
        return {"status": "TOOL_RECONCILE_OK", "interrupted": []}

    def mutate(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        interrupted = []
        for index, call in enumerate(calls):
            if call.get("status") == "RUNNING":
                calls[index] = {
                    **call,
                    "status": "INTERRUPTED",
                    "interrupted_at": utc_now(),
                    "result": {"status": "ERROR_TOOL_CALL_INTERRUPTED", "detail": "Process ended while the call was running; side effects unknown."},
                }
                interrupted.append(calls[index])
        return interrupted

    interrupted = update_json(_tool_calls_path(cfg), [], mutate)
    for call in interrupted:
        _append_audit_event(
            {
                "event": "TOOL_CALL_INTERRUPTED",
                "tool_call_id": call["tool_call_id"],
                "tool_name": call.get("tool_name"),
                "status": "INTERRUPTED",
                "actor": "system",
                "reason": "startup reconciliation",
            },
            cfg,
        )
    return {"status": "TOOL_RECONCILE_OK", "interrupted": [call["tool_call_id"] for call in interrupted]}


def run_tool_call(
    tool_call_id: str,
    *,
    cfg: Settings = settings,
    executed_by: str = "operator",
) -> dict[str, Any]:
    def claim(call: dict[str, Any]) -> dict[str, Any]:
        status = call.get("status")
        if status == "PENDING_APPROVAL":
            raise ToolRuntimeError("ERROR_TOOL_APPROVAL_REQUIRED", f"Approve tool call before execution: {tool_call_id}")
        if status == "DENIED":
            raise ToolRuntimeError("ERROR_TOOL_CALL_DENIED", f"Denied tool call cannot execute: {tool_call_id}")
        if status == "EXECUTED":
            raise ToolRuntimeError("ERROR_TOOL_CALL_ALREADY_EXECUTED", f"Tool call already executed: {tool_call_id}")
        if status == "RUNNING":
            raise ToolRuntimeError("ERROR_TOOL_CALL_ALREADY_RUNNING", f"Tool call is already running: {tool_call_id}")
        if status != "APPROVED":
            raise ToolRuntimeError("ERROR_TOOL_CALL_NOT_APPROVED", f"Tool call is not approved: {tool_call_id}")
        if call.get("tool_name") not in TOOL_REGISTRY:
            raise ToolRuntimeError("ERROR_TOOL_NOT_ALLOWED", f"Tool is not in the local allowlist: {call.get('tool_name')}")
        return {**call, "status": "RUNNING", "started_at": utc_now(), "executed_by": executed_by}

    # The APPROVED -> RUNNING claim is atomic, so a duplicate invocation cannot execute twice.
    call = _transition(tool_call_id, cfg, claim)
    tool_name = str(call.get("tool_name", ""))
    tool = TOOL_REGISTRY[tool_name]
    _append_audit_event(
        {"event": "TOOL_CALL_STARTED", "tool_call_id": tool_call_id, "tool_name": tool_name, "status": "RUNNING", "actor": executed_by},
        cfg,
    )
    failure: ToolRuntimeError | None = None
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["result"] = tool.handler(dict(call.get("arguments") or {}), cfg)
        except BaseException as exc:  # noqa: BLE001 - reported to the caller below
            outcome["error"] = exc

    # The handler runs in a worker so a stuck handler cannot hold the call RUNNING forever.
    worker = threading.Thread(target=invoke, name=f"tool-{tool_call_id}", daemon=True)
    worker.start()
    worker.join(tool.timeout_seconds)
    if worker.is_alive():
        # Python cannot kill the thread; its eventual return value is discarded and never recorded as EXECUTED.
        failure = ToolRuntimeError("ERROR_TOOL_TIMEOUT", f"{tool_name} exceeded {tool.timeout_seconds:g}s")
        result = {
            "status": failure.status,
            "detail": failure.detail,
            "timeout_seconds": tool.timeout_seconds,
            "handler_abandoned": True,
            # A mutating handler may still finish its change after the timeout; say so instead of guessing.
            "side_effects": "UNKNOWN" if tool.mutates_state else "NONE_EXPECTED",
        }
    elif isinstance(outcome.get("error"), ToolRuntimeError):
        failure = outcome["error"]
        result = {"status": failure.status, "detail": failure.detail}
    elif "error" in outcome:
        exc = outcome["error"]
        failure = ToolRuntimeError("ERROR_TOOL_INTERNAL_FAILURE", f"{type(exc).__name__}: {exc}")
        result = {"status": failure.status, "detail": failure.detail}
    else:
        result = outcome["result"]
    final_status = "FAILED" if failure else "EXECUTED"
    call = _transition(
        tool_call_id,
        cfg,
        lambda current: {**current, "status": final_status, "executed_at": utc_now(), "executed_by": executed_by, "result": result},
    )
    _append_audit_event(
        {
            "event": "TOOL_CALL_FAILED" if failure else "TOOL_CALL_EXECUTED",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": failure.status if failure else "EXECUTED",
            "actor": executed_by,
            "result_status": result.get("status"),
        },
        cfg,
    )
    if failure:
        raise failure
    return {"status": "TOOL_CALL_EXECUTED", "tool_call": call, "result": result}
