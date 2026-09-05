from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, settings

CODE_ROOT = Path(__file__).resolve().parents[2]
OBJECTIVE = (
    "Transform Mucaro AI from a validated local prototype into a governed, "
    "release-grade, local-first AI workbench with hardened CLI workflows, "
    "measurable retrieval quality, permissioned source ingestion, cited answer "
    "traceability, explicit memory controls, approved tool execution, "
    "repeatable fresh-clone certification, and a clear path toward "
    "production-quality packaging."
)


@dataclass(frozen=True)
class ProductRequirement:
    requirement_id: str
    category: str
    requirement: str
    status: str
    evidence: str
    gap: str
    next_step: str

    def as_dict(self) -> dict[str, str]:
        return {
            "requirement_id": self.requirement_id,
            "category": self.category,
            "requirement": self.requirement,
            "status": self.status,
            "evidence": self.evidence,
            "gap": self.gap,
            "next_step": self.next_step,
        }


@dataclass(frozen=True)
class ProductRoadmapStage:
    stage_id: str
    title: str
    requirement_ids: tuple[str, ...]
    evidence: str
    gap: str
    next_step: str


def _has(cfg: Settings, *parts: str) -> bool:
    return (cfg.project_root.joinpath(*parts)).exists() or (CODE_ROOT.joinpath(*parts)).exists()


def _status_from_files(cfg: Settings, files: list[tuple[str, ...]]) -> str:
    return "PASS" if all(_has(cfg, *parts) for parts in files) else "OPEN"


