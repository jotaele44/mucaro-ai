"""Strict 100/100 certification projection over Mucaro's existing denominators.

This module intentionally does not create a third denominator. It composes:
- the frozen 100-row MUCARO_V1 capability denominator; and
- the 139-row release-engineering denominator (138 applicable + 1 justified N/A).

Only PASS receives certification credit. Historical weighted/partial scores remain
available through denominator_audit but cannot satisfy this terminal gate.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .denominator_audit import audit_current_denominator_state
from .release_scope import STATUS_CERTIFIED, compute_release_status


CAPABILITY_DENOMINATOR_TOTAL = 100
RELEASE_DENOMINATOR_TOTAL = 139
RELEASE_NOT_APPLICABLE_TOTAL = 1
RELEASE_APPLICABLE_TOTAL = 138

STATUS_MUCARO_100_CERTIFIED = "MUCARO_100_CERTIFIED"
STATUS_MUCARO_100_PROVISIONAL = "MUCARO_100_PROVISIONAL"


def _percent(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100.0, 1) if denominator else 0.0


def compute_mucaro_100(project_root: Path) -> dict[str, Any]:
    """Return the strict capability + release closure projection."""
    capability = audit_current_denominator_state(project_root)
    capability_rows = capability.get("requirements") if isinstance(capability, dict) else []
    if not isinstance(capability_rows, list):
        capability_rows = []
    capability_counts = Counter(
        str(row.get("state"))
        for row in capability_rows
        if isinstance(row, dict)
    )
    capability_ids = [
        str(row.get("id"))
        for row in capability_rows
        if isinstance(row, dict) and row.get("id")
    ]
    capability_pass = capability_counts.get("PASS", 0)
    capability_integrity = (
        capability.get("status") == "MUCARO_V1_DENOMINATOR_AUDIT_OK"
        and len(capability_rows) == CAPABILITY_DENOMINATOR_TOTAL
        and len(set(capability_ids)) == CAPABILITY_DENOMINATOR_TOTAL
    )
    capability_terminal = (
        capability_integrity
        and capability_pass == CAPABILITY_DENOMINATOR_TOTAL
        and sum(count for state, count in capability_counts.items() if state != "PASS") == 0
    )

    release = compute_release_status(project_root)
    denominator = release.get("denominator") or {}
    release_counts = denominator.get("by_state") or {}
    release_total = int(denominator.get("total") or 0)
    release_not_applicable = int(release_counts.get("NOT_APPLICABLE") or 0)
    release_applicable = release_total - release_not_applicable
    release_pass = int(release_counts.get("PASS") or 0)
    release_integrity = (
        bool(denominator.get("arithmetic_closes"))
        and release_total == RELEASE_DENOMINATOR_TOTAL
        and release_not_applicable == RELEASE_NOT_APPLICABLE_TOTAL
        and release_applicable == RELEASE_APPLICABLE_TOTAL
    )
    release_terminal = (
        release_integrity
        and release.get("status") == STATUS_CERTIFIED
        and release_pass == RELEASE_APPLICABLE_TOTAL
    )

    conditions = {
        "capability_denominator_exact": capability_integrity,
        "capability_100_of_100_pass": capability_terminal,
        "release_denominator_exact": release_integrity,
        "release_138_of_138_applicable_pass": release_terminal,
        "release_terminal_rule_certified": release.get("status") == STATUS_CERTIFIED,
    }
    certified = all(conditions.values())

    return {
        "schema_version": "MUCARO_100_SCORE/1.0",
        "status": STATUS_MUCARO_100_CERTIFIED if certified else STATUS_MUCARO_100_PROVISIONAL,
        "scoring_rule": "PASS_ONLY",
        "capability": {
            "denominator": CAPABILITY_DENOMINATOR_TOTAL,
            "pass": capability_pass,
            "strict_score_percent": _percent(capability_pass, CAPABILITY_DENOMINATOR_TOTAL),
            "state_counts": dict(sorted(capability_counts.items())),
            "integrity": capability_integrity,
        },
        "release": {
            "denominator": release_total,
            "not_applicable": release_not_applicable,
            "applicable_denominator": release_applicable,
            "pass": release_pass,
            "strict_score_percent": _percent(release_pass, release_applicable),
            "integrity": release_integrity,
            "release_status": release.get("status"),
            "residue": release.get("residue") or {},
        },
        "conditions": conditions,
        "certified": certified,
    }
