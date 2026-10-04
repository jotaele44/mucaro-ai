from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .coding_container import CodingContainerError, build_coding_container_plan, run_coding_container_plan
from .coding_lifecycle import CodingLifecycleError, build_pull_request_request, commit_approved_changes, create_work_branch_transaction
from .coding_checks import CodingCheck, CodingCheckError, execute_coding_check_suite
from .coding_mutation import CodingMutationError, apply_patch_transaction
from .coding_patch import validate_patch_proposal
from .coding_workspace import CodingWorkspaceError, inspect_repository, inspect_repository_diff, read_repository_file
from .config import Settings, settings
from .contracts import validate_contract
from .github_ci import GitHubCIError, read_github_ci_status
from .github_pull_requests import GitHubPullRequestError, open_or_update_pull_request
from .github_resources import (
    GitHubResourceError,
    get_github_gist,
    get_github_issue,
    get_github_pull_request,
    list_github_gists,
    list_github_issues,
    list_github_pull_requests,
)
from .ingestion import get_chunks, get_index_status, get_sources
from .memory import list_memory_items
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
    # Autopilot may mark a task PASSED only from this tool-owned result contract.
    # Empty statuses means the tool is not yet eligible for autonomous terminalization.
    autopilot_success_statuses: tuple[str, ...] = ()
    autopilot_success_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def autopilot_result_is_success(self, result: dict[str, Any]) -> bool | None:
        if not self.autopilot_success_statuses:
            return None
        if result.get("status") not in self.autopilot_success_statuses:
            return False
        for field, allowed in self.autopilot_success_fields:
            if str(result.get(field)) not in allowed:
                return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "mutates_state": self.mutates_state,
            "requires_approval": self.requires_approval,
            "network": self.network,
            "timeout_seconds": self.timeout_seconds,
            "offline_availability": "AVAILABLE_LOCALLY" if self.network == "none" else "REQUIRES_REMOTE_SERVICE",
            "autopilot_success_contract": (
                {
                    "statuses": list(self.autopilot_success_statuses),
                    "required_fields": {field: list(allowed) for field, allowed in self.autopilot_success_fields},
                }
                if self.autopilot_success_statuses
                else None
            ),
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



def _coding_exec(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    image = arguments.get("image")
    command = arguments.get("command")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.exec requires a non-empty repo_root argument.")
    if not isinstance(image, str) or not image.strip():
        raise ToolRuntimeError("ERROR_CODING_EXEC_IMAGE_REQUIRED", "coding.exec requires an immutable pinned image identity.")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise ToolRuntimeError("ERROR_CODING_EXEC_COMMAND_REQUIRED", "coding.exec requires a non-empty argv array of strings.")
    if arguments.get("network_allowed") not in (None, False):
        raise ToolRuntimeError("ERROR_CODING_EXEC_NETWORK_POLICY", "coding.exec does not permit network access.")

    bounded_limits = {
        "timeout_seconds": (1, 120),
        "max_output_bytes": (1, 2 * 1024 * 1024),
        "memory_mb": (128, 4096),
        "cpus": (0.25, 4.0),
        "pids_limit": (16, 512),
        "workspace_mb": (64, 4096),
    }
    options: dict[str, Any] = {}
    for key, (minimum, maximum) in bounded_limits.items():
        if key not in arguments:
            continue
        value = arguments[key]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < minimum
            or value > maximum
            or (key != "cpus" and not isinstance(value, int))
        ):
            raise ToolRuntimeError(
                "ERROR_CODING_EXEC_RESOURCE_LIMIT",
                f"coding.exec {key} must be within {minimum}..{maximum}.",
            )
        options[key] = value
    if "engine" in arguments:
        engine = arguments["engine"]
        if not isinstance(engine, str) or engine not in {"docker", "podman"}:
            raise ToolRuntimeError("ERROR_CODING_EXEC_ENGINE", "coding.exec engine must be docker or podman.")
        options["engine"] = engine
    try:
        plan = build_coding_container_plan(
            repo_root,
            image=image,
            command=command,
            cfg=cfg,
            network_allowed=False,
            **options,
        )
        receipt = run_coding_container_plan(plan, cfg=cfg)
    except CodingContainerError as exc:
        detail = exc.detail
        if exc.receipt is not None:
            detail = f"{detail}; receipt_status={exc.receipt.get('status')}"
        raise ToolRuntimeError(exc.status, detail) from exc

    execution_status = str(receipt.get("execution_status") or "UNKNOWN")
    return {
        "status": "CODING_EXEC_OK" if execution_status == "PASS" else "CODING_EXEC_COMMAND_FAILED",
        "execution_status": execution_status,
        "plan_sha256": receipt.get("plan_sha256"),
        "head_sha": receipt.get("head_sha"),
        "command_argv": receipt.get("command_argv"),
        "exit_code": receipt.get("exit_code"),
        "duration_ms": receipt.get("duration_ms"),
        "stdout": receipt.get("stdout"),
        "stderr": receipt.get("stderr"),
        "source_mount_read_only": receipt.get("source_mount_read_only"),
        "workspace_ephemeral": receipt.get("workspace_ephemeral"),
        "network_allowed": receipt.get("network_allowed"),
        "container_receipt_sha256": receipt.get("receipt_sha256"),
        "container_receipt": receipt,
    }



