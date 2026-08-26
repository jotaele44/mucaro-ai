from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, settings

CODE_ROOT = Path(__file__).resolve().parents[2]
OBJECTIVE = (
    "Have a custom built LLM program that is comparable to ChatGPT and Claude "
    "and has the ability to access the user directory after prompting the user for permission."
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
            "TXT, MD, CSV, JSON, and PDF parsing paths",
            "DOCX, HTML, email, image, audio, and video ingestion remain open.",
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
            "OPEN",
            "Current ingestion is document/text oriented.",
            "No multimodal understanding path is implemented.",
            "Add scoped multimodal ingestion and model-provider support after tool/runtime hardening.",
        ),
        ProductRequirement(
            "personalization_memory",
            "assistant_memory",
            "Assistant has explicit user-controlled long-term preferences or memory.",
            "OPEN",
            "Workspace profiles and conversations exist.",
            "No explicit preference memory store or memory controls are certified.",
            "Design opt-in memory with inspect/delete/export controls.",
        ),
        ProductRequirement(
            "production_security",
            "operations",
            "Assistant is hardened for broader network exposure.",
            "OPEN",
            "Optional local operator token and secret scans exist.",
            "No production auth, multi-user isolation, or deployment hardening is certified.",
            "Keep local-first default; add production profile only with explicit threat model.",
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
    return {
        "status": status,
        "objective": OBJECTIVE,
        "readiness_percent": readiness_percent,
        "counts": counts,
        "requirements": rows,
        "next_recommended_vector": "multimodal_interface",
    }
