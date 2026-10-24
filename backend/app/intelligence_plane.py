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
                "state": "CANDIDATE",
                "contract": "model-runtime-contract/0.x",
                "notes": "Mock and Anthropic adapters exist behind a common response contract. Deterministic routing-policy receipts now prevent implicit live-provider escalation; broader provider coverage and executed routing certification remain open.",
            },
            "agent_runtime": {
                "state": "PARTIAL",
                "contract": None,
                "notes": "Planning/execution gates, conversations, bounded natural-language intent planning, approval-gated tools, skills, local memory and coding lifecycle primitives exist; provider-native Anthropic/OpenAI-compatible tool loops now persist message history and terminal tool-result continuation under bounded turns, while subagents, fleet scheduling and crash-resume semantics beyond the run state machine remain open.",
            },
            "tool_runtime": {
                "state": "AVAILABLE",
                "contract": "mucaro-tool-runtime/local",
                "notes": "Deny-by-default approval-gated local tool runtime with execution receipts.",
            },
            "skill_registry": {
                "state": "CANDIDATE",
                "contract": "federation-skill-contract/1.0",
                "notes": f"Local immutable registry foundation with {len(registered_skills)} registered skill(s).",
            },
            "skill_mount_runtime": {
                "state": "CANDIDATE",
                "contract": "skill-mount-manifest/1.0",
                "notes": "Local mount, capability authorization, activation, execution, ledger and execution-routing foundation.",
            },
            "execution_router": {
                "state": "CANDIDATE",
                "contract": "mucaro-execution-router/1.x",
                "notes": "Policy-driven IN_PROCESS, LOCAL_WORKER and CONTAINER routing exists with pinned container bindings. Distributed service/federation-worker execution and trusted live container certification remain open.",
            },
            "cited_retrieval": {
                "state": "AVAILABLE",
                "contract": "mucaro-retrieval/local",
                "notes": "Local cited retrieval with BM25, persistent TF-IDF, semantic/hybrid strategies, mixed-format regression fixtures, retrieval-quality receipts, an exact-denominator real-world-corpus certification gate, and a deterministic real-world corpus materializer. Qualifying nonsynthetic source bytes/provenance, bound evaluation evidence and production-scale stress proof remain open.",
            },
            "memory_context": {
                "state": "CANDIDATE",
                "contract": "mucaro-memory-selection/1.0",
                "notes": "Operator-controlled local memory supports explicit enable/disable/delete/export. A deterministic selector now bounds prompt context, filters kinds, excludes disabled rows and deduplicates normalized content; richer durable provenance and conflict/version semantics remain open.",
            },
            "advanced_gis_processing": {
                "state": "CANDIDATE",
                "contract": "mucaro-gis-processing/0.x",
                "notes": "Topology inspection/repair, spatial classification/join/overlay, pyproj CRS transformation, WGS84 ellipsoidal measurement, vector-format manifests, GeoTIFF inspection/sampling/clipping, map/export provenance, vector-raster analytical receipts and independent control fixtures are source-backed. Broad live regression, vertical/time-dependent CRS cases and rendered map certification remain open.",
            },
            "state_of_art_ocr_analysis": {
                "state": "CANDIDATE",
                "contract": "mucaro-ocr-analysis/0.x",
                "notes": "Image OCR receipts preserve source hashes, engine identity, raw/normalized/canonical text, confidence states and source-coordinate spans; PDF rendering/page lineage, layout/confidence receipts, a frozen 12-case denominator, and an exact-byte corpus evidence ledger are source-backed. Table/form/handwriting support, difficult-scan accuracy and real executed corpus evidence remain open.",
            },
            "governed_web_browsing": {
                "state": "CANDIDATE",
                "contract": "mucaro-web-browsing/0.x",
                "notes": "Approval-gated governed fetch/sessions, byte-pinned snapshots, robots/cache evidence, static-HTML discovery, bounded autonomous traversal, credential-last web search, and rendered-browser DOM/screenshot receipts are source-backed. WEB-08/09 remain unpromoted until credentialed search and live Playwright/Chromium corpus evidence close.",
            },
            "coding_programming_expertise": {
                "state": "CANDIDATE",
                "contract": "mucaro-coding/1.x",
                "notes": "Governed repository discovery/read/diff, patch validation and controlled mutation, isolated coding-container execution receipts, line-bound review evidence, guarded branch/commit lifecycle, deterministic PR request artifacts and a fail-closed coding certification ledger are merged. Live pinned-runtime evidence, remote authenticated push/PR and zero-residue corpus execution remain open.",
            },
            "transparency_objectivity_governance": {
                "state": "CANDIDATE",
                "contract": "mucaro-evidence-governance/0.x",
                "notes": "Cited retrieval, provenance DAGs, runtime identities, release receipts, frozen denominators, append-only audit events, exact-SHA hosted-CI artifact generation and a fail-closed release-grade matrix provide the governance substrate; exact-current-main external evidence and complete cross-capability release closure remain open.",
            },
            "cross_repo_orchestration": {
                "state": "ABSENT",
                "contract": None,
                "notes": "Intentionally deferred until federation contracts and authenticated remote execution boundaries are available.",
            },
        },
    }
