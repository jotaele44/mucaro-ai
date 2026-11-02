"""Derive a fail-closed, event-aware MUCARO_100 closure register.

This is an analysis artifact. It does not award certification credit, mutate
canonical ledgers, or claim external evidence has been independently verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.denominator_audit import (
    audit_current_denominator_state,
    load_denominator_events,
)
from backend.app.program_denominator import load_denominator


def _load(relative: str) -> dict[str, Any]:
    payload = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{relative}: expected an object")
    return payload


def _index(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError(f"{label}: non-object record")
        key = row.get(field)
        if not isinstance(key, str) or not key or key in result:
            raise ValueError(f"{label}: absent or duplicate {field}: {key!r}")
        result[key] = row
    return result


def _git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def build_register() -> dict[str, Any]:
    frozen = load_denominator(ROOT)
    projection = audit_current_denominator_state(ROOT)
    ledger = load_denominator_events(ROOT)
    release = _load("_architecture/release/acceptance_ledger_v1.json")
    scope = _load("_architecture/release/release_scope_manifest_v1.json")
    blockers = _load("_architecture/release/blocker_ledger_v1.json")

    if projection.get("status") != "MUCARO_V1_DENOMINATOR_AUDIT_OK" or projection.get("failures"):
        raise ValueError("capability event projection failed")
    f = _index(frozen["requirements"], "id", "frozen capabilities")
    p = _index(projection["requirements"], "id", "projected capabilities")
    r = _index(release["rows"], "requirement_id", "release rows")
    s = _index(scope["requirements"], "requirement_id", "scope rows")
    b = _index(blockers["blockers"], "blocker_id", "blockers")
    events = _index(ledger["events"], "event_id", "events")

    if len(f) != 100 or set(f) != set(p):
        raise ValueError("100-row capability identity is not conserved")
    if len(r) != 139 or set(r) != set(s):
        raise ValueError("139-row release identity is not conserved")
    if len(b) != 6:
        raise ValueError("blocker universe differs from frozen six")
    if len(events) != len(projection.get("applied_events", [])):
        raise ValueError("not every event was applied")
    if {x["event_id"] for x in projection["applied_events"]} != set(events):
        raise ValueError("applied event identities do not match source")
    na_ids = {key for key, value in r.items() if value.get("state") == "NOT_APPLICABLE"}
    if len(na_ids) != 1 or any(r[key].get("scope_state") != "NOT_APPLICABLE" for key in na_ids):
        raise ValueError("release NOT_APPLICABLE denominator is not justified")
    if any(value.get("scope_state") != s[key].get("scope_state") for key, value in r.items()):
        raise ValueError("release/manifest scope_state mismatch")

    by_requirement: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in ledger["events"]:
        by_requirement[event["requirement_id"]].append(event)

    capability_rows = []
    for key in sorted(f):
        current = p[key]
        history = by_requirement.get(key, [])
        capability_rows.append({
            "requirement_id": key,
            "title": current.get("title"),
            "track": current.get("track"),
            "frozen_state": f[key]["state"],
            "projected_state": current["state"],
            "events": [{
                "event_id": x["event_id"],
                "from_state": x["from_state"],
                "to_state": x["to_state"],
                "evidence_type": x.get("evidence_type"),
                "evidence_refs": x["evidence"],
                "reason": x["reason"],
                "independent_evidence_verification": "NOT_ASSESSED",
            } for x in history],
        })

    release_rows = []
    for key in sorted(r):
        row = r[key]
        spec = s[key]
        release_rows.append({
            "requirement_id": key,
            "state": row["state"],
            "scope_state": row["scope_state"],
            "requirement_text": row["requirement_text"],
            "verification_method": spec.get("verification_method"),
            "source": spec.get("source"),
            "crosswalk": spec.get("crosswalk", []),
            "implementation_state": row.get("implementation_state"),
            "implementation_refs": row.get("implementation_refs"),
            "evidence_refs": row.get("evidence_refs"),
            "positive_gate": row.get("positive_gate"),
            "negative_gate": row.get("negative_gate"),
            "dependencies": row.get("dependencies"),
            "blocker_id": row.get("blocker_id"),
            "dag_node": row.get("dag_node"),
            "evidence_verification": "NOT_ASSESSED",
        })

    cap_states = Counter(x["projected_state"] for x in capability_rows)
    rel_states = Counter(x["state"] for x in release_rows)
    if sum(cap_states.values()) != 100 or sum(rel_states.values()) != 139:
        raise ValueError("state arithmetic does not close")

    return {
        "schema_version": "MUCARO_100_CLOSURE_REGISTER/1.0",
        "artifact_class": "DERIVED_ANALYSIS_NOT_CERTIFICATION",
        "source": {
            "repository": "jotaele44/mucaro-ai",
            "commit": _git("rev-parse", "HEAD"),
            "tree": _git("rev-parse", "HEAD^{tree}"),
            "branch": _git("branch", "--show-current"),
            "worktree_porcelain": _git("status", "--porcelain=v1", "-uall"),
        },
        "capability_event_audit": {
            "status": projection["status"],
            "audit_head": ledger.get("audit_head"),
            "applied_event_count": len(events),
            "counts": dict(sorted(cap_states.items())),
            "non_pass_count": 100 - cap_states.get("PASS", 0),
            "requirements": capability_rows,
        },
        "release_acceptance": {
            "total": 139,
            "applicable": 138,
            "not_applicable": 1,
            "counts": dict(sorted(rel_states.items())),
            "non_pass_applicable_count": sum(
                x["state"] != "PASS" and x["scope_state"] != "NOT_APPLICABLE"
                for x in release_rows
            ),
            "requirements": release_rows,
        },
        "blocker_ledger": blockers["blockers"],
        "rules": [
            "Frozen and current capability states remain separately attributable",
            "Event evidence references do not constitute independent authentication",
            "PASS requires acceptance by canonical governed validation",
            "Source/backend/GUI/package/native evidence are not interchangeable",
            "The score denominators are never changed by this report",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True, help="File outside the project worktree")
    args = ap.parse_args()
    target = args.output.resolve()
    if target.is_relative_to(ROOT.resolve()):
        ap.error("output must be outside the Mucaro source worktree")
    report = build_register()
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print("CLOSURE_REGISTER_V1_WRITTEN", target)
    print("SHA256", digest)
    print("CAPABILITY_NON_PASS", report["capability_event_audit"]["non_pass_count"])
    print("RELEASE_NON_PASS_APPLICABLE", report["release_acceptance"]["non_pass_applicable_count"])
    print("BLOCKER_RECORDS", len(report["blocker_ledger"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