def build_product_audit(cfg: Settings = settings) -> dict[str, Any]:
    requirements = [
        ProductRequirement(
            "local_desktop_app",
            "assistant_surface",
            "Local app can launch without a hosted service.",
            _status_from_files(cfg, [("scripts", "launch_app.py"), ("frontend", "src", "App.jsx")]),
            "scripts/launch_app.py and frontend/src/App.jsx",
            "",
            "Keep launcher smoke in release and fresh-clone certification.",
        ),
        ProductRequirement(
            "installable_cli",
            "assistant_surface",
            "Installable CLI exposes assistant operations.",
            _status_from_files(cfg, [("pyproject.toml",), ("backend", "app", "cli.py")]),
            "pyproject.toml console script and backend/app/cli.py",
            "",
            "Continue using CLI as the automation-safe operator surface.",
        ),
        ProductRequirement(
            "permission_gated_user_directory",
            "local_file_access",
            "User-directory access requires explicit permission before ingestion.",
            _status_from_files(
                cfg,
                [
                    ("backend", "app", "permissions.py"),
                    ("frontend", "src", "components", "DirectoryIntake.jsx"),
                ],
            ),
            "backend/app/permissions.py and DirectoryIntake.jsx",
            "",
            "Keep grants local and require explicit ingest for new directories.",
        ),
        ProductRequirement(
            "document_ingestion",
            "local_file_access",
            "Assistant can ingest common local document formats.",
            _status_from_files(cfg, [("backend", "app", "parser.py"), ("backend", "app", "ingestion.py")]),
            "TXT, MD, HTML, DOCX, CSV, JSON, PDF, and metadata-only image/audio/video parsing paths",
            "Email ingestion remains open; media content understanding is metadata-only.",
            "Expand parsers without weakening source-lineage manifests.",
        ),
        ProductRequirement(
            "source_lineage_citations",
            "retrieval_grounding",
            "Answers preserve chunk IDs, source IDs, citations, and answer traces.",
            _status_from_files(
                cfg,
                [("backend", "app", "services.py"), ("backend", "app", "citations.py"), ("backend", "app", "exports.py")],
            ),
            "backend/app/services.py, citations.py, and exports.py",
            "",
            "Preserve answer-trace schema while adding retrieval strategies.",
        ),
        ProductRequirement(
            "local_retrieval_strategies",
            "retrieval_grounding",
            "Local deterministic retrieval supports multiple strategies.",
            _status_from_files(cfg, [("backend", "app", "retrieval.py"), ("backend", "app", "vector_index.py")]),
            "lexical, vector TF-IDF, BM25, hybrid, and persistent TF-IDF strategies",
            "",
            "Keep default local and deterministic until larger evals justify changes.",
        ),
        ProductRequirement(
            "semantic_vector_store",
            "retrieval_grounding",
            "Embedding-backed semantic retrieval for larger corpora.",
            _status_from_files(cfg, [("backend", "app", "semantic_store.py")]),
            "backend/app/semantic_store.py, semantic retrieval strategy, and missing/stale-store tests",
            "Current embeddings are deterministic local hash embeddings; higher-quality model embeddings remain an upgrade.",
            "Add optional model embedding adapters only after eval evidence and privacy gates are defined.",
        ),
        ProductRequirement(
            "durable_conversations",
            "assistant_memory",
            "Assistant keeps durable local chat sessions with follow-up context.",
            _status_from_files(cfg, [("backend", "app", "conversations.py"), ("frontend", "src", "App.jsx")]),
            "backend/app/conversations.py, API routes, frontend, and CLI conversation commands",
            "",
            "Add stronger search/filtering and conversation export when needed.",
        ),
        ProductRequirement(
            "run_history_audit",
            "assistant_memory",
            "Answer runs remain auditable independently of chat deletion.",
            _status_from_files(cfg, [("backend", "app", "db.py"), ("backend", "app", "exports.py")]),
            "runs table, exports, API routes, and CLI runs commands",
            "",
            "Keep run traces immutable enough for source audit workflows.",
        ),
        ProductRequirement(
            "readiness_diagnostics",
            "operations",
            "Operator can inspect local readiness before asking questions.",
            _status_from_files(cfg, [("backend", "app", "cli.py")]),
            "mucaro doctor",
            "",
            "Extend diagnostics as model/tool surfaces grow.",
        ),
        ProductRequirement(
            "live_model_adapter",
            "model_layer",
            "Assistant can call a live frontier model when explicitly enabled.",
            _status_from_files(cfg, [("backend", "app", "model_router.py")]),
            "Anthropic adapter behind explicit live-mode environment gates",
            "Only mock and Anthropic are currently supported.",
            "Add provider-neutral routing and clearer model readiness UX.",
        ),
        ProductRequirement(
            "tool_use_and_actions",
            "model_layer",
            "Assistant can safely use tools beyond retrieval-backed answering.",
            _status_from_files(cfg, [("backend", "app", "tools.py")]),
            "backend/app/tools.py with allowlisted tools, explicit approvals, execution gates, and audit events",
            "The initial allowlist is intentionally read-only; broader action tools remain future scoped work.",
            "Add narrowly scoped write/action tools only with per-tool policies and stronger UX review.",
        ),
        ProductRequirement(
            "multimodal_interface",
            "assistant_surface",
            "Assistant can work with images/audio/video and rich multimodal inputs.",
            _status_from_files(cfg, [("backend", "app", "multimodal.py")]),
            "Scoped image/audio/video metadata ingestion through parser, API, CLI, and source-lineage manifests.",
            "Semantic image, audio, or video understanding still requires a live multimodal model adapter.",
            "Add model-backed multimodal understanding and richer media previews.",
        ),
        ProductRequirement(
            "personalization_memory",
            "assistant_memory",
            "Assistant has explicit user-controlled long-term preferences or memory.",
            _status_from_files(cfg, [("backend", "app", "memory.py")]),
            "Explicit local memory store with API, CLI, and browser controls for opt-in save, list, enable/disable, delete, export, and answer-context wiring.",
            "",
            "Keep memory user-controlled and separate from cited source evidence.",
        ),
        ProductRequirement(
            "production_security",
            "operations",
            "Assistant is hardened for broader network exposure.",
            _status_from_files(cfg, [("backend", "app", "security.py")]),
            "Production profile enforces operator auth and CORS guardrails; local mode blocks public bind unless explicitly allowed.",
            "Multi-tenant SaaS isolation is excluded by design for this local-first app.",
            "Keep production profile fail-closed and extend only with explicit threat-model changes.",
        ),
        ProductRequirement(
            "source_artifact_packaging",
            "operations",
            "Release candidates produce governed source artifacts with exact receipts.",
            _status_from_files(
                cfg,
                [
                    ("backend", "app", "packaging.py"),
                    ("docs", "PACKAGING_POLICY.md"),
                    ("scripts", "certify_release.py"),
                ],
            ),
            "mucaro package status/build, docs/PACKAGING_POLICY.md, and the release package_artifact gate",
            "",
            "Keep generated artifact receipts bound to exact Git HEAD before any governed release tag.",
        ),
        ProductRequirement(
            "github_release_governance",
            "operations",
            "Governed releases require current-head hosted CI, protected branch policy, and tag binding.",
            "OPEN",
            "mucaro certify status --include-github and mucaro certify github-actions",
            "Hosted GitHub Actions startup currently fails before jobs and branch protection is not enforced; tag binding is verified by the release-state and GitHub governance commands.",
            "Repair hosted Actions startup, enable branch protection after CI is healthy, then require current-head GitHub governance before promoting a governed release.",
        ),
    ]

    rows = [requirement.as_dict() for requirement in requirements]
    counts = {
        "pass": sum(1 for row in rows if row["status"] == "PASS"),
        "open": sum(1 for row in rows if row["status"] == "OPEN"),
        "blocked": sum(1 for row in rows if row["status"] == "BLOCKED"),
        "total": len(rows),
    }
    status = "PRODUCT_AUDIT_OK" if counts["open"] == 0 and counts["blocked"] == 0 else "PRODUCT_AUDIT_OPEN"
    if counts["blocked"]:
        status = "PRODUCT_AUDIT_BLOCKED"
    readiness_percent = round((counts["pass"] / counts["total"]) * 100, 1) if counts["total"] else 0.0
    next_recommended_vector = "complete" if status == "PRODUCT_AUDIT_OK" else next(
        (row["requirement_id"] for row in rows if row["status"] in {"BLOCKED", "OPEN"}),
        "complete",
    )
    return {
        "status": status,
        "objective": OBJECTIVE,
        "readiness_percent": readiness_percent,
        "counts": counts,
        "requirements": rows,
        "next_recommended_vector": next_recommended_vector,
    }


