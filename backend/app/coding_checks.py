from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_container import (
    CodingContainerError,
    build_coding_container_plan,
    execute_coding_container_plan,
    validate_coding_container_plan,
)
from .config import Settings, settings

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


def _validate_checks(checks: list[CodingCheck]) -> None:
    if not checks:
        raise CodingCheckError("ERROR_CODING_CHECKS_REQUIRED", "at least one check is required")
    seen: set[str] = set()
    for check in checks:
        check.validate()
        if check.check_id in seen:
            raise CodingCheckError("ERROR_CODING_CHECK_DUPLICATE", check.check_id)
        seen.add(check.check_id)


def build_coding_check_suite_plan(
    repo_root: str | Path,
    *,
    image: str,
    checks: list[CodingCheck],
    cfg: Settings = settings,
) -> dict[str, Any]:
    _validate_checks(checks)
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
                "mount_mode": "read_only",
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
            "read_only_mount_for_all": all(row.get("mount_mode") == "read_only" for row in rows),
            "no_execution_claimed": True,
        },
        "limitations": [
            "this artifact plans test/lint/type/build/security/benchmark checks but does not execute them",
            "a valid plan is not evidence that the declared command passes",
        ],
        "falsification_criteria": [
            "block the suite plan when any required check cannot produce a current valid read-only plan",
            "reject duplicate check identities",
            "reject coding checks that request network access or writable repository mounts",
            "never translate PLANNED into PASS without executable evidence",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload


def execute_coding_check_suite(
    repo_root: str | Path,
    *,
    image: str,
    checks: list[CodingCheck],
    cfg: Settings = settings,
) -> dict[str, Any]:
    _validate_checks(checks)
    rows: list[dict[str, Any]] = []
    for check in checks:
        try:
            plan = build_coding_container_plan(
                repo_root,
                image=image,
                command=list(check.command),
                cfg=cfg,
                network_allowed=False,
            )
            receipt = execute_coding_container_plan(plan, cfg=cfg)
            state = "PASS" if receipt.get("passed") is True else "FAIL"
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "required": check.required,
                "state": state,
                "command": list(check.command),
                "plan_sha256": plan.plan_sha256,
                "execution_receipt": receipt,
            }
        except CodingContainerError as exc:
            row = {
                "check_id": check.check_id,
                "kind": check.kind,
                "required": check.required,
                "state": "BLOCKED",
                "command": list(check.command),
                "error_status": exc.status,
                "error_detail": exc.detail,
            }
        rows.append(row)

    state_counts = {state: sum(row["state"] == state for row in rows) for state in ("PASS", "FAIL", "BLOCKED")}
    required_failures = [row["check_id"] for row in rows if row["required"] and row["state"] != "PASS"]
    all_required_pass = not required_failures
    payload = {
        "schema_version": CODING_CHECK_SUITE_SCHEMA_VERSION,
        "status": "CODING_CHECK_SUITE_PASS" if all_required_pass else "CODING_CHECK_SUITE_NOT_PASS",
        "execution_status": "EXECUTED",
        "image": image,
        "check_count": len(rows),
        "state_counts": state_counts,
        "required_failure_count": len(required_failures),
        "required_failures": required_failures,
        "checks": rows,
        "invariants": {
            "row_accounting_closed": len(rows) == sum(state_counts.values()),
            "unique_check_ids": len({row["check_id"] for row in rows}) == len(rows),
            "all_pass_rows_have_execution_receipts": all(
                row["state"] != "PASS" or isinstance(row.get("execution_receipt"), dict) for row in rows
            ),
            "all_required_pass": all_required_pass,
            "network_disabled_for_executed_rows": all(
                row.get("execution_receipt", {}).get("network_allowed") is False
                for row in rows
                if row["state"] in {"PASS", "FAIL"}
            ),
            "read_only_repository_for_executed_rows": all(
                row.get("execution_receipt", {}).get("repository_read_only") is True
                for row in rows
                if row["state"] in {"PASS", "FAIL"}
            ),
        },
        "limitations": [
            "suite PASS proves only the declared checks on the recorded repository and immutable image manifestations",
            "tool coverage remains bounded by the supplied check definitions",
            "container-engine and image supply-chain trust require separate release certification evidence",
        ],
        "falsification_criteria": [
            "suite cannot PASS when any required row is FAIL or BLOCKED",
            "PASS rows require live execution receipts with exit code zero",
            "repository drift, stale plans, timeouts and oversized outputs are BLOCKED rather than silently ignored",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload
