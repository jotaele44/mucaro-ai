from __future__ import annotations

from typing import Any

from .skill_runtime import list_skills

REPO_ID = "mucaro-ai"
MEMBERSHIP_STATUS = "PROVISIONAL"
ROLES = ["INTELLIGENCE_PLANE_CANDIDATE"]


def build_intelligence_plane_descriptor() -> dict[str, Any]:
    registered_skills = list_skills()["skills"]
    return {
        "schema": "repo-capability-contract/1.0",
        "repo_id": REPO_ID,
        "membership_status": MEMBERSHIP_STATUS,
        "roles": ROLES,
        "baseline_required": False,
        "lockstep_participant": True,
        "capabilities": {
            "model_runtime": {
                "state": "PARTIAL",
                "contract": "model-runtime-contract/0.x",
                "notes": "Current router supports mock and Anthropic; provider-neutral runtime remains open.",
            },
            "agent_runtime": {
                "state": "PARTIAL",
                "contract": None,
                "notes": "Planning/execution gate and conversations exist; durable workflow DAG orchestration remains open.",
            },
            "tool_runtime": {
                "state": "AVAILABLE",
                "contract": "mucaro-tool-runtime/local",
                "notes": "Deny-by-default approval-gated local tool runtime.",
            },
            "skill_registry": {
                "state": "CANDIDATE",
                "contract": "federation-skill-contract/1.0",
                "notes": f"Local immutable registry foundation with {len(registered_skills)} registered skill(s).",
            },
            "skill_mount_runtime": {
                "state": "CANDIDATE",
                "contract": "skill-mount-manifest/1.0",
                "notes": "Local mount, capability authorization, activation, execution, and ledger foundation.",
            },
            "execution_router": {
                "state": "ABSENT",
                "contract": None,
                "notes": "Distributed repo/container/service/federation-worker routing is not implemented.",
            },
            "cited_retrieval": {
                "state": "AVAILABLE",
                "contract": "mucaro-retrieval/local",
                "notes": "Local cited retrieval with BM25, persistent TF-IDF, and semantic strategies.",
            },
            "advanced_gis_processing": {
                "state": "CANDIDATE",
                "contract": "mucaro-gis-processing/0.x",
                "notes": "Local GeoJSON inspection, point-in-polygon, comparison, limited EPSG:4326/EPSG:3857 reprojection, CSV point-to-GeoJSON, and bbox feature-filter receipts exist with CRS preservation, geometry state counts, coordinate dimensionality, topology-style equality, retained/excluded feature counts, vertex Hausdorff distance, vertex symmetric-difference, adapter, source-header, skipped-row, row-accounting, and output-hash evidence; robust topology, broader CRS/datum/grid-shift/axis-order support, planar overlay, geometry clipping, map export, broader spatial formats, raster/vector processing, and GIS eval corpus remain open.",
            },
            "state_of_art_ocr_analysis": {
                "state": "CANDIDATE",
                "contract": "mucaro-ocr-analysis/0.x",
                "notes": "Local image OCR receipts exist with source-image hashes, engine identity, raw/normalized/canonical text separation, confidence states, and source-coordinate spans. PDF/page rendering, layout calibration, handwriting/table/form support, optional provider policy, and broader OCR eval corpus remain open.",
            },
            "governed_web_browsing": {
                "state": "CANDIDATE",
                "contract": "mucaro-web-browsing/0.x",
                "notes": "Approval-gated web.fetch can create byte-pinned snapshots, and web.fetch_session records operator-supplied multi-page source selection with replay receipts. Snapshots can be ingested and cited with HTML body/structure traces, PDF/non-text parser traces, immutable snapshot-only cache policy, redirect-target robots evidence, and explicit HTTP error-page citation policy; automated search/discovery, rendered-browser capture, and autonomous link-following remain open.",
            },
            "coding_programming_expertise": {
                "state": "ABSENT",
                "contract": None,
                "notes": "Governed code reading, patching, testing, and review output are planned as a future capability boundary.",
            },
            "transparency_objectivity_governance": {
                "state": "CANDIDATE",
                "contract": "mucaro-evidence-governance/0.x",
                "notes": "Cited retrieval, release receipts, and audits provide a foundation; cross-capability uncertainty and contradiction rules remain open.",
            },
            "cross_repo_orchestration": {
                "state": "ABSENT",
                "contract": None,
                "notes": "Intentionally deferred until federation contracts are available.",
            },
        },
    }
