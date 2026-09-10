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
                "state": "ABSENT",
                "contract": None,
                "notes": "GIS processing, spatial provenance, CRS preservation, and geometry regression gates are planned but not implemented.",
            },
            "state_of_art_ocr_analysis": {
                "state": "ABSENT",
                "contract": None,
                "notes": "OCR extraction, layout analysis, confidence calibration, and OCR fixtures are planned but not implemented.",
            },
            "governed_web_browsing": {
                "state": "CANDIDATE",
                "contract": "mucaro-web-browsing/0.x",
                "notes": "Approval-gated web.fetch can create byte-pinned snapshots that can be ingested and cited with HTML parser traces, non-text classification, and explicit HTTP error-page citation policy; PDF/deeper parser depth, cache behavior, redirect-target robots completeness, browser discovery, and session policy remain open.",
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
