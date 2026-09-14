from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

DENOMINATOR_VERSION = "MUCARO_V1_DENOMINATOR/1.0"
SCHEMA_VERSION = "mucaro_v1_denominator/1.0"
FROZEN_REQUIREMENT_COUNT = 100
FROZEN_TRACK_COUNT = 10
ROWS_PER_TRACK = 10
BASELINE_RAW_COMPLETION_PERCENT = 61.5
BASELINE_WEIGHTED_COMPLETION_PERCENT = 62.6
BASELINE_SOURCE_HEAD = "9a06357afa9e34367776b1f46223a803ab23c958"
FREEZE_PARENT_HEAD = "6ef58a942618a2411696e9e6c88920748f5f31bb"

ALLOWED_STATES = {"PASS", "PROVISIONAL", "OPEN", "BLOCKED", "FAIL", "UNVERIFIED"}
SCORE_RULE = {"PASS": 1.0, "PROVISIONAL": 0.5, "OPEN": 0.0, "BLOCKED": 0.0, "FAIL": 0.0, "UNVERIFIED": 0.0}
TRACK_WEIGHTS_PERCENT = {
    "backend_capability_contracts": 12, "cli_workflows": 10, "provenance": 12,
    "retrieval": 10, "gis": 12, "ocr": 8, "browsing": 10, "coding": 10,
    "packaging": 8, "hosted_governance": 8,
}

