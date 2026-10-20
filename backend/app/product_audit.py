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
    "production-quality packaging. Capabilities must include advanced GIS "
    "processing, state-of-the-art OCR analysis, powerful web browsing "
    "abilities, coding and programming expertise, all governed by maximum "
    "transparency and objectivity. The conversation-first GUI, source-bound "
    "launcher, portable embedded-runtime packaging, transactional install/rollback "
    "and fail-closed release-grade matrix now exist as candidate operator/release "
    "surfaces; the Mac App Store security policy and candidate auditor are "
    "source-backed, while external corpus, device, signing, provisioning, "
    "clean-machine, App Store review, and live-runtime evidence remains required "
    "before release-grade certification."
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
            "Local app can launch without a hosted service and provide a clean prompt-and-response GUI.",
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
            "TXT, MD, HTML, DOCX, EML, MSG text probe, CSV, JSON, PDF, and metadata-only image/audio/video parsing paths",
            "Full Outlook MSG structured property extraction remains open; media content understanding is metadata-only.",
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
            "Provider registry with mock and Anthropic readiness diagnostics behind explicit live-mode gates",
            "Only mock and Anthropic adapters are currently implemented.",
            "Add new providers through the registry without weakening fail-closed live-mode behavior.",
        ),
        ProductRequirement(
            "tool_use_and_actions",
            "model_layer",
            "Assistant can safely use tools beyond retrieval-backed answering.",
            _status_from_files(cfg, [("backend", "app", "tools.py")]),
            "backend/app/tools.py with allowlisted tools, explicit approvals, execution gates, and audit events",
            "The allowlist includes read-only inspection plus approval-gated governed fetch/session, web search, rendered-browser, coding workspace and controlled coding lifecycle tools; live provider/runtime evidence remains separately gated.",
            "Add narrowly scoped write/action tools only with per-tool policies and stronger UX review.",
        ),
        ProductRequirement(
            "advanced_gis_processing",
            "capability_expansion",
            "Assistant supports advanced GIS processing with spatial provenance and repeatable geometry checks.",
            "OPEN",
            "The shared CLI now exposes bounded GeoJSON inspection, spatial join, comparison, matched polygon overlay, limited compatibility reprojection, CSV points, bbox filtering/clipping, planar and spherical measurement, explicit Shapely topology repair, pyproj/PROJ horizontal CRS transformation with always-XY, offline-only candidate selection, grid inventory, pipeline identity, and one exact-source-bound public-domain NOAA Puerto Rico datum grid with manifest/hash/byte/license provenance and no runtime download, WGS84 ellipsoidal measurement with explicit antimeridian/polar residue, GeoJSON/GeoPackage/Shapefile manifestation inspection, GeoTIFF inspection/sampling/transactional clipping/resampling, exact-CRS vector-raster cell-center analysis with governed zonal statistics, a byte-frozen USGS 3DEP raster manifestation, projected-metre slope/aspect derivation with complete-neighborhood and flat-aspect controls, terrain-lineage-bound hillshade with explicit illumination and zero/NODATA separation, bounded priority-flood depression conditioning with explicit outlet, no-breach, and maximum-depth policy, bounded D8 direction/accumulation/contributing-area derivation with explicit boundary/sink/flat/tie terminal states, frozen Census/USGS vector snapshots, and renderer/layer-bound map-export provenance. Saved-report validation covers these advanced receipts, while mucaro eval gis runs 64 committed positive/negative cases across 25 operations with source/output hashes, dimensionality, NODATA, unresolved residue, and arithmetic closure.",
            "Advanced GIS remains open: additional regional datum grids, independent-engine datum validation, vertical and time-dependent CRS, broader axis-order and large-geometry ellipsoidal adjudication beyond the bounded controls, dimension-preserving mutation, broader topology/overlay policy, fractional zonal coverage, independent rendered-map validation, larger and more diverse real raster/vector corpora, additional vector/raster formats and terrain algorithms beyond bounded slope/aspect/hillshade, remote sensing, hydrology beyond bounded priority-flood conditioning and D8, bathymetry, and temporal GIS are not yet certified.",
            "Add an independent-engine datum control or a second source-bound regional grid next, while preserving exact source hashes, license evidence, CRS and unit declarations, canonical pipeline identity, offline execution, explicit ambiguity states, and fail-closed accounting.",
        ),
        ProductRequirement(
            "state_of_art_ocr_analysis",
            "capability_expansion",
            "Assistant supports state-of-the-art OCR analysis with confidence, layout, and source-image traceability.",
            "OPEN",
            "backend/app/ocr_analysis.py and mucaro ocr analyze provide local Tesseract-backed image and PDF/page receipt candidates with source SHA-256, engine identity, raw/normalized/canonical text separation, engine-derived layout and confidence ledgers, bounding boxes, no-text handling, parser warnings, limitations, and falsification criteria; backend/app/capability_contracts.py validates saved OCR receipt accounting when the ledgers are present.",
            "The implemented boundary remains narrow: representative document accuracy, semantic table/form/handwriting support, labeled confidence calibration, optional provider policy, and broader OCR regression corpus remain open. Image receipts now include engine-derived layout and confidence ledgers with span-accounting validation.",
            "Expand the OCR candidate into document-intelligence gates with frozen image/PDF fixtures, layout and confidence thresholds, broader output validation, and optional cloud/private engines only behind explicit privacy policy.",
        ),
        ProductRequirement(
            "governed_web_browsing",
            "capability_expansion",
            "Assistant has powerful web browsing abilities governed by provenance, safety, and citation controls.",
            "OPEN",
            "web.fetch is an approval-gated tool that enforces URL policy and writes byte-pinned snapshots plus a local snapshot ledger; web.fetch_session adds operator-supplied multi-page source selection with replay receipts. Web snapshots can be ingested as citeable local sources with URL, retrieval UTC, hash metadata, cache/robots metadata, redirect-target robots evidence, HTML body/structure traces, PDF page traces, non-text parser classification, immutable snapshot-only cache policy, and explicit HTTP error-page citation policy.",
            "Governed fetch/session evidence, static discovery, bounded autonomous traversal, credential-last search, and a rendered-browser evidence adapter are source-backed. Live credentialed search evidence and a frozen Playwright/Chromium execution corpus remain open, and rendered observations are not citation-ready by default.",
            "Execute and freeze credentialed search plus rendered-browser corpora before promoting WEB-08/09; preserve the existing citation/source-selection boundaries.",
        ),
        ProductRequirement(
            "coding_programming_expertise",
            "capability_expansion",
            "Assistant can perform coding and programming work with transparent patches, tests, and repository safety boundaries.",
            "OPEN",
            "Governed repository read/discovery, exact dirty-state accounting, Git-native patch identity, controlled patch mutation with rollback, isolated coding-container plans/execution receipts, test/lint/type/build suite contracts, line-bound review evidence, guarded local Git lifecycle, deterministic PR request artifacts, and a fail-closed regression certification ledger are merged.",
            "Live pinned-image container execution and complete executed coding-regression closure remain material evidence gaps; authenticated remote push/PR remains a credential-last action.",
            "Use live isolated-container receipts and the frozen coding regression manifest to close executed evidence without weakening the host-write, network, identity, or approval boundaries.",
        ),
        ProductRequirement(
            "transparency_objectivity_governance",
            "capability_expansion",
            "All advanced capabilities preserve maximum transparency and objectivity through evidence, uncertainty, and contradiction handling.",
            "OPEN",
            "backend/app/capability_contracts.py defines a cross-capability evidence taxonomy, uncertainty, contradiction, and falsification contract, and now includes executable validators for saved advanced GIS and local image OCR receipts.",
            "GIS and OCR have initial executable report validators, but browsing, retrieval, and coding outputs are not yet covered by shared transparency/objectivity validators; coding outputs remain absent.",
            "Expand executable capability-output validation from GIS/OCR reports to browsing, retrieval, and coding outputs, then add fixtures that reject unsupported certainty.",
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
                    ("backend", "app", "macos_app_store.py"),
                    ("docs", "PACKAGING_POLICY.md"),
                    ("docs", "APPLE_APP_STORE_SECURITY.md"),
                    ("_architecture", "macos_app_store_security_profile.json"),
                    ("scripts", "certify_release.py"),
                ],
            ),
            "mucaro package status/build/macos-app/app-store-audit, packaging policies, and the release package_artifact gate",
            "Source artifacts, a source-bound local wrapper, an embedded portable-runtime bundle, transactional install/reinstall/rollback, a fail-closed Mac App Store source/candidate audit, and Store-channel executable-runtime denial are source-backed. Clean-machine direct distribution and Apple-controlled Store evidence remain open.",
            "Keep every distribution receipt exact-head bound; execute clean-machine direct-distribution checks and the Store candidate audit on real signed artifacts, then preserve App Store Connect and App Review as external exact-build gates.",
        ),
        ProductRequirement(
            "github_release_governance",
            "operations",
            "Governed releases require current-head hosted CI, protected branch policy, and tag binding.",
            "OPEN",
            "mucaro certify status --include-github and mucaro certify github-actions",
            "Hosted Actions is again admitting and executing release jobs; exact-SHA hosted-CI evidence artifact generation is merged. Final release-grade proof still requires a retrievable receipt bound to the exact current main head, plus any required branch-protection/tag policy evidence.",
            "Require current-head hosted-CI artifact evidence and preserve branch/tag governance as separate release gates; do not reuse stale PR-head receipts for main.",
        ),
        ProductRequirement(
            "final_phase_gui_operator_workspace",
            "assistant_surface",
            "Conversation-first GUI/operator workspace is implemented as the primary local interaction surface.",
            "OPEN",
            "Chat is the canonical default workspace with separate Sources, Directory Access, Memory, Planning and Settings destinations; answer evidence/cited chunks/validation remain inspectable and duplicate directory-permission controls were removed.",
            "Rendered desktop/mobile/accessibility certification and richer synchronized map/table/code/artifact workspaces remain open; source presence alone is not visual/device proof.",
            "Run rendered desktop and mobile accessibility regressions against representative workflows before promoting the GUI/operator requirement.",
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
            "Full Outlook MSG structured extraction, higher-quality embeddings, and semantic media understanding remain upgrade vectors.",
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
            "Broader write tools, additional model providers, and semantic multimodal understanding remain future scoped work.",
            "Add new model/tool capabilities through the registry with opt-in controls, audit events, and fail-closed security rules.",
        ),
        ProductRoadmapStage(
            "advanced_capability_expansion",
            "Advanced capability expansion",
            (
                "advanced_gis_processing",
                "state_of_art_ocr_analysis",
                "governed_web_browsing",
                "coding_programming_expertise",
                "transparency_objectivity_governance",
            ),
            "The expanded capability objective is visible in product audit and roadmap output; GIS now has CLI/eval/contract coverage for bounded vector, raster, real-raster corpus, slope/aspect terrain, terrain-lineage-bound hillshade, priority-flood depression conditioning, D8 direction/accumulation, topology repair, horizontal CRS, ellipsoidal measurement, vector-raster, and map-export provenance operations, while OCR, web, and coding each have source-backed candidate boundaries.",
            "Full GIS remote-sensing, broader terrain, hydrology beyond bounded D8, bathymetry, and temporal maturity, full document-intelligence OCR, credentialed and rendered browsing corpora, complete coding runtime evidence, and cross-capability objectivity enforcement remain open by design.",
            "Advance each capability from bounded candidate receipts to independently controlled real-corpus certification without weakening permission, source identity, unresolved-residue, or arithmetic-closure gates.",
        ),
        ProductRoadmapStage(
            "release_packaging_certification",
            "Release packaging certification",
            ("source_artifact_packaging",),
            "Source-artifact packaging, local macOS wrappers, portable-runtime/install controls, and the fail-closed Mac App Store policy/candidate auditor are part of the product audit and release-state path.",
            "Real clean-machine direct-distribution evidence and a self-contained sandboxed Store build with resolved loopback-server architecture, native file access, final privacy inventory, Mac App Distribution provisioning, upload processing, and Apple review are still open. Coding execution is disabled in the Store channel rather than accepted as review-compliant.",
            "Keep source artifacts, app checks, distribution checks, and receipts exact-head bound; never promote local Store audit success beyond provisional without Apple evidence for the exact submitted build.",
        ),
        ProductRoadmapStage(
            "hosted_release_governance",
            "Hosted release governance",
            ("github_release_governance",),
            "GitHub governance remains separate from local certification so hosted failures do not masquerade as product test failures.",
            "Hosted GitHub Actions startup and private branch-protection enforcement are not yet proven.",
            "Repair hosted Actions startup, enforce branch protection when available, and rerun current-head governance certification.",
        ),
        ProductRoadmapStage(
            "final_operator_gui",
            "Final operator GUI",
            ("final_phase_gui_operator_workspace",),
            "The GUI remains a final-phase operator surface over proven backend, CLI, provenance, permission, and certification workflows.",
            "The full conversation-first GUI/workspace experience is intentionally not the active implementation vector.",
            "Keep GUI expansion deferred until backend capability gates and release governance can support it without hiding uncertainty.",
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
