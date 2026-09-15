from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .coding_container import (
    CodingContainerError,
    build_coding_container_plan,
    execute_coding_container,
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


def run_coding_check_suite(
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
    required_failures: list[str] = []
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
                mount_mode="read_only",
                network_allowed=False,
            )
            execution = execute_coding_container(plan)
        except CodingContainerError as exc:
            execution = {
                "status": "CODING_CONTAINER_ERROR",
                "error_status": exc.status,
                "error_detail": exc.detail,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "stdout_bytes": 0,
                "stderr_bytes": 0,
                "plan_sha256": None,
                "image": image,
                "network_allowed": False,
                "mount_mode": "read_only",
            }
        passed = execution.get("status") == "CODING_CONTAINER_PASS"
        if check.required and not passed:
            required_failures.append(check.check_id)
        rows.append(
            {
                "check_id": check.check_id,
                "kind": check.kind,
                "command": list(check.command),
                "required": check.required,
                "state": "PASS" if passed else "FAIL",
                "execution": execution,
            }
        )
    payload = {
        "schema_version": CODING_CHECK_SUITE_SCHEMA_VERSION,
        "status": "CODING_CHECK_SUITE_PASS" if not required_failures else "CODING_CHECK_SUITE_FAIL",
        "image": image,
        "check_count": len(rows),
        "pass_count": sum(row["state"] == "PASS" for row in rows),
        "fail_count": sum(row["state"] == "FAIL" for row in rows),
        "required_failure_count": len(required_failures),
        "required_failures": required_failures,
        "checks": rows,
        "invariants": {
            "row_accounting_closed": len(rows) == sum(row["state"] in {"PASS", "FAIL"} for row in rows),
            "unique_check_ids": len(seen) == len(rows),
            "network_disabled_for_all": all(row["execution"].get("network_allowed") is False for row in rows),
            "read_only_mount_for_all": all(row["execution"].get("mount_mode") == "read_only" for row in rows),
        },
        "limitations": [
            "suite quality depends on the declared check commands and pinned image contents",
            "a passing command is execution evidence, not release certification by itself",
        ],
        "falsification_criteria": [
            "fail the suite when any required check fails or cannot execute",
            "reject duplicate check identities",
            "reject coding checks that request network access or writable repository mounts",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