_ROWS: list[tuple[str, str, str, str]] = [
    ("BC-01", "backend_capability_contracts", "Shared advanced-capability contract model", "PASS"),
    ("BC-02", "backend_capability_contracts", "Immutable Skill Registry and deterministic skill identity", "PASS"),
    ("BC-03", "backend_capability_contracts", "Mount-authorize-activate-execute lifecycle", "PASS"),
    ("BC-04", "backend_capability_contracts", "Persistent execution ledger", "PASS"),
    ("BC-05", "backend_capability_contracts", "Provider-neutral model runtime contract", "PASS"),
    ("BC-06", "backend_capability_contracts", "Fail-closed capability allowlist", "PASS"),
    ("BC-07", "backend_capability_contracts", "General capability policy engine", "OPEN"),
    ("BC-08", "backend_capability_contracts", "Isolated local-worker runtime", "OPEN"),
    ("BC-09", "backend_capability_contracts", "Container runtime and deterministic execution router", "OPEN"),
    ("BC-10", "backend_capability_contracts", "Cross-capability output validator", "PROVISIONAL"),
    ("CLI-01", "cli_workflows", "File and source ingestion", "PASS"),
    ("CLI-02", "cli_workflows", "Ask/query with retrieval strategy selection", "PASS"),
    ("CLI-03", "cli_workflows", "Source and index inspection and maintenance", "PASS"),
    ("CLI-04", "cli_workflows", "Conversation and run-history management", "PASS"),
    ("CLI-05", "cli_workflows", "Explicit local memory controls", "PASS"),
    ("CLI-06", "cli_workflows", "Approval-gated tool lifecycle", "PASS"),
    ("CLI-07", "cli_workflows", "Release certification and receipt inspection", "PASS"),
    ("CLI-08", "cli_workflows", "Advanced GIS OCR and web operator surfaces", "PASS"),
    ("CLI-09", "cli_workflows", "Governed coding-agent workflow", "OPEN"),
    ("CLI-10", "cli_workflows", "Complete GUI-to-CLI/backend behavioral parity", "PROVISIONAL"),
    ("PR-01", "provenance", "Raw source identity and SHA-256", "PASS"),
    ("PR-02", "provenance", "Source and chunk stable identifiers", "PASS"),
    ("PR-03", "provenance", "Retrieval-to-citation answer trace", "PASS"),
    ("PR-04", "provenance", "Web byte snapshots and retrieval UTC", "PASS"),
    ("PR-05", "provenance", "GIS operation and output receipts", "PASS"),
    ("PR-06", "provenance", "OCR source-image and engine provenance", "PASS"),
    ("PR-07", "provenance", "Package and member hash manifests", "PASS"),
    ("PR-08", "provenance", "Exact-head release and handoff evidence", "PASS"),
    ("PR-09", "provenance", "Unified cross-capability artifact lineage DAG", "PROVISIONAL"),
    ("PR-10", "provenance", "Runtime and toolchain identity bound to every derivation", "OPEN"),
    ("RT-01", "retrieval", "Hybrid retrieval", "PASS"),
    ("RT-02", "retrieval", "BM25 retrieval", "PASS"),
    ("RT-03", "retrieval", "Persistent local TF-IDF retrieval", "PASS"),
    ("RT-04", "retrieval", "Semantic retrieval", "PASS"),
    ("RT-05", "retrieval", "Missing or stale store fail-closed behavior", "PASS"),
    ("RT-06", "retrieval", "Citation rank and source traceability", "PASS"),
    ("RT-07", "retrieval", "Precision recall and no-context evaluation", "PASS"),
    ("RT-08", "retrieval", "Mixed-format and distractor evaluation", "PASS"),
    ("RT-09", "retrieval", "Frozen representative real-world large corpus", "OPEN"),
    ("RT-10", "retrieval", "Production-scale retrieval stress and scalability proof", "PROVISIONAL"),
    ("GIS-01", "gis", "Geometry and topology inspection", "PASS"),
    ("GIS-02", "gis", "Point-in-polygon and spatial classification", "PASS"),
    ("GIS-03", "gis", "Spatial join with accounting", "PASS"),
    ("GIS-04", "gis", "Geometry comparison and overlay metrics", "PASS"),
    ("GIS-05", "gis", "Standalone polygon overlay with receipts", "PASS"),
    ("GIS-06", "gis", "CRS transformation framework", "PROVISIONAL"),
    ("GIS-07", "gis", "Planar and geodesic measurements", "PROVISIONAL"),
    ("GIS-08", "gis", "General clipping filtering and conversion workflow", "PROVISIONAL"),
    ("GIS-09", "gis", "Robust topology repair and broad vector/raster operations", "OPEN"),
    ("GIS-10", "gis", "Broad CRS datum independent-engine and map/export certification", "OPEN"),
    ("OCR-01", "ocr", "Local image OCR execution", "PASS"),
    ("OCR-02", "ocr", "Raw normalized canonical bbox and confidence receipt validator", "PASS"),
    ("OCR-03", "ocr", "Reliable confidence thresholding", "PROVISIONAL"),
    ("OCR-04", "ocr", "Bounding-box and layout evidence", "PROVISIONAL"),
    ("OCR-05", "ocr", "Multilingual and accent handling", "PROVISIONAL"),
    ("OCR-06", "ocr", "Negative no-text and low-confidence handling", "PROVISIONAL"),
    ("OCR-07", "ocr", "PDF page rendering and page lineage", "OPEN"),
    ("OCR-08", "ocr", "Multi-column table and form reconstruction", "OPEN"),
    ("OCR-09", "ocr", "Handwriting and difficult-scan coverage", "OPEN"),
    ("OCR-10", "ocr", "Frozen representative OCR certification corpus", "OPEN"),
    ("WEB-01", "browsing", "URL policy and governed fetch", "PASS"),
    ("WEB-02", "browsing", "Frozen byte snapshots", "PASS"),
    ("WEB-03", "browsing", "Redirect parser and error-page evidence", "PASS"),
    ("WEB-04", "browsing", "Citation bound to fetched content", "PASS"),
    ("WEB-05", "browsing", "Robots and cache policy", "PROVISIONAL"),
    ("WEB-06", "browsing", "Multi-page governed session receipts", "PROVISIONAL"),
    ("WEB-07", "browsing", "Non-text and PDF evidence path", "PROVISIONAL"),
    ("WEB-08", "browsing", "Automated search and discovery", "OPEN"),
    ("WEB-09", "browsing", "Rendered and JavaScript browser interaction", "OPEN"),
    ("WEB-10", "browsing", "Autonomous bounded link-following and exhaustion accounting", "OPEN"),
    ("CODE-01", "coding", "Formal coding capability contract", "PASS"),
    ("CODE-02", "coding", "Generic approval and tool safety substrate", "PROVISIONAL"),
    ("CODE-03", "coding", "Repository read and discovery runtime", "OPEN"),
    ("CODE-04", "coding", "Workspace and dirty-state protection", "OPEN"),
    ("CODE-05", "coding", "Patch proposal and diff receipts", "OPEN"),
    ("CODE-06", "coding", "Sandboxed mutation runtime", "OPEN"),
    ("CODE-07", "coding", "Test lint type and build execution receipts", "OPEN"),
    ("CODE-08", "coding", "Code-review evidence with file and line binding", "OPEN"),
    ("CODE-09", "coding", "Branch commit and PR safety lifecycle", "OPEN"),
    ("CODE-10", "coding", "Coding regression and certification corpus", "OPEN"),
    ("PKG-01", "packaging", "Governed source-release artifact", "PASS"),
    ("PKG-02", "packaging", "Archive member SHA-256 manifest", "PASS"),
    ("PKG-03", "packaging", "Wheel and sdist build path", "PASS"),
    ("PKG-04", "packaging", "Fresh-clone certification", "PASS"),
    ("PKG-05", "packaging", "Source-bound macOS application", "PASS"),
    ("PKG-06", "packaging", "Distribution and handoff manifests", "PASS"),
    ("PKG-07", "packaging", "Portable embedded runtime", "PROVISIONAL"),
    ("PKG-08", "packaging", "Clean-machine install and reinstall workflow", "PROVISIONAL"),
    ("PKG-09", "packaging", "Signed and notarized installer", "OPEN"),
    ("PKG-10", "packaging", "Upgrade migration and rollback guarantees", "OPEN"),
    ("GOV-01", "hosted_governance", "GitHub Actions diagnostic", "PASS"),
    ("GOV-02", "hosted_governance", "Release dashboard and governance report", "PASS"),
    ("GOV-03", "hosted_governance", "Support bundle and incident packet", "PASS"),
    ("GOV-04", "hosted_governance", "Exact-head hosted evidence classification", "PASS"),
    ("GOV-05", "hosted_governance", "Repository Actions policy verified", "PROVISIONAL"),
    ("GOV-06", "hosted_governance", "Workflow registration and token permissions", "PROVISIONAL"),
    ("GOV-07", "hosted_governance", "Current-head CI run admission and PASS", "BLOCKED"),
    ("GOV-08", "hosted_governance", "Startup probe admission and PASS", "BLOCKED"),
    ("GOV-09", "hosted_governance", "Billing/minutes and branch-protection enforcement", "OPEN"),
    ("GOV-10", "hosted_governance", "Protected exact-head tagged governed release", "OPEN"),
]

DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "BC-07": ("BC-06",), "BC-08": ("BC-07", "PR-09", "PR-10"), "BC-09": ("BC-07", "BC-08", "PR-10"), "BC-10": ("BC-09",),
    "CLI-09": ("CODE-03", "CODE-06", "CODE-07"), "CLI-10": ("BC-10", "CODE-10", "PKG-08"),
    "PR-09": ("PR-01", "PR-05", "PR-06"), "PR-10": ("PR-09", "BC-07"), "RT-09": ("RT-07", "RT-08"), "RT-10": ("RT-09",),
    "GIS-09": ("GIS-06", "GIS-07", "GIS-08"), "GIS-10": ("GIS-09",), "OCR-07": ("OCR-02",), "OCR-08": ("OCR-07",), "OCR-09": ("OCR-07",), "OCR-10": ("OCR-03", "OCR-08", "OCR-09"),
    "WEB-08": ("WEB-01", "WEB-04"), "WEB-09": ("WEB-01", "WEB-03"), "WEB-10": ("WEB-05", "WEB-06", "WEB-08", "WEB-09"),
    "CODE-02": ("BC-07",), "CODE-03": ("BC-07", "PR-09"), "CODE-04": ("CODE-03",), "CODE-05": ("CODE-03",), "CODE-06": ("BC-08", "BC-09", "CODE-04", "CODE-05"),
    "CODE-07": ("CODE-06",), "CODE-08": ("CODE-07",), "CODE-09": ("CODE-08",), "CODE-10": ("CODE-09", "BC-10"),
    "PKG-07": ("BC-09",), "PKG-08": ("PKG-07",), "PKG-09": ("PKG-08",), "PKG-10": ("PKG-08",),
    "GOV-07": ("GOV-05", "GOV-06"), "GOV-08": ("GOV-05", "GOV-06"), "GOV-09": ("GOV-01",), "GOV-10": ("GOV-07", "GOV-08", "GOV-09", "PKG-09", "PKG-10"),
}


