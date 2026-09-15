from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_container import (
    CodingContainerError,
    build_coding_container_plan,
    validate_coding_container_plan,
)
from .config import Settings, settings

CODING_CHECK_SUITE_SCHEMA_VERSION = "mucaro-coding-check-suite/1.0"
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


def build_coding_check_suite_plan(
    repo_root: str | Path,
    *,
    image: str,
    checks: list[CodingCheck],
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not checks:
        raise CodingCheckError("ERROR_CODING_CHECKS_REQUIRED", "at least one check is required")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    planning_failures: list[str] = []
    for check in checks:
        check.validate()
        if check.check_id in seen:
            raise CodingCheckError("ERROR_CODING_CHECK_DUPLICATE", check.check_id)
        seen.add(check.check_id)
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

    state_counts = {
        state: sum(row["state"] == state for row in rows)
        for state in ("PLANNED", "STALE", "BLOCKED")
    }
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
            "unique_check_ids": len(seen) == len(rows),
            "network_disabled_for_all": all(row.get("network_allowed") is False for row in rows),
            "read_only_mount_for_all": all(row.get("mount_mode") == "read_only" for row in rows),
            "no_execution_claimed": True,
        },
        "limitations": [
            "this artifact plans test/lint/type/build/security/benchmark checks but does not execute them",
            "PASS/FAIL execution receipts remain unavailable until an approved isolated execution adapter is connected",
            "a valid plan is not evidence that the declared command passes",
        ],
        "falsification_criteria": [
            "block the suite plan when any required check cannot produce a current valid read-only plan",
            "reject duplicate check identities",
            "reject coding checks that request network access or writable repository mounts",
            "never translate PLANNED into PASS without executable evidence",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