def _coding_branch_create(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    branch_name = arguments.get("branch_name")
    expected_head_sha = arguments.get("expected_head_sha")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.branch_create requires a non-empty repo_root argument.")
    if not isinstance(branch_name, str) or not branch_name.strip():
        raise ToolRuntimeError("ERROR_CODING_BRANCH_REQUIRED", "coding.branch_create requires branch_name.")
    if not isinstance(expected_head_sha, str) or not expected_head_sha:
        raise ToolRuntimeError("ERROR_CODING_BRANCH_HEAD_REQUIRED", "coding.branch_create requires expected_head_sha.")
    try:
        return create_work_branch_transaction(
            repo_root,
            branch_name=branch_name,
            expected_head_sha=expected_head_sha,
            cfg=cfg,
        )
    except CodingLifecycleError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_commit(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    expected_head_sha = arguments.get("expected_head_sha")
    expected_branch = arguments.get("expected_branch")
    approved_paths = arguments.get("approved_paths")
    message = arguments.get("message")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.commit requires a non-empty repo_root argument.")
    if not isinstance(expected_head_sha, str) or not expected_head_sha:
        raise ToolRuntimeError("ERROR_CODING_COMMIT_HEAD_REQUIRED", "coding.commit requires expected_head_sha.")
    if not isinstance(expected_branch, str) or not expected_branch.strip():
        raise ToolRuntimeError("ERROR_CODING_COMMIT_BRANCH_REQUIRED", "coding.commit requires expected_branch.")
    if not isinstance(approved_paths, list) or not approved_paths:
        raise ToolRuntimeError("ERROR_CODING_COMMIT_PATHS_REQUIRED", "coding.commit requires approved_paths.")
    if not isinstance(message, str) or not message.strip():
        raise ToolRuntimeError("ERROR_CODING_COMMIT_MESSAGE_REQUIRED", "coding.commit requires a non-empty message.")
    try:
        return commit_approved_changes(
            repo_root,
            expected_head_sha=expected_head_sha,
            expected_branch=expected_branch,
            approved_paths=approved_paths,
            message=message,
            cfg=cfg,
        )
    except CodingLifecycleError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _coding_pr_request(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    expected_head_sha = arguments.get("expected_head_sha")
    head_branch = arguments.get("head_branch")
    base_branch = arguments.get("base_branch")
    title = arguments.get("title")
    body = arguments.get("body", "")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.pr_request requires a non-empty repo_root argument.")
    if not isinstance(expected_head_sha, str) or not expected_head_sha:
        raise ToolRuntimeError("ERROR_CODING_PR_HEAD_REQUIRED", "coding.pr_request requires expected_head_sha.")
    if not isinstance(head_branch, str) or not head_branch.strip():
        raise ToolRuntimeError("ERROR_CODING_PR_HEAD_BRANCH_REQUIRED", "coding.pr_request requires head_branch.")
    if not isinstance(base_branch, str) or not base_branch.strip():
        raise ToolRuntimeError("ERROR_CODING_PR_BASE_BRANCH_REQUIRED", "coding.pr_request requires base_branch.")
    if not isinstance(title, str) or not title.strip():
        raise ToolRuntimeError("ERROR_CODING_PR_TITLE_REQUIRED", "coding.pr_request requires title.")
    if not isinstance(body, str):
        raise ToolRuntimeError("ERROR_CODING_PR_BODY_INVALID", "coding.pr_request body must be a string.")
    try:
        return build_pull_request_request(
            repo_root,
            expected_head_sha=expected_head_sha,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
            cfg=cfg,
        )
    except CodingLifecycleError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_pr_open_update(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    required = (
        "repo_root",
        "remote_repo",
        "expected_head_sha",
        "head_branch",
        "base_branch",
        "title",
        "expected_request_sha256",
    )
    missing = [key for key in required if not isinstance(arguments.get(key), str) or not arguments.get(key).strip()]
    if missing:
        raise ToolRuntimeError("ERROR_GITHUB_PR_ARGUMENT_REQUIRED", f"missing required string arguments: {','.join(missing)}")
    body = arguments.get("body", "")
    if not isinstance(body, str):
        raise ToolRuntimeError("ERROR_GITHUB_PR_BODY_INVALID", "github.pr_open_update body must be a string.")
    try:
        return open_or_update_pull_request(
            arguments["repo_root"],
            remote_repo=arguments["remote_repo"],
            expected_head_sha=arguments["expected_head_sha"],
            head_branch=arguments["head_branch"],
            base_branch=arguments["base_branch"],
            title=arguments["title"],
            body=body,
            expected_request_sha256=arguments["expected_request_sha256"],
            cfg=cfg,
        )
    except GitHubPullRequestError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc

def _coding_regression_run(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    repo_root = arguments.get("repo_root")
    image = arguments.get("image")
    raw_checks = arguments.get("checks")
    if not isinstance(repo_root, str) or not repo_root.strip():
        raise ToolRuntimeError("ERROR_CODING_REPO_ROOT_REQUIRED", "coding.regression_run requires a non-empty repo_root argument.")
    if not isinstance(image, str) or not image.strip():
        raise ToolRuntimeError("ERROR_CODING_REGRESSION_IMAGE_REQUIRED", "coding.regression_run requires an immutable pinned image identity.")
    if not isinstance(raw_checks, list) or not raw_checks or len(raw_checks) > 6:
        raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECKS", "coding.regression_run requires 1..6 check definitions.")

    checks: list[CodingCheck] = []
    for index, raw in enumerate(raw_checks):
        if not isinstance(raw, dict):
            raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECK", f"check {index} must be an object.")
        check_id = raw.get("check_id")
        kind = raw.get("kind")
        command = raw.get("command")
        required = raw.get("required", True)
        if not isinstance(check_id, str) or not check_id.strip():
            raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECK", f"check {index} requires check_id.")
        if not isinstance(kind, str):
            raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECK", f"check {index} requires kind.")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECK", f"check {index} requires a non-empty argv array.")
        if not isinstance(required, bool):
            raise ToolRuntimeError("ERROR_CODING_REGRESSION_CHECK", f"check {index} required must be boolean.")
        checks.append(CodingCheck(check_id=check_id.strip(), kind=kind, command=tuple(command), required=required))
    try:
        return execute_coding_check_suite(repo_root, image=image, checks=checks, cfg=cfg)
    except CodingCheckError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_ci_status(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    repo = arguments.get("repo")
    if not isinstance(repo, str):
        raise ToolRuntimeError("ERROR_GITHUB_CI_REPO_REQUIRED", "github.ci_status requires repo in owner/name form.")
    try:
        return read_github_ci_status(
            repo=repo,
            branch=arguments.get("branch"),
            workflow=arguments.get("workflow", "CI"),
            expected_head_sha=arguments.get("expected_head_sha"),
            limit=arguments.get("limit", 10),
        )
    except GitHubCIError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc

def _github_issues_list(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return list_github_issues(
            repo=arguments.get("repo"),
            state=arguments.get("state", "open"),
            page=arguments.get("page", 1),
            per_page=arguments.get("per_page", 30),
        )
    except GitHubResourceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_issue_view(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return get_github_issue(repo=arguments.get("repo"), number=arguments.get("number"))
    except GitHubResourceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_prs_list(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return list_github_pull_requests(
            repo=arguments.get("repo"),
            state=arguments.get("state", "open"),
            page=arguments.get("page", 1),
            per_page=arguments.get("per_page", 30),
        )
    except GitHubResourceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_pr_view(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return get_github_pull_request(repo=arguments.get("repo"), number=arguments.get("number"))
    except GitHubResourceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_gists_list(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return list_github_gists(
            owner=arguments.get("owner"),
            page=arguments.get("page", 1),
            per_page=arguments.get("per_page", 30),
        )
    except GitHubResourceError as exc:
        raise ToolRuntimeError(exc.status, exc.detail) from exc


def _github_gist_view(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    del cfg
    try:
        return get_github_gist(gist_id=arguments.get("gist_id"))
    except GitHubResourceError as exc:
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
        autopilot_success_statuses=("SOURCE_LIBRARY_STATUS_OK",),
    ),
    "product.audit": ToolDefinition(
        name="product.audit",
        description="Run the local product-readiness audit.",
        mutates_state=False,
        requires_approval=True,
        handler=_product_audit,
        autopilot_success_statuses=("PRODUCT_AUDIT_OK",),
    ),
    "workspace_profiles.list": ToolDefinition(
        name="workspace_profiles.list",
        description="List local workspace profiles.",
        mutates_state=False,
        requires_approval=True,
        handler=_workspace_profiles,
        autopilot_success_statuses=("WORKSPACE_PROFILES_OK",),
    ),
    "memory.list": ToolDefinition(
        name="memory.list",
        description="List explicit local memory items.",
        mutates_state=False,
        requires_approval=True,
        handler=_memory_list,
        autopilot_success_statuses=("MEMORY_OK",),
    ),
    "coding.repo_status": ToolDefinition(
        name="coding.repo_status",
        description="Inspect an approved local Git repository, exact HEAD, branch, and dirty-state candidate sets without mutation.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_repo_status,
        autopilot_success_statuses=("CODING_REPOSITORY_STATUS_OK",),
    ),
    "coding.read_file": ToolDefinition(
        name="coding.read_file",
        description="Read one bounded repository-relative file with byte SHA-256 and explicit text/binary classification.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_read_file,
        autopilot_success_statuses=("CODING_FILE_READ_OK",),
    ),
    "coding.diff": ToolDefinition(
        name="coding.diff",
        description="Inspect a bounded read-only Git diff and report staged, unstaged, and untracked residue explicitly.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_diff,
        autopilot_success_statuses=("CODING_DIFF_OK",),
    ),
    "coding.patch_check": ToolDefinition(
        name="coding.patch_check",
        description="Validate a bounded unified-diff proposal with path, preimage, dirty-overlap, and git-apply checks without mutation.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_patch_check,
        autopilot_success_statuses=("CODING_PATCH_CHECK_OK",),
    ),
    "coding.patch_apply": ToolDefinition(
        name="coding.patch_apply",
        description="Apply only an explicitly approved patch after exact HEAD, patch-hash, candidate-set, preimage, and dirty-overlap revalidation.",
        mutates_state=True,
        requires_approval=True,
        handler=_coding_patch_apply,
        autopilot_success_statuses=("CODING_MUTATION_APPLIED",),
    ),
    "coding.exec": ToolDefinition(
        name="coding.exec",
        description="Execute a bounded argv command in the isolated coding container with immutable image identity, network disabled, read-only host source, and an ephemeral writable workspace.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_exec,
        autopilot_success_statuses=("CODING_EXEC_OK",),
        autopilot_success_fields=(("execution_status", ("PASS",)),),
        timeout_seconds=150.0,
    ),
    "coding.branch_create": ToolDefinition(
        name="coding.branch_create",
        description="Create one approved non-protected Git work branch only when the repository is clean and exact HEAD matches the approved source SHA.",
        mutates_state=True,
        requires_approval=True,
        handler=_coding_branch_create,
        autopilot_success_statuses=("CODING_BRANCH_CREATED",),
        timeout_seconds=60.0,
    ),
    "coding.commit": ToolDefinition(
        name="coding.commit",
        description="Commit exactly the approved dirty-path set on the exact approved non-protected branch and parent SHA, with rollback on commit or postcondition failure.",
        mutates_state=True,
        requires_approval=True,
        handler=_coding_commit,
        autopilot_success_statuses=("CODING_COMMIT_CREATED",),
        timeout_seconds=120.0,
    ),
    "coding.pr_request": ToolDefinition(
        name="coding.pr_request",
        description="Build an exact local pull-request request artifact from a clean work branch and current expected HEAD without performing a network action.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_pr_request,
        autopilot_success_statuses=("CODING_PR_REQUEST_READY",),
        timeout_seconds=60.0,
    ),
    "github.pr_open_update": ToolDefinition(
        name="github.pr_open_update",
        description="Create or update the unique open GitHub pull request for an exact frozen local request only after the remote head branch SHA matches the approved local HEAD.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_github_pr_open_update,
        autopilot_success_statuses=("GITHUB_PR_OPEN_UPDATE_OK",),
        timeout_seconds=180.0,
    ),
    "coding.regression_run": ToolDefinition(
        name="coding.regression_run",
        description="Execute 1..6 declared test/lint/type/build/security/benchmark checks through the isolated coding-container boundary and preserve PASS/FAIL/BLOCKED rows.",
        mutates_state=False,
        requires_approval=True,
        handler=_coding_regression_run,
        autopilot_success_statuses=("CODING_CHECK_SUITE_EXECUTED_PASS",),
        autopilot_success_fields=(("execution_status", ("PASS",)),),
        timeout_seconds=900.0,
    ),
    "github.ci_status": ToolDefinition(
        name="github.ci_status",
        description="Read bounded GitHub Actions workflow status and optional current-head binding without triggering or mutating remote workflows.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_ci_status,
        autopilot_success_statuses=("GITHUB_CI_STATUS_OK",),
        autopilot_success_fields=(("ci_state", ("PASS",)),),
        timeout_seconds=75.0,
    ),
    "github.issues_list": ToolDefinition(
        name="github.issues_list",
        description="Read one bounded GitHub Issues search page with repository identity and explicit search-ceiling limitations.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_issues_list,
        autopilot_success_statuses=("GITHUB_ISSUES_OK",),
        timeout_seconds=75.0,
    ),
    "github.issue_view": ToolDefinition(
        name="github.issue_view",
        description="Read one exact GitHub issue by repository and numeric issue identity; pull requests are rejected from this boundary.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_issue_view,
        autopilot_success_statuses=("GITHUB_ISSUE_OK",),
        timeout_seconds=75.0,
    ),
    "github.prs_list": ToolDefinition(
        name="github.prs_list",
        description="Read one bounded GitHub pull-request page with exact repository, head SHA/ref, and base-ref fields.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_prs_list,
        autopilot_success_statuses=("GITHUB_PRS_OK",),
        timeout_seconds=75.0,
    ),
    "github.pr_view": ToolDefinition(
        name="github.pr_view",
        description="Read one exact GitHub pull request by repository and numeric PR identity.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_pr_view,
        autopilot_success_statuses=("GITHUB_PR_OK",),
        timeout_seconds=75.0,
    ),
    "github.gists_list": ToolDefinition(
        name="github.gists_list",
        description="Read one bounded GitHub Gists page for an owner or the authenticated account without mutating Gists.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_gists_list,
        autopilot_success_statuses=("GITHUB_GISTS_OK",),
        timeout_seconds=75.0,
    ),
    "github.gist_view": ToolDefinition(
        name="github.gist_view",
        description="Read one exact GitHub Gist with bounded file count/content and canonical gist identity verification.",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=_github_gist_view,
        autopilot_success_statuses=("GITHUB_GIST_OK",),
        timeout_seconds=75.0,
    ),
    "web.render": ToolDefinition(
        name="web.render",
        description="Render one explicitly approved public HTTP(S) page in an optional headless browser and save DOM/screenshot provenance; rendered output is not citation-ready by default.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_render,
        autopilot_success_statuses=("WEB_RENDER_OK",),
    ),
    "web.search": ToolDefinition(
        name="web.search",
        description="Run an explicitly approved live web search and save a credential-free search receipt; result snippets are discovery evidence, not cited page evidence.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_search,
        autopilot_success_statuses=("WEB_SEARCH_OK",),
    ),
    "web.fetch": ToolDefinition(
        name="web.fetch",
        description="Fetch one HTTP(S) URL through governed policy and save a byte-pinned local snapshot.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_fetch,
        autopilot_success_statuses=("WEB_FETCH_SNAPSHOT_OK",),
    ),
    "web.fetch_session": ToolDefinition(
        name="web.fetch_session",
        description="Fetch an approved operator-supplied URL set and save a replayable multi-page browsing receipt.",
        mutates_state=True,
        requires_approval=True,
        network="remote",
        handler=_web_fetch_session,
        autopilot_success_statuses=("WEB_BROWSE_SESSION_OK",),
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


def _binding_failure(call: dict[str, Any], cfg: Settings, *, anchors: tuple[str, ...]) -> tuple[str, str] | None:
    """The stored arguments must hash to the digest the audit log recorded for this call (V05).

    The ledger record alone is mutable: an editor could rewrite both its arguments and its digest.
    So the digest is also checked against each ``anchors`` event in the hash-chained audit log,
    which cannot be edited without breaking the chain.
    """
    recorded = call.get("arguments_sha256")
    if not recorded:
        return "ERROR_TOOL_BINDING_MISSING", "Tool call has no arguments digest to bind a decision to; request a new call."
    if recorded != arguments_digest(call.get("arguments") or {}):
        return "ERROR_TOOL_BINDING_MISMATCH", "Tool call arguments changed after the request; request a new call."
    if verify_tool_audit_chain(cfg)["status"] != "TOOL_AUDIT_CHAIN_OK":
        return "ERROR_TOOL_AUDIT_CHAIN_BROKEN", "The tool audit log failed verification; investigate it, then request a new call."
    events = [event for event in _load_audit_events(cfg) if event.get("tool_call_id") == call.get("tool_call_id")]
    for anchor in anchors:
        audited = {event.get("arguments_sha256") for event in events if event.get("event") == anchor}
        if not audited:
            return "ERROR_TOOL_BINDING_MISSING", f"No audited {anchor} digest for this call; request a new call."
        if audited != {recorded}:
            return "ERROR_TOOL_BINDING_MISMATCH", "Tool call arguments differ from the audited digest; request a new call."
    return None


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
        failure = _binding_failure(call, cfg, anchors=("TOOL_CALL_REQUESTED",))
        if failure:
            raise ToolRuntimeError(failure[0], f"{failure[1]} {tool_call_id}")
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
        failure = _binding_failure(call, cfg, anchors=("TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED"))
        if failure:
            # Ends FAILED rather than staying APPROVED, so a work run sees the call finish and reports it.
            result = {"status": failure[0], "detail": failure[1]}
            return {**call, "status": "FAILED", "executed_at": utc_now(), "executed_by": executed_by, "result": result}
        return {**call, "status": "RUNNING", "started_at": utc_now(), "executed_by": executed_by}

    # The APPROVED -> RUNNING claim is atomic, so a duplicate invocation cannot execute twice.
    call = _transition(tool_call_id, cfg, claim)
    tool_name = str(call.get("tool_name", ""))
    if call["status"] == "FAILED":
        refused = call["result"]
        _append_audit_event(
            {
                "event": "TOOL_CALL_FAILED",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": refused["status"],
                "actor": executed_by,
                "result_status": refused["status"],
            },
            cfg,
        )
        raise ToolRuntimeError(refused["status"], f"{refused['detail']} {tool_call_id}")
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
