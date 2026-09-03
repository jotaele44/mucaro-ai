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
            "cross_repo_orchestration": {
                "state": "ABSENT",
                "contract": None,
                "notes": "Intentionally deferred until federation contracts are available.",
            },
        },
    }