def build_v1_denominator() -> dict[str, Any]:
    requirements = [{"id": rid, "track": track, "requirement": requirement, "state": state, "dependencies": list(DEPENDENCIES.get(rid, ()))} for rid, track, requirement, state in _ROWS]
    return {"schema_version": SCHEMA_VERSION, "denominator_version": DENOMINATOR_VERSION, "program": "Mucaro AI V1", "frozen_requirement_count": FROZEN_REQUIREMENT_COUNT, "frozen_track_count": FROZEN_TRACK_COUNT, "rows_per_track": ROWS_PER_TRACK, "baseline": {"raw_completion_percent": BASELINE_RAW_COMPLETION_PERCENT, "weighted_completion_percent": BASELINE_WEIGHTED_COMPLETION_PERCENT, "source_baseline_head": BASELINE_SOURCE_HEAD, "freeze_parent_head": FREEZE_PARENT_HEAD}, "score_rule": dict(SCORE_RULE), "track_weights_percent": dict(TRACK_WEIGHTS_PERCENT), "allowed_states": sorted(ALLOWED_STATES), "requirements": requirements}


def validate_v1_denominator(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = deepcopy(payload if payload is not None else build_v1_denominator())
    rows = payload.get("requirements") if isinstance(payload, dict) else None
    failures: list[str] = []
    if payload.get("denominator_version") != DENOMINATOR_VERSION: failures.append("denominator_version")
    if not isinstance(rows, list): rows = []; failures.append("requirements_array")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    tracks = [row.get("track") for row in rows if isinstance(row, dict)]
    states = [row.get("state") for row in rows if isinstance(row, dict)]
    if len(rows) != FROZEN_REQUIREMENT_COUNT: failures.append("requirement_count")
    if len(set(ids)) != FROZEN_REQUIREMENT_COUNT: failures.append("requirement_id_uniqueness")
    if set(ids) != {row[0] for row in _ROWS}: failures.append("requirement_membership")
    track_counts = Counter(tracks)
    if set(track_counts) != set(TRACK_WEIGHTS_PERCENT): failures.append("track_membership")
    if any(count != ROWS_PER_TRACK for count in track_counts.values()): failures.append("rows_per_track")
    if len(track_counts) != FROZEN_TRACK_COUNT: failures.append("track_count")
    if any(state not in ALLOWED_STATES for state in states): failures.append("allowed_states")
    if sum(TRACK_WEIGHTS_PERCENT.values()) != 100: failures.append("track_weight_sum")
    valid_ids = set(ids)
    unknown_dependencies = sorted({dep for row in rows if isinstance(row, dict) for dep in row.get("dependencies", []) if dep not in valid_ids})
    if unknown_dependencies: failures.append("dependency_membership")
    return {"status": "MUCARO_V1_DENOMINATOR_OK" if not failures else "MUCARO_V1_DENOMINATOR_FAILED", "denominator_version": payload.get("denominator_version"), "requirement_count": len(rows), "track_count": len(track_counts), "track_counts": dict(sorted(track_counts.items())), "state_counts": dict(sorted(Counter(states).items())), "baseline_raw_completion_percent": BASELINE_RAW_COMPLETION_PERCENT, "baseline_weighted_completion_percent": BASELINE_WEIGHTED_COMPLETION_PERCENT, "failures": failures, "unknown_dependencies": unknown_dependencies}
