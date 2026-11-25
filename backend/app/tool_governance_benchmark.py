"""MATRIX-011 tool-governance benchmark: the frozen denominator for permissions and governed tool execution.

Three groups of cases, each run against the real code in its own temporary workspace with the mock model and no
network:

- action classes (MATRIX-011): allowed, denied, approval-required, cancelled, tool error, timeout, retry, restart,
  duplicate invocation, through the tool runtime and work runs, with the audit chain verified;
- permission taxonomy (TGT-025, flagpole V06): filesystem, network, models, subprocess/git and other side-effecting
  capabilities, distribution channel, unknown inputs, through the permission engine and its real callers;
- authority (INV-006, TGT-014): planning never authorizes, undeclared escalation, approval bound to the arguments,
  audit tampering, memory, and lease revocation.

The receipt records each case's observed outcome, closes its arithmetic, and binds the manifest bytes.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import Settings
from .memory_benchmark import _memory_cannot_authorize, _sha256_bytes, _stable_hash
from .permission_engine import (
    ALLOW,
    ASK,
    DENY,
    SIDE_EFFECTING_CAPABILITIES,
    PermissionRequest,
    evaluate,
)
from .permissions import DirectoryPermissionError, grant_directory
from .recovery_benchmark import _app_restart_reconciles, _settings
from .tools import (
    TOOL_AUDIT_NAME,
    TOOL_CALLS_NAME,
    TOOL_REGISTRY,
    ToolDefinition,
    ToolRuntimeError,
    approve_tool_call,
    cancel_tool_call,
    deny_tool_call,
    list_tool_audit_events,
    list_tool_calls,
    request_tool_call,
    run_tool_call,
    verify_tool_audit_chain,
)
from .utils import read_json, utc_now, write_json

MANIFEST_PATH = Path("_architecture") / "tool_governance_benchmark_v1.json"
MANIFEST_SCHEMA_VERSION = "mucaro-matrix011-tool-governance-benchmark/1.0"
BENCHMARK_SCHEMA_VERSION = "mucaro-matrix011-tool-governance-cert/1.0"
STATUS_OK = "MATRIX011_TOOL_GOVERNANCE_CERT_OK"
STATUS_FAILED = "MATRIX011_TOOL_GOVERNANCE_CERT_FAILED"
REQUIRED_CATEGORIES = (
    "allowed",
    "denied",
    "approval_required",
    "cancelled",
    "tool_error",
    "timeout",
    "retry",
    "restart",
    "duplicate_invocation",
    "filesystem",
    "network",
    "model",
    "side_effecting_capability",
    "distribution",
    "unknown_input",
    "planning_authority",
    "escalation",
    "argument_binding",
    "audit_integrity",
    "memory_authority",
    "lease",
)
PROBE = "benchmark.probe"


@contextmanager
def _probe(handler: Callable[[dict[str, Any], Settings], dict[str, Any]] | None = None, *, timeout: float | None = None) -> Iterator[list[dict[str, Any]]]:
    """A side-effecting test tool that records every execution of its handler."""
    executions: list[dict[str, Any]] = []

    def recording(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
        executions.append(dict(arguments))
        return (handler or (lambda a, c: {"status": "PROBE_OK"}))(arguments, cfg)

    definition = ToolDefinition(PROBE, "benchmark probe", True, True, recording)
    if timeout is not None:
        definition = replace(definition, timeout_seconds=timeout)
    TOOL_REGISTRY[PROBE] = definition
    try:
        yield executions
    finally:
        TOOL_REGISTRY.pop(PROBE, None)


def _status(call: Callable[[], Any]) -> str:
    try:
        call()
    except ToolRuntimeError as exc:
        return exc.status
    return "ACCEPTED"


def _call(cfg: Settings, tool_call_id: str) -> dict[str, Any]:
    return next(row for row in list_tool_calls(cfg)["tool_calls"] if row["tool_call_id"] == tool_call_id)


def _events(cfg: Settings, tool_call_id: str | None = None) -> list[str]:
    return [
        event["event"]
        for event in list_tool_audit_events(cfg)["events"]
        if tool_call_id is None or event.get("tool_call_id") == tool_call_id
    ]


def _chain_ok(cfg: Settings) -> bool:
    return verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"


def _req(capability: str, **kwargs: Any) -> PermissionRequest:
    return PermissionRequest(capability=capability, subject="matrix011-benchmark", **kwargs)


# --- action classes ---------------------------------------------------------------------------------------------


def _allowed_executes_and_audits(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    call_id = request_tool_call("source_library.status", {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
    approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
    executed = run_tool_call(call_id, cfg=cfg, executed_by="benchmark")
    events = _events(cfg, call_id)
    ok = (
        executed["tool_call"]["status"] == "EXECUTED"
        and events == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_STARTED", "TOOL_CALL_EXECUTED"]
        and _chain_ok(cfg)
    )
    return ("EXECUTED_AND_AUDITED" if ok else "EXECUTION_NOT_AUDITED"), {"events": events}


def _unknown_tool_refused(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    status = _status(lambda: request_tool_call("shell.exec", {"cmd": "rm -rf /"}, cfg=cfg, requested_by="benchmark"))
    rejected = [e for e in list_tool_audit_events(cfg)["events"] if e["event"] == "TOOL_REQUEST_REJECTED"]
    ok = status == "ERROR_TOOL_NOT_ALLOWED" and len(rejected) == 1 and not list_tool_calls(cfg)["tool_calls"]
    return (status if ok else "UNKNOWN_TOOL_ACCEPTED"), {"rejected_events": len(rejected)}


def _denied_never_executes(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _probe() as executions:
        call_id = request_tool_call(PROBE, {"target": "x"}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        deny_tool_call(call_id, cfg=cfg)
        rerun = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
        reapprove = _status(lambda: approve_tool_call(call_id, cfg=cfg, approved_by="benchmark"))
    ok = _call(cfg, call_id)["status"] == "DENIED" and executions == [] and rerun != "ACCEPTED" and reapprove != "ACCEPTED"
    return ("DENIED_NEVER_EXECUTED" if ok else "DENIED_CALL_RAN"), {"rerun": rerun, "reapprove": reapprove}


def _approval_required_before_run(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _probe() as executions:
        call_id = request_tool_call(PROBE, {}, cfg=cfg, requested_by="model")["tool_call"]["tool_call_id"]
        status = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
    ok = executions == [] and _call(cfg, call_id)["status"] == "PENDING_APPROVAL"
    return (status if ok else "RAN_WITHOUT_APPROVAL"), {"executions": len(executions)}


def _cancelled_never_executes(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _probe() as executions:
        call_id = request_tool_call(PROBE, {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
        cancel_tool_call(call_id, cfg=cfg, cancelled_by="benchmark", reason="benchmark")
        rerun = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
    ok = _call(cfg, call_id)["status"] == "CANCELLED" and executions == [] and rerun != "ACCEPTED" and _chain_ok(cfg)
    return ("CANCELLED_NEVER_EXECUTED" if ok else "CANCELLED_CALL_RAN"), {"rerun": rerun}


def _tool_error_recorded(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    def fails(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
        raise ToolRuntimeError("ERROR_BENCHMARK_TOOL_FAILED", "handler failed")

    with _probe(fails):
        call_id = request_tool_call(PROBE, {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
        status = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
    call = _call(cfg, call_id)
    events = _events(cfg, call_id)
    ok = (
        status == "ERROR_BENCHMARK_TOOL_FAILED"
        and call["status"] == "FAILED"
        and call["result"]["status"] == status
        and events[-1] == "TOOL_CALL_FAILED"
        and "TOOL_CALL_EXECUTED" not in events
    )
    return ("FAILED_WITH_TYPED_STATUS" if ok else "FAILURE_NOT_RECORDED"), {"status": status}


def _timeout_never_executed(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    release = threading.Event()
    finished = threading.Event()

    def hangs(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
        release.wait(10)
        finished.set()
        return {"status": "LATE_RESULT"}

    with _probe(hangs, timeout=0.2):
        call_id = request_tool_call(PROBE, {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
        status = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
        release.set()
        finished.wait(5)
    call = _call(cfg, call_id)
    ok = (
        status == "ERROR_TOOL_TIMEOUT"
        and call["status"] == "FAILED"
        and call["result"].get("handler_abandoned") is True
        and call["result"].get("side_effects") == "UNKNOWN"
        and "TOOL_CALL_EXECUTED" not in _events(cfg, call_id)
    )
    return ("TIMEOUT_SIDE_EFFECTS_UNKNOWN" if ok else "TIMEOUT_RECORDED_AS_SUCCESS"), {"status": status}


def _retry_needs_fresh_approval(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from .conversations import create_conversation
    from .recovery_benchmark import _flaky_tool, _inline_runs
    from .response_flow import get_pending_interaction, resolve_interaction

    with _inline_runs() as work_runs, _flaky_tool(work_runs) as flaky:
        chat = create_conversation("retry", cfg)["conversation_id"]
        run = work_runs.submit_message(chat, "/flaky", mode="ask", cfg=cfg)["run"]
        first = list_tool_calls(cfg)["tool_calls"][0]
        approve_tool_call(first["tool_call_id"], cfg=cfg, approved_by="benchmark")
        work_runs.resume_waiting_run(run["run_id"], cfg)
        question = get_pending_interaction(chat, cfg)
        resolve_interaction(
            question["interaction_id"], expected_version=question["version"], resolved_by="user", option_id="retry_failed", cfg=cfg
        )
        work_runs.resume_waiting_run(run["run_id"], cfg)
        state = work_runs.get_run(run["run_id"], cfg)["state"]
        handler_calls = flaky["n"]
    retry = list_tool_calls(cfg)["tool_calls"][-1]
    ok = (
        state == "AWAITING_APPROVAL"
        and retry["status"] == "PENDING_APPROVAL"
        and retry.get("retry_of") == first["tool_call_id"]
        and not retry.get("approved_by")
        and handler_calls == 1
    )
    return ("RETRY_PENDING_APPROVAL" if ok else "RETRY_REUSED_APPROVAL"), {"run_state": state, "handler_calls": handler_calls}


def _duplicate_invocation_refused(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _probe() as executions:
        call_id = request_tool_call(PROBE, {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
        run_tool_call(call_id, cfg=cfg, executed_by="benchmark")
        second = _status(lambda: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
        reapprove = _status(lambda: approve_tool_call(call_id, cfg=cfg, approved_by="benchmark"))
    ok = len(executions) == 1 and second != "ACCEPTED" and reapprove != "ACCEPTED" and _events(cfg, call_id).count("TOOL_CALL_EXECUTED") == 1
    return ("SECOND_RUN_REFUSED" if ok else "RAN_TWICE"), {"executions": len(executions), "second_run": second}


# --- permission taxonomy ----------------------------------------------------------------------------------------


def _filesystem_grant_enforced(cfg: Settings, root: Path) -> tuple[str, dict[str, Any]]:
    outside = root.parent / f"{root.name}-outside"  # beside the project root, so only a grant can open it
    granted = outside / "granted"
    sibling = outside / "sibling"
    secret = outside / "secret"
    for directory in (granted, sibling, secret):
        directory.mkdir(parents=True, exist_ok=True)
    (secret / "token.txt").write_text("s", encoding="utf-8")
    (granted / "link").symlink_to(secret)
    before = evaluate(_req("filesystem.read", target=str(granted)), cfg=cfg).decision
    grant_directory(granted, cfg, granted_by="benchmark")
    decisions = {
        "before_grant": before,
        "granted": evaluate(_req("filesystem.read", target=str(granted)), cfg=cfg).decision,
        "traversal": evaluate(_req("filesystem.read", target=str(granted / ".." / "sibling")), cfg=cfg).decision,
        "symlink": evaluate(_req("filesystem.read", target=str(granted / "link" / "token.txt")), cfg=cfg).decision,
        "no_target": evaluate(_req("filesystem.read"), cfg=cfg).decision,
        "write_by_agent": evaluate(_req("filesystem.write", target=str(granted / "new.txt")), cfg=cfg).decision,
    }
    expected = {"before_grant": DENY, "granted": ALLOW, "traversal": DENY, "symlink": DENY, "no_target": DENY, "write_by_agent": ASK}
    return ("GRANT_ENFORCED_NO_ESCAPE" if decisions == expected else "FILESYSTEM_BOUNDARY_BROKEN"), decisions


def _too_broad_grant_refused(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    refused = []
    for candidate in (Path("/"), Path.home()):
        try:
            grant_directory(candidate, cfg, granted_by="benchmark")
            refused.append("GRANTED")
        except DirectoryPermissionError as exc:
            refused.append(exc.status)
    ok = refused == ["ERROR_DIRECTORY_PERMISSION_TOO_BROAD"] * 2
    return ("ERROR_DIRECTORY_PERMISSION_TOO_BROAD" if ok else "BROAD_GRANT_ACCEPTED"), {"results": refused}


def _network_private_and_live_gates(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    statuses = {}
    for tool_name, arguments in (
        ("web.fetch", {"url": "http://127.0.0.1:9/admin"}),
        ("web.search", {"query": "benchmark"}),
    ):
        call_id = request_tool_call(tool_name, arguments, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
        approve_tool_call(call_id, cfg=cfg, approved_by="benchmark")
        statuses[tool_name] = _status(lambda call_id=call_id: run_tool_call(call_id, cfg=cfg, executed_by="benchmark"))
    statuses["agent_http"] = evaluate(_req("network.public_http"), cfg=cfg).decision
    ok = statuses == {
        "web.fetch": "ERROR_WEB_PRIVATE_HOST_BLOCKED",
        "web.search": "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED",
        "agent_http": ASK,
    }
    return ("NETWORK_FAILS_CLOSED" if ok else "NETWORK_GATE_OPEN"), statuses


def _model_denied_before_transport(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from . import model_router
    from .model_router import ModelProviderError, generate_answer

    reached = {"n": 0}
    original = model_router._local_answer

    def transport(*args: Any, **kwargs: Any) -> Any:
        reached["n"] += 1
        raise AssertionError("local model transport reached")

    model_router._local_answer = transport
    try:
        try:
            generate_answer(
                "q",
                [{"chunk_id": "c", "source_id": "s", "text": "t"}],
                model_provider="openai_compatible_local",
                cfg=replace(cfg, distribution_channel="sideload"),
            )
            status = "ANSWERED"
        except ModelProviderError as exc:
            status = exc.status
    finally:
        model_router._local_answer = original
    ok = status == "ERROR_PERMISSION_DENIED" and reached["n"] == 0
    return (status if ok else "TRANSPORT_REACHED"), {"transport_calls": reached["n"]}


def _side_effects_ask_for_agent(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    capabilities = sorted(SIDE_EFFECTING_CAPABILITIES - {"filesystem.write"})
    agent = {name: evaluate(_req(name), cfg=cfg).decision for name in capabilities}
    approved = {name: evaluate(_req(name, approved=True), cfg=cfg).decision for name in capabilities}
    read_only = {name: evaluate(_req(name), cfg=cfg).decision for name in ("model.invoke", "git.read", "database.read")}
    ok = (
        set(agent.values()) == {ASK}
        and set(approved.values()) == {ALLOW}
        and set(read_only.values()) == {ALLOW}
        and {"subprocess.execute", "git.mutate", "network.public_http", "mcp.invoke", "subagent.spawn"} <= set(capabilities)
    )
    return ("AGENT_SIDE_EFFECTS_ASK" if ok else "SIDE_EFFECT_ALLOWED_WITHOUT_APPROVAL"), {"asked": len(agent)}


def _distribution_channel_enforced(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    store = replace(cfg, distribution_channel="mac_app_store")
    decisions = {
        "store_subprocess": evaluate(_req("subprocess.execute", origin="operator"), cfg=store).decision,
        "store_git_read": evaluate(_req("git.read", origin="operator"), cfg=store).decision,
        "unsupported_channel": evaluate(_req("model.invoke"), cfg=replace(cfg, distribution_channel="sideload")).decision,
    }
    ok = set(decisions.values()) == {DENY}
    return ("CHANNEL_RESTRICTIONS_DENY" if ok else "CHANNEL_NOT_ENFORCED"), decisions


def _unknown_inputs_denied(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    decisions = {
        "unknown_capability": evaluate(_req("teleport.anywhere", origin="operator"), cfg=cfg).decision,
        "unknown_origin": evaluate(_req("model.invoke", origin="root"), cfg=cfg).decision,
        "blank_subject": evaluate(PermissionRequest(capability="model.invoke", subject="  "), cfg=cfg).decision,
    }
    ok = set(decisions.values()) == {DENY}
    return ("FAIL_CLOSED_ON_UNKNOWN" if ok else "UNKNOWN_INPUT_ALLOWED"), decisions


# --- authority --------------------------------------------------------------------------------------------------


def _planning_never_authorizes(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    capabilities = sorted(SIDE_EFFECTING_CAPABILITIES - {"filesystem.write"})
    decisions = {name: evaluate(_req(name, origin="planning", approved=True), cfg=cfg).decision for name in capabilities}
    return ("PLANNING_STILL_ASKS" if set(decisions.values()) == {ASK} else "PLANNING_AUTHORIZED"), {"checked": len(decisions)}


def _undeclared_escalation_denied(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    decision = evaluate(_req("subprocess.execute", origin="operator", declared=frozenset({"model.invoke"})), cfg=cfg)
    return (decision.decision if decision.decision == DENY else "ESCALATION_ALLOWED"), {"reasons": list(decision.reasons)}


def _edit_call(cfg: Settings, tool_call_id: str, **fields: Any) -> None:
    path = cfg.processed_dir / TOOL_CALLS_NAME
    calls = read_json(path, [])
    for call in calls:
        if call["tool_call_id"] == tool_call_id:
            call.update(fields)
    write_json(path, calls)


def _arguments_bound_to_approval(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    with _probe() as executions:
        before = request_tool_call(PROBE, {"url": "https://example.com"}, cfg=cfg, requested_by="model")["tool_call"]
        _edit_call(cfg, before["tool_call_id"], arguments={"url": "https://attacker.invalid"})
        approve_status = _status(
            lambda: approve_tool_call(
                before["tool_call_id"], cfg=cfg, approved_by="benchmark", expected_arguments_sha256=before["arguments_sha256"]
            )
        )
        after = request_tool_call(PROBE, {"url": "https://example.com"}, cfg=cfg, requested_by="model")["tool_call"]
        approve_tool_call(after["tool_call_id"], cfg=cfg, approved_by="benchmark")
        _edit_call(cfg, after["tool_call_id"], arguments={"url": "https://attacker.invalid"})
        run_status = _status(lambda: run_tool_call(after["tool_call_id"], cfg=cfg, executed_by="benchmark"))
    ok = approve_status == run_status == "ERROR_TOOL_BINDING_MISMATCH" and executions == []
    return ("ERROR_TOOL_BINDING_MISMATCH" if ok else "CHANGED_ARGUMENTS_ACCEPTED"), {
        "before_approval": approve_status,
        "after_approval": run_status,
    }


def _audit_tamper_blocks_approval(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    call_id = request_tool_call("source_library.status", {}, cfg=cfg, requested_by="benchmark")["tool_call"]["tool_call_id"]
    path = cfg.processed_dir / TOOL_AUDIT_NAME
    events = read_json(path, [])
    events[-1]["actor"] = "someone-else"
    write_json(path, events)
    status = _status(lambda: approve_tool_call(call_id, cfg=cfg, approved_by="benchmark"))
    ok = _call(cfg, call_id)["status"] == "PENDING_APPROVAL" and not _chain_ok(cfg)
    return (status if ok else "TAMPERED_CHAIN_ACCEPTED"), {"status": status}


def _lease_revocation_enforced(cfg: Settings, _root: Path) -> tuple[str, dict[str, Any]]:
    from .permission_leases import (
        grant_permission_lease,
        matching_permission_leases,
        revoke_permission_lease,
        verify_permission_lease_audit_chain,
    )

    TOOL_REGISTRY["benchmark.read"] = ToolDefinition(
        "benchmark.read", "lease fixture", False, True, lambda a, c: {"status": "OK"}, autopilot_success_statuses=("OK",)
    )
    try:
        lease = grant_permission_lease(
            run_id="run-lease", scope_type="EXACT_TOOLS", tool_names=["benchmark.read"], granted_by="operator", cfg=cfg
        )["lease"]
        before = request_tool_call("benchmark.read", {}, run_id="run-lease", requested_by="autopilot", reason="lease", cfg=cfg)["tool_call"]
        matched_before = matching_permission_leases(before["tool_call_id"], cfg=cfg)["match_count"]
        first = revoke_permission_lease(lease["lease_id"], revoked_by="operator", cfg=cfg)
        second = revoke_permission_lease(lease["lease_id"], revoked_by="operator", cfg=cfg)
        after = request_tool_call("benchmark.read", {}, run_id="run-lease", requested_by="autopilot", reason="lease", cfg=cfg)["tool_call"]
        matched_after = matching_permission_leases(after["tool_call_id"], cfg=cfg)["match_count"]
        audit = verify_permission_lease_audit_chain(cfg)["status"]
    finally:
        TOOL_REGISTRY.pop("benchmark.read", None)
    ok = matched_before == 1 and matched_after == 0 and first["changed"] and not second["changed"] and audit == "PERMISSION_LEASE_AUDIT_OK"
    return ("REVOKED_LEASE_GRANTS_NOTHING" if ok else "REVOKED_LEASE_STILL_MATCHES"), {
        "matched_before": matched_before,
        "matched_after": matched_after,
    }


SUBPROCESS_CASES = {"RESTART_INTERRUPTED_NOT_RERUN"}

CASES: dict[str, Callable[..., tuple[str, dict[str, Any]]]] = {
    "ALLOWED_EXECUTES_AND_AUDITS": _allowed_executes_and_audits,
    "UNKNOWN_TOOL_REFUSED": _unknown_tool_refused,
    "DENIED_NEVER_EXECUTES": _denied_never_executes,
    "APPROVAL_REQUIRED_BEFORE_RUN": _approval_required_before_run,
    "CANCELLED_NEVER_EXECUTES": _cancelled_never_executes,
    "TOOL_ERROR_RECORDED": _tool_error_recorded,
    "TIMEOUT_NEVER_EXECUTED": _timeout_never_executed,
    "RETRY_NEEDS_FRESH_APPROVAL": _retry_needs_fresh_approval,
    "RESTART_INTERRUPTED_NOT_RERUN": _app_restart_reconciles,
    "DUPLICATE_INVOCATION_REFUSED": _duplicate_invocation_refused,
    "FILESYSTEM_GRANT_ENFORCED": _filesystem_grant_enforced,
    "TOO_BROAD_GRANT_REFUSED": _too_broad_grant_refused,
    "NETWORK_PRIVATE_AND_LIVE_GATES": _network_private_and_live_gates,
    "MODEL_DENIED_BEFORE_TRANSPORT": _model_denied_before_transport,
    "SIDE_EFFECTS_ASK_FOR_AGENT": _side_effects_ask_for_agent,
    "DISTRIBUTION_CHANNEL_ENFORCED": _distribution_channel_enforced,
    "UNKNOWN_INPUTS_DENIED": _unknown_inputs_denied,
    "PLANNING_NEVER_AUTHORIZES": _planning_never_authorizes,
    "UNDECLARED_ESCALATION_DENIED": _undeclared_escalation_denied,
    "ARGUMENTS_BOUND_TO_APPROVAL": _arguments_bound_to_approval,
    "AUDIT_TAMPER_BLOCKS_APPROVAL": _audit_tamper_blocks_approval,
    "MEMORY_CANNOT_AUTHORIZE": _memory_cannot_authorize,
    "LEASE_REVOCATION_ENFORCED": _lease_revocation_enforced,
}
_SUBPROCESS_HANDLERS = {case_id: CASES[case_id] for case_id in SUBPROCESS_CASES}


def _run_case(case: dict[str, Any], workspace: Path, project_root: Path) -> dict[str, Any]:
    case_id = case["case_id"]
    handler = CASES.get(case_id)
    root = workspace / case_id.lower()
    root.mkdir(parents=True, exist_ok=True)
    if handler is None:
        observed, evidence = "NO_HANDLER", {}
    else:
        try:
            if handler is _SUBPROCESS_HANDLERS.get(case_id):
                observed, evidence = handler(_settings(root), root, project_root=project_root)
            else:
                observed, evidence = handler(_settings(root), root)
        except Exception as exc:  # noqa: BLE001 - a crashing case is a failed case, recorded with its error type
            observed, evidence = f"CASE_ERROR:{type(exc).__name__}", {"detail": str(exc)[:400]}
    return {
        "case_id": case_id,
        "category": case["category"],
        "expected_outcome": case["expected_outcome"],
        "observed_outcome": observed,
        "passed": observed == case["expected_outcome"],
        "evidence": evidence,
    }


def certify_tool_governance_benchmark(project_root: Path, workspace: Path, *, head_sha: str | None = None) -> dict[str, Any]:
    project_root = project_root.resolve()
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (project_root / MANIFEST_PATH).read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = manifest.get("cases") or []
    case_ids = [case.get("case_id") for case in cases]
    categories = {case.get("category") for case in cases}
    required = int(manifest.get("required_case_count") or 0)
    denominator_checks = {
        "manifest_schema": manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION,
        "case_count_matches_declaration": len(cases) == required and required > 0,
        "case_ids_unique": len(set(case_ids)) == len(case_ids),
        "every_case_has_a_handler": all(case_id in CASES for case_id in case_ids),
        "every_handler_is_declared": set(CASES) == set(case_ids),
        "required_categories_declared": list(manifest.get("required_categories") or []) == list(REQUIRED_CATEGORIES),
        "every_required_category_covered": set(REQUIRED_CATEGORIES) <= categories,
    }
    rows: list[dict[str, Any]] = []
    if all(denominator_checks.values()):
        rows = [_run_case(case, workspace, project_root) for case in cases]
    passed = sum(1 for row in rows if row["passed"])
    arithmetic = {
        "case_count_expected": required,
        "case_count_observed": len(rows),
        "case_pass": passed,
        "case_fail": len(rows) - passed,
        "arithmetic_closes": passed + (len(rows) - passed) == len(rows) == required,
    }
    terminal_checks = {
        "denominator_checks_pass": all(denominator_checks.values()),
        "all_cases_pass": bool(rows) and passed == required,
        "case_identity_matches_manifest": [row["case_id"] for row in rows] == case_ids,
        "every_required_category_covered": set(REQUIRED_CATEGORIES) <= {row["category"] for row in rows if row["passed"]},
        "zero_unexpected_outcomes": bool(rows) and all(row["passed"] for row in rows),
        "arithmetic_closes": arithmetic["arithmetic_closes"],
    }
    receipt = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": STATUS_OK if all(terminal_checks.values()) else STATUS_FAILED,
        "benchmark_id": manifest.get("benchmark_id"),
        "head_sha": head_sha,
        "executed_utc": utc_now(),
        "benchmark_manifest_path": str(MANIFEST_PATH),
        "benchmark_manifest_sha256": _sha256_bytes(manifest_bytes),
        "denominator_checks": denominator_checks,
        "arithmetic": arithmetic,
        "terminal_checks": terminal_checks,
        "case_rows": rows,
        "failed_checks": [name for name, ok in {**denominator_checks, **terminal_checks}.items() if not ok],
        "limitations": list(manifest.get("limitations") or []),
    }
    receipt["receipt_sha256"] = _stable_hash(receipt)
    return receipt
