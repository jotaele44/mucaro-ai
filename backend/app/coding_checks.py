from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_container import (
    CodingContainerError,
    build_coding_container_plan,
    run_coding_container_plan,
    validate_coding_container_plan,
)
from .config import Settings, settings
from .event_stream import EventSink, emit

CODING_CHECK_SUITE_SCHEMA_VERSION = "mucaro-coding-check-suite/1.1"
ALLOWED_CHECK_KINDS = {"test", "lint", "type", "build", "security", "benchmark"}


class CodingCheckError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class CodingCheck:
    check_id: str
    kind: str
    command: tuple[str, ...]
    required: bool = True

    def validate(self) -> None:
        if not self.check_id or self.kind not in ALLOWED_CHECK_KINDS or not self.command:
            raise CodingCheckError("ERROR_CODING_CHECK_DEFINITION", self.check_id or self.kind)


def _validated_checks(checks: list[CodingCheck]) -> list[CodingCheck]:
    if not checks:
        raise CodingCheckError("ERROR_CODING_CHECKS_REQUIRED", "at least one check is required")
    seen: set[str] = set()
    for check in checks:
        check.validate()
        if check.check_id in seen:
            raise CodingCheckError("ERROR_CODING_CHECK_DUPLICATE", check.check_id)
        seen.add(check.check_id)
    return checks


def _receipt_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_coding_check_suite_plan(
    repo_root: str | Path,
    *,
    image: str,
    checks: list[CodingCheck],
    cfg: Settings = settings,
) -> dict[str, Any]:
    checks = _validated_checks(checks)
    rows: list[dict[str, Any]] = []
    planning_failures: list[str] = []
    for check in checks:
        try:
            plan = build_coding_container_plan(
                repo_root,
                image=image,
                command=list(check.command),
                cfg=cfg,
                network_allowed=False,
            )
            validation = validate_coding_container_plan(plan, cfg=cfg)
            planned = validation.get("status") == "CODING_CONTAINER_PLAN_VALID"
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "command": list(check.command),
                "required": check.required,
                "state": "PLANNED" if planned else "STALE",
                "plan_sha256": plan.plan_sha256,
                "head_sha": plan.head_sha,
                "dirty_status": plan.dirty_status,
                "dirty_paths_sha256": plan.dirty_paths_sha256,
                "image": plan.image,
                "network_allowed": plan.network_allowed,
                "mount_mode": plan.mount_mode,
                "validation": validation,
            }
        except CodingContainerError as exc:
            planned = False
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "command": list(check.command),
                "required": check.required,
                "state": "BLOCKED",
                "error_status": exc.status,
                "error_detail": exc.detail,
                "image": image,
                "network_allowed": False,
                "mount_mode": "read_only_source_ephemeral_workspace",
            }
        if check.required and not planned:
            planning_failures.append(check.check_id)
        rows.append(row)

    state_counts = {state: sum(row["state"] == state for row in rows) for state in ("PLANNED", "STALE", "BLOCKED")}
    payload = {
        "schema_version": CODING_CHECK_SUITE_SCHEMA_VERSION,
        "status": "CODING_CHECK_SUITE_PLAN_READY" if not planning_failures else "CODING_CHECK_SUITE_PLAN_BLOCKED",
        "execution_status": "NOT_EXECUTED",
        "image": image,
        "check_count": len(rows),
        "state_counts": state_counts,
        "required_planning_failure_count": len(planning_failures),
        "required_planning_failures": planning_failures,
        "checks": rows,
        "invariants": {
            "row_accounting_closed": len(rows) == sum(state_counts.values()),
            "unique_check_ids": len({row["check_id"] for row in rows}) == len(rows),
            "network_disabled_for_all": all(row.get("network_allowed") is False for row in rows),
            "isolated_mount_for_all": all(row.get("mount_mode") == "read_only_source_ephemeral_workspace" for row in rows),
            "no_execution_claimed": True,
        },
        "limitations": [
            "a valid plan is not evidence that the declared command passes",
            "live execution still depends on an available container engine and exact pinned coding image",
        ],
        "falsification_criteria": [
            "block the suite plan when any required check cannot produce a current valid isolated plan",
            "reject duplicate check identities",
            "reject coding checks that request network access or a writable host repository mount",
            "never translate PLANNED into PASS without executable evidence",
        ],
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload


def execute_coding_check_suite(
    repo_root: str | Path,
    *,
    image: str,
    checks: list[CodingCheck],
    cfg: Settings = settings,
    on_event: EventSink | None = None,
) -> dict[str, Any]:
    checks = _validated_checks(checks)
    rows: list[dict[str, Any]] = []
    required_failures: list[str] = []
    required_blocked: list[str] = []

    for check in checks:
        emit(on_event, "check_start", check_id=check.check_id, kind=check.kind, command=list(check.command))
        try:
            plan = build_coding_container_plan(
                repo_root,
                image=image,
                command=list(check.command),
                cfg=cfg,
                network_allowed=False,
            )
            receipt = run_coding_container_plan(plan, cfg=cfg)
            passed = receipt.get("execution_status") == "PASS"
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "command": list(check.command),
                "required": check.required,
                "state": "PASS" if passed else "FAIL",
                "plan_sha256": plan.plan_sha256,
                "execution_receipt": receipt,
            }
            if check.required and not passed:
                required_failures.append(check.check_id)
        except CodingContainerError as exc:
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "command": list(check.command),
                "required": check.required,
                "state": "BLOCKED",
                "error_status": exc.status,
                "error_detail": exc.detail,
                "execution_receipt": exc.receipt,
            }
            if check.required:
                required_blocked.append(check.check_id)
        rows.append(row)
        emit(on_event, "check_result", check_id=check.check_id, state=row["state"], **({"error_status": row["error_status"]} if "error_status" in row else {}))

    state_counts = {state: sum(row["state"] == state for row in rows) for state in ("PASS", "FAIL", "BLOCKED")}
    required_failure_count = len(required_failures)
    required_blocked_count = len(required_blocked)
    if required_blocked_count:
        status = "CODING_CHECK_SUITE_EXECUTED_BLOCKED"
        execution_status = "BLOCKED"
    elif required_failure_count:
        status = "CODING_CHECK_SUITE_EXECUTED_FAIL"
        execution_status = "FAIL"
    else:
        status = "CODING_CHECK_SUITE_EXECUTED_PASS"
        execution_status = "PASS"

    payload = {
        "schema_version": CODING_CHECK_SUITE_SCHEMA_VERSION,
        "status": status,
        "execution_status": execution_status,
        "image": image,
        "check_count": len(rows),
        "state_counts": state_counts,
        "required_failure_count": required_failure_count,
        "required_failures": required_failures,
        "required_blocked_count": required_blocked_count,
        "required_blocked": required_blocked,
        "checks": rows,
        "invariants": {
            "row_accounting_closed": len(rows) == sum(state_counts.values()),
            "unique_check_ids": len({row["check_id"] for row in rows}) == len(rows),
            "required_pass_closure": not required_failures and not required_blocked,
            "every_row_has_terminal_state": all(row["state"] in {"PASS", "FAIL", "BLOCKED"} for row in rows),
        },
        "falsification_criteria": [
            "any required nonzero command prevents suite PASS",
            "any required infrastructure or isolation error prevents suite PASS",
            "every PASS must contain an execution receipt from the isolated coding container boundary",
            "optional failures remain visible and are never silently discarded",
        ],
    }
    payload["receipt_sha256"] = _receipt_hash(payload)
    return payload