def _stage_status(rows: list[dict[str, str]]) -> str:
    statuses = {row.get("status") for row in rows}
    if "BLOCKED" in statuses:
        return "BLOCKED"
    if "OPEN" in statuses:
        return "OPEN"
    return "PASS"


def build_product_roadmap(cfg: Settings = settings) -> dict[str, Any]:
    audit = build_product_audit(cfg)
    requirements = {
        str(row.get("requirement_id")): row
        for row in audit.get("requirements", [])
        if isinstance(row, dict) and row.get("requirement_id")
    }
    stages = [
        ProductRoadmapStage(
            "canonical_operator_surface",
            "Canonical operator surface",
            (
                "local_desktop_app",
                "installable_cli",
                "readiness_diagnostics",
                "durable_conversations",
                "run_history_audit",
            ),
            "Desktop launcher, CLI, doctor, conversations, and run audit are exposed through supported app surfaces.",
            "",
            "Keep CLI, API, and desktop behavior routed through shared backend services.",
        ),
        ProductRoadmapStage(
            "permissioned_source_grounding",
            "Permissioned source grounding",
            (
                "permission_gated_user_directory",
                "document_ingestion",
                "source_lineage_citations",
                "local_retrieval_strategies",
                "semantic_vector_store",
            ),
            "Directory grants, parsers, source lineage, deterministic strategies, and semantic-store scaffolding are present.",
            "Email ingestion, higher-quality embeddings, and semantic media understanding remain upgrade vectors.",
            "Expand parser and embedding adapters behind explicit permission, provenance, and evaluation gates.",
        ),
        ProductRoadmapStage(
            "controlled_assistant_behavior",
            "Controlled assistant behavior",
            (
                "personalization_memory",
                "tool_use_and_actions",
                "live_model_adapter",
                "multimodal_interface",
                "production_security",
            ),
            "Memory, tools, live-mode routing, multimodal metadata, and security posture are explicitly bounded.",
            "Broader write tools, provider-neutral routing, and semantic multimodal understanding remain future scoped work.",
            "Add new model/tool capabilities only with opt-in controls, audit events, and fail-closed security rules.",
        ),
        ProductRoadmapStage(
            "release_packaging_certification",
            "Release packaging certification",
            ("source_artifact_packaging",),
            "Source-artifact packaging is part of the product audit and release-state receipt path.",
            "",
            "Keep source artifacts, distribution checks, and receipts exact-head bound before release tags.",
        ),
        ProductRoadmapStage(
            "hosted_release_governance",
            "Hosted release governance",
            ("github_release_governance",),
            "GitHub governance remains separate from local certification so hosted failures do not masquerade as product test failures.",
            "Hosted GitHub Actions startup and private branch-protection enforcement are not yet proven.",
            "Repair hosted Actions startup, enforce branch protection when available, and rerun current-head governance certification.",
        ),
    ]

    stage_rows: list[dict[str, Any]] = []
    for stage in stages:
        rows = [requirements[requirement_id] for requirement_id in stage.requirement_ids if requirement_id in requirements]
        counts = {
            "pass": sum(1 for row in rows if row.get("status") == "PASS"),
            "open": sum(1 for row in rows if row.get("status") == "OPEN"),
            "blocked": sum(1 for row in rows if row.get("status") == "BLOCKED"),
            "total": len(rows),
        }
        status = _stage_status(rows)
        open_requirements = [
            str(row.get("requirement_id"))
            for row in rows
            if row.get("status") in {"OPEN", "BLOCKED"}
        ]
        stage_rows.append(
            {
                "stage_id": stage.stage_id,
                "title": stage.title,
                "status": status,
                "counts": counts,
                "requirements": [str(row.get("requirement_id")) for row in rows],
                "open_requirements": open_requirements,
                "evidence": stage.evidence,
                "gap": stage.gap,
                "next_step": stage.next_step,
            }
        )

    stage_statuses = {row["status"] for row in stage_rows}
    status = "PRODUCT_ROADMAP_OK"
    if "BLOCKED" in stage_statuses:
        status = "PRODUCT_ROADMAP_BLOCKED"
    elif "OPEN" in stage_statuses:
        status = "PRODUCT_ROADMAP_OPEN"
    next_recommended_stage = next(
        (row["stage_id"] for row in stage_rows if row["status"] in {"BLOCKED", "OPEN"}),
        "complete",
    )
    return {
        "status": status,
        "objective": audit.get("objective"),
        "audit_status": audit.get("status"),
        "audit_counts": audit.get("counts"),
        "readiness_percent": audit.get("readiness_percent"),
        "next_recommended_vector": audit.get("next_recommended_vector"),
        "next_recommended_stage": next_recommended_stage,
        "stages": stage_rows,
    }
