"""Build a source-bound dependency backlog from a MUCARO_100 closure register.

Exact dependency edges come only from explicit ledger fields. Shared files and
thematic workstreams are *candidate* affinities, never certified dependencies.
This tool does not update scores, source ledgers, or external evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WORKSTREAMS = {
    "BC": "01-SECURITY-AND-EXECUTION",
    "CODE": "02-CODING-AND-CLI",
    "CLI": "02-CODING-AND-CLI",
    "PR": "02-CODING-AND-CLI",
    "RT": "03-RETRIEVAL-GIS-OCR-WEB",
    "GIS": "03-RETRIEVAL-GIS-OCR-WEB",
    "OCR": "03-RETRIEVAL-GIS-OCR-WEB",
    "WEB": "03-RETRIEVAL-GIS-OCR-WEB",
    "MOB": "04-FRONTEND-AND-MOBILE",
    "PKG": "05-PACKAGING",
    "GOV": "06-GOVERNANCE-AND-RELEASE",
    "SD": "01-SECURITY-AND-EXECUTION",
    "KERNEL": "01-SECURITY-AND-EXECUTION",
    "INV": "02-CODING-AND-CLI",
    "TGT": "07-RELEASE-ACCEPTANCE",
    "MATRIX": "07-RELEASE-ACCEPTANCE",
    "GOAL": "07-RELEASE-ACCEPTANCE",
    "EXEC": "07-RELEASE-ACCEPTANCE",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_rows(rows: Any, key: str, family: str) -> dict[str, dict[str, Any]]:
    require(isinstance(rows, list), f"{family}: records must be a list")
    seen = {}
    for row in rows:
        require(isinstance(row, dict), f"{family}: record must be object")
        ident = row.get(key)
        require(isinstance(ident, str) and bool(ident), f"{family}: missing identity")
        require(ident not in seen, f"{family}: duplicate identity {ident}")
        seen[ident] = row
    return seen


def plan(register: dict[str, Any], source_hash: str) -> dict[str, Any]:
    cap = register["capability_event_audit"]
    rel = register["release_acceptance"]
    caps = unique_rows(cap["requirements"], "requirement_id", "capabilities")
    releases = unique_rows(rel["requirements"], "requirement_id", "release")
    blockers = unique_rows(register["blocker_ledger"], "blocker_id", "blockers")
    require(len(caps) == 100 and len(releases) == 139 and len(blockers) == 6,
            "canonical denominators differ from 100/139/6")
    cap_open = {i: x for i, x in caps.items() if x["projected_state"] != "PASS"}
    rel_open = {i: x for i, x in releases.items()
                if x["scope_state"] != "NOT_APPLICABLE" and x["state"] != "PASS"}
    require(len(cap_open) == cap["non_pass_count"], "capability arithmetic mismatch")
    require(len(rel_open) == rel["non_pass_applicable_count"], "release arithmetic mismatch")
    require(sum(x["state"] == "NOT_APPLICABLE" for x in releases.values()) == 1,
            "expected one release NOT_APPLICABLE")

    tasks = []
    groups = defaultdict(list)
    for family, collection in (("capability", cap_open), ("release", rel_open)):
        for ident, row in sorted(collection.items()):
            prefix = ident.split("-")[0]
            workstream = WORKSTREAMS.get(prefix, "08-REVIEW-UNCLASSIFIED")
            blocker_id = row.get("blocker_id") if family == "release" else None
            if blocker_id:
                require(blocker_id in blockers, f"{ident}: references missing blocker {blocker_id}")
            raw_deps = row.get("dependencies") if family == "release" else []
            dependencies = raw_deps if isinstance(raw_deps, list) else []
            evidence = row.get("evidence_refs", []) if family == "release" else [
                ev.get("evidence_refs", []) for ev in row.get("events", [])
            ]
            task = {
                "requirement_id": ident,
                "family": family,
                "state": row["projected_state"] if family == "capability" else row["state"],
                "workstream": workstream,
                "blocker_id": blocker_id,
                "declared_dependencies": dependencies,
                "dag_node": row.get("dag_node") if family == "release" else None,
                "positive_gate": row.get("positive_gate") if family == "release" else None,
                "negative_gate": row.get("negative_gate") if family == "release" else None,
                "implementation_refs": row.get("implementation_refs") if family == "release" else None,
                "evidence_refs": evidence,
                "evidence_authentication": "NOT_ASSESSED",
                "workstream_assignment": "HEURISTIC_GROUPING_NOT_AUTHORITY",
            }
            tasks.append(task)
            groups[workstream].append(ident)

    # Expose joins only where *declared* release dependencies refer to existing
    # requirement IDs or DAG nodes. All remaining entries stay unresolved.
    all_ids = set(caps) | set(releases)
    dag_nodes = {x.get("dag_node") for x in releases.values() if x.get("dag_node")}
    edges, unresolved = [], []
    for task in tasks:
        for dep in task["declared_dependencies"]:
            if isinstance(dep, str) and dep in all_ids:
                edges.append({"from": dep, "to": task["requirement_id"], "kind": "DECLARED_ID"})
            elif isinstance(dep, str) and dep in dag_nodes:
                edges.append({"from": dep, "to": task["requirement_id"], "kind": "DECLARED_DAG_NODE"})
            else:
                unresolved.append({"to": task["requirement_id"], "raw_dependency": dep,
                                   "resolution": "REVIEW_REQUIRED"})
    return {
        "schema_version": "MUCARO_100_DEPENDENCY_BACKLOG/1.0",
        "artifact_class": "DERIVED_PLANNING_NOT_CERTIFICATION",
        "register_sha256": source_hash,
        "register_source": register.get("source"),
        "counts": {
            "capability_non_pass": len(cap_open),
            "release_non_pass": len(rel_open),
            "blocker_records": len(blockers),
            "task_occurrences": len(tasks),
            "workstreams": dict(sorted(Counter(t["workstream"] for t in tasks).items())),
        },
        "groups": [{"name": name, "requirement_ids": sorted(ids)}
                   for name, ids in sorted(groups.items())],
        "tasks": tasks,
        "declared_edges": sorted(edges, key=lambda x: (x["to"], x["from"])),
        "unresolved_dependency_references": unresolved,
        "blockers": list(blockers.values()),
        "policy": {
            "no_score_changes": True,
            "no_inferred_edges_as_verified_dependencies": True,
            "no_dependency_omission": True,
            "manual_adjudication_required_for_unknown_references": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    register_path = args.register.resolve()
    output_path = args.output.resolve()
    require(register_path != output_path, "cannot overwrite input register")
    raw = register_path.read_bytes()
    require(not output_path.is_relative_to(Path(__file__).resolve().parents[1]),
            "output must be outside source worktree")
    report = plan(json.loads(raw), hashlib.sha256(raw).hexdigest())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print("DEPENDENCY_BACKLOG_WRITTEN", output_path)
    print("SHA256", hashlib.sha256(output_path.read_bytes()).hexdigest())
    print("CAPABILITY_NON_PASS", report["counts"]["capability_non_pass"])
    print("RELEASE_NON_PASS", report["counts"]["release_non_pass"])
    print("DECLARED_EDGES", len(report["declared_edges"]))
    print("UNRESOLVED_REFERENCES", len(report["unresolved_dependency_references"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
