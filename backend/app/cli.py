from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from .config import Settings, settings
from .capability_contracts import (
    build_capability_contract_report,
    validate_gis_report_file,
    validate_ocr_report_file,
    validate_retrieval_quality_file,
    validate_web_snapshot_file,
)
from .answer_trace_eval import (
    ANSWER_TRACE_EVAL_OK,
    ANSWER_TRACE_QUALITY_OK,
    ANSWER_TRACE_QUALITY_PROFILE,
    DEFAULT_ANSWER_TRACE_FIXTURE,
    build_answer_trace_eval_report,
    build_answer_trace_quality_certification,
    format_answer_trace_eval_text,
    format_answer_trace_quality_certification_text,
)
from .conversations import (
    append_answer_turn,
    build_conversation_context,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
    update_conversation,
)
from .db import list_payloads
from .execution_gate import analyze_task
from .gis_crs import reproject_geojson_crs
from .gis_corpus import validate_real_gis_corpus
from .gis_export import create_map_export_manifest
from .gis_geodesy import measure_geojson_ellipsoidal
from .gis_hydrology import condition_raster_depressions, derive_raster_d8_flow
from .gis_processing import (
    GISProcessingError,
    clip_geojson_by_bbox,
    compare_geojson_geometries,
    convert_csv_points_to_geojson,
    filter_geojson_by_bbox,
    inspect_geojson,
    measure_geojson,
    overlay_geojson_collections,
    reproject_geojson,
    spatial_join_points_to_polygons,
)
from .gis_raster import (
    clip_raster_bbox,
    derive_raster_hillshade,
    derive_raster_terrain,
    inspect_raster,
    resample_raster,
    sample_raster,
)
from .gis_topology_repair import repair_geojson_topology
from .gis_vector_formats import inspect_vector_format
from .gis_vector_raster import analyze_vector_raster_intersection, compute_zonal_statistics
from .gis_eval import (
    DEFAULT_GIS_EVAL_FIXTURE,
    GIS_EVAL_OK,
    GIS_QUALITY_OK,
    GIS_QUALITY_PROFILE,
    build_gis_eval_report,
    build_gis_quality_certification,
    format_gis_eval_text,
    format_gis_quality_certification_text,
)
from .flagpole_inventory import dirty_paths, generate as generate_flagpole_inventory, validate as validate_flagpole_inventory, validate_schema as validate_flagpole_inventory_schema, verify_receipt as verify_flagpole_receipt, write_inventory as write_flagpole_inventory
from .ocr_analysis import OCRAnalysisError, analyze_image_ocr
from .ocr_structure import OCRStructureError, reconstruct_ocr_report
from .ingestion import (
    IngestLimitError,
    delete_source,
    get_index_status,
    get_sources,
    ingest_file,
    ingest_folder,
    ingest_web_snapshot,
    reindex_sources,
)
from .contracts import contract_errors, list_contracts
from .offline import build_offline_manifest
from .macos_app_store import build_macos_app_store_readiness
from .parser import ParseError
from .memory import (
    MemoryError,
    delete_memory_item,
    export_memory,
    list_memory_items,
    remember_memory_item,
    set_memory_enabled,
)
from .packaging import (
    DISTRIBUTION_MANIFEST_DIR,
    REQUIRED_DISTRIBUTION_RECEIPTS,
    build_distribution_manifest,
    build_macos_app_bundle,
    build_package_status,
    build_release_artifacts,
    verify_macos_app_bundle,
    verify_distribution_manifest,
    verify_release_artifact,
)
from .portable_runtime import (
    PortableRuntimeError,
    build_portable_macos_app,
    verify_portable_macos_app,
)
from .portable_runtime_source import (
    PortableRuntimeSourceError,
    materialize_portable_runtime,
    verify_materialized_runtime,
)
from .portable_install import (
    PortableInstallError,
    install_portable_macos_app,
    rollback_portable_macos_app,
)
from .permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)
from .product_audit import build_product_audit, build_product_roadmap
from .release_receipts import find_release_receipt, list_release_receipts, load_release_receipt, summarize_release_receipts
from .release_scope import (
    capture_state_snapshot,
    compute_release_status,
    freeze_release_scope,
    validate_release_scope,
    write_state_snapshot,
    write_status_doc,
)
from .retrieval import MAX_TOP_K, MIN_TOP_K, RETRIEVAL_STRATEGIES
from .retrieval_eval import DEFAULT_FIXTURE, build_retrieval_eval_report, format_retrieval_eval_text
from .retrieval_quality_certification import (
    RETRIEVAL_QUALITY_OK,
    RETRIEVAL_QUALITY_PROFILE,
    build_retrieval_quality_certification,
    format_retrieval_quality_certification_text,
)
from .security import SecurityProfileError, build_security_report, enforce_startup_security
from .model_router import ModelProviderError, list_model_providers, model_provider_names, model_provider_status
from .operator_workflow_certification import (
    OPERATOR_WORKFLOW_OK,
    OPERATOR_WORKFLOW_PROFILE,
    build_operator_workflow_certification,
    format_operator_workflow_certification_text,
)
from .services import answer_question
from .tools import (
    ToolRuntimeError,
    approve_tool_call,
    cancel_tool_call,
    deny_tool_call,
    list_tool_audit_events,
    list_tool_calls,
    list_tools,
    request_tool_call,
    run_tool_call,
    verify_tool_audit_chain,
)
from .validation import validate_project_json


class MucaroCLIError(RuntimeError):
    def __init__(self, status: str, detail: str, *, exit_code: int = 2) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.exit_code = exit_code


GOVERNANCE_REPORT_SCHEMA_VERSION = "mucaro_governance_report_v1"
GOVERNANCE_REPORT_DIR = Path("data") / "exports" / "governance_reports"
SUPPORT_BUNDLE_SCHEMA_VERSION = "mucaro_release_support_bundle_v1"
SUPPORT_BUNDLE_DIR = Path("data") / "exports" / "support_bundles"
NEXT_ACTIONS_SCHEMA_VERSION = "mucaro_release_next_actions_v2"
NEXT_ACTIONS_DIR = Path("data") / "exports" / "release_next_actions"
HOSTED_INCIDENT_PACKET_SCHEMA_VERSION = "mucaro_hosted_incident_packet_v1"
HOSTED_INCIDENT_PACKET_DIR = Path("data") / "exports" / "hosted_incident_packets"
RELEASE_REFRESH_LOG_SCHEMA_VERSION = "mucaro_release_refresh_log_v1"
RELEASE_REFRESH_LOG_DIR = Path("data") / "exports" / "release_refresh_logs"
RELEASE_HANDOFF_REFRESH_STEPS = ("local_release", "python_dist", "source_artifact", "fresh_clone")


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("PORT_MUST_BE_INTEGER") from exc
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("PORT_RANGE:1-65535")
    return port


def package_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        try:
            return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
        except (KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return importlib.metadata.version("mucaro-ai")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.23-dev"


def parse_json_object(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("JSON_OBJECT_INVALID") from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("JSON_OBJECT_REQUIRED")
    return payload


def parse_top_k(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("TOP_K_MUST_BE_INTEGER") from exc
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        raise argparse.ArgumentTypeError(f"TOP_K_RANGE:{MIN_TOP_K}-{MAX_TOP_K}")
    return top_k


def parse_non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("NON_NEGATIVE_INTEGER_REQUIRED") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("NON_NEGATIVE_INTEGER_REQUIRED")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mucaro",
        description="Command line interface for the local-first Mucaro AI.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ask = subcommands.add_parser("ask", help="Ask a cited retrieval question.")
    ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    ask.add_argument("--provider", choices=model_provider_names(), help="Model provider override.")
    ask.add_argument("--model", help="Model name override.")
    ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit explicit offline mock fallback when the selected route is unavailable.")
    ask.add_argument("--allow-live", action="store_true", help="Explicitly allow a live model provider route.")
    ask.add_argument("--require-offline", action="store_true", help="Require an offline model route.")
    ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ask.add_argument("--no-persist", action="store_true", help="Do not write the run to SQLite.")
    ask.set_defaults(handler=run_ask)

    ingest = subcommands.add_parser("ingest", help="Ingest a supported file or folder.")
    ingest.add_argument("path", nargs="?", default="data/input", help="File or folder to ingest.")
    ingest.add_argument(
        "--grant-directory-access",
        action="store_true",
        help="Prompt to grant access when PATH is outside the project directory.",
    )
    ingest.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    ingest.set_defaults(handler=run_ingest)

    doctor = subcommands.add_parser("doctor", help="Check local assistant readiness.")
    doctor.add_argument("--format", choices=["json", "text"], default="text", help="Output format.")
    doctor.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    doctor.set_defaults(handler=run_doctor)

    models = subcommands.add_parser("models", help="Inspect model provider readiness.")
    models_subcommands = models.add_subparsers(dest="models_command", required=True)

    models_list = models_subcommands.add_parser("list", help="List configured model providers.")
    models_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    models_list.set_defaults(handler=run_models_list)

    models_status = models_subcommands.add_parser("status", help="Show readiness for one model provider.")
    models_status.add_argument("provider", nargs="?", default=None, help="Provider name; defaults to the configured provider.")
    models_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    models_status.set_defaults(handler=run_models_status)

    audit = subcommands.add_parser("audit", help="Audit product completion against the target objective.")
    audit_subcommands = audit.add_subparsers(dest="audit_command", required=True)

    audit_product = audit_subcommands.add_parser("product", help="Audit ChatGPT/Claude-like product gaps.")
    audit_product.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    audit_product.add_argument("--strict", action="store_true", help="Exit nonzero while product gaps remain open.")
    audit_product.set_defaults(handler=run_audit_product)

    audit_roadmap = audit_subcommands.add_parser("roadmap", help="Group product audit gaps into development stages.")
    audit_roadmap.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    audit_roadmap.add_argument("--strict", action="store_true", help="Exit nonzero while roadmap stages remain open.")
    audit_roadmap.set_defaults(handler=run_audit_roadmap)

    audit_completion = audit_subcommands.add_parser(
        "completion",
        help="Audit release-grade completion against the active Mucaro AI goal.",
    )
    audit_completion.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    audit_completion.add_argument("--strict", action="store_true", help="Exit nonzero until every completion requirement passes.")
    audit_completion.add_argument(
        "--include-github",
        action="store_true",
        help="Include live GitHub governance evidence in the completion audit.",
    )
    audit_completion.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect with --include-github.",
    )
    audit_completion.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect with --include-github.",
    )
    audit_completion.set_defaults(handler=run_audit_completion)

    eval_cmd = subcommands.add_parser("eval", help="Run measurable local quality evaluations.")
    eval_subcommands = eval_cmd.add_subparsers(dest="eval_command", required=True)

    eval_retrieval = eval_subcommands.add_parser("retrieval", help="Evaluate local retrieval strategies against a fixture.")
    eval_retrieval.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Retrieval evaluation fixture path.")
    eval_retrieval.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to score ({MIN_TOP_K}-{MAX_TOP_K}).")
    eval_retrieval.add_argument("--min-hybrid-recall", type=float, default=0.80, help="Minimum recall@k required for hybrid retrieval.")
    eval_retrieval.add_argument("--min-no-context-accuracy", type=float, default=1.00, help="Minimum no-context accuracy required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-semantic-recall", type=float, default=0.80, help="Minimum recall@k required for semantic retrieval.")
    eval_retrieval.add_argument("--min-semantic-no-context", type=float, default=1.00, help="Minimum no-context accuracy required for semantic retrieval.")
    eval_retrieval.add_argument("--min-hybrid-precision", type=float, default=0.20, help="Minimum context precision@k required for hybrid retrieval.")
    eval_retrieval.add_argument("--min-semantic-precision", type=float, default=0.20, help="Minimum context precision@k required for semantic retrieval.")
    eval_retrieval.add_argument("--min-expected-binding-recall", type=float, default=0.95, help="Minimum expected binding recall@k required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-multi-binding-full-hit", type=float, default=0.60, help="Minimum multi-binding full-hit rate required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-distractor-free-rate", type=float, default=1.00, help="Minimum distractor-free query rate required for every retrieval strategy.")
    eval_retrieval.add_argument("--min-category-recall", type=float, default=0.0, help="Optional minimum recall@k required for every positive-query category.")
    eval_retrieval.add_argument("--min-source-formats", type=parse_non_negative_int, default=0, help="Optional minimum distinct source_format values required by the fixture.")
    eval_retrieval.add_argument(
        "--min-category-binding-recall",
        type=float,
        default=0.0,
        help="Optional minimum expected-binding recall@k required for every positive-query category.",
    )
    eval_retrieval.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    eval_retrieval.set_defaults(handler=run_eval_retrieval)

    eval_answer_trace = eval_subcommands.add_parser(
        "answer-trace",
        help="Evaluate cited-answer traceability against a fixture.",
    )
    eval_answer_trace.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_ANSWER_TRACE_FIXTURE,
        help="Answer-trace evaluation fixture path.",
    )
    eval_answer_trace.add_argument("--min-case-pass-rate", type=float, default=1.0, help="Minimum fixture case pass rate.")
    eval_answer_trace.add_argument(
        "--allow-no-negative-cases",
        action="store_true",
        help="Do not require at least one negative citation case in the fixture.",
    )
    eval_answer_trace.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    eval_answer_trace.set_defaults(handler=run_eval_answer_trace)

    eval_gis = eval_subcommands.add_parser(
        "gis",
        help="Evaluate local GIS receipts against a fixture manifest.",
    )
    eval_gis.add_argument("--fixture", type=Path, default=DEFAULT_GIS_EVAL_FIXTURE, help="GIS evaluation fixture path.")
    eval_gis.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    eval_gis.set_defaults(handler=run_eval_gis)

    certify = subcommands.add_parser("certify", help="Inspect release certification readiness.")
    certify_subcommands = certify.add_subparsers(dest="certify_command", required=True)

    certify_status = certify_subcommands.add_parser(
        "status",
        help="Show local release readiness before running full certification gates.",
    )
    certify_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_status.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    certify_status.add_argument(
        "--include-github",
        action="store_true",
        help="Include read-only GitHub branch, CI, and remote tag governance checks when gh is available.",
    )
    certify_status.add_argument(
        "--github-workflow",
        default="CI",
        help="GitHub Actions workflow name to use for hosted CI evidence with --include-github.",
    )
    certify_status.set_defaults(handler=run_certify_status)

    certify_github = certify_subcommands.add_parser(
        "github-actions",
        help="Diagnose read-only GitHub Actions governance and hosted runner startup.",
    )
    certify_github.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_github.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    certify_github.add_argument("--workflow", default="CI", help="Primary GitHub Actions workflow name to inspect.")
    certify_github.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    certify_github.set_defaults(handler=run_certify_github_actions)

    certify_retrieval = certify_subcommands.add_parser(
        "retrieval-quality",
        help="Certify release retrieval quality against the calibrated fixture profile.",
    )
    certify_retrieval.add_argument(
        "--profile",
        default=RETRIEVAL_QUALITY_PROFILE,
        help="Named retrieval-quality profile to record in the release receipt.",
    )
    certify_retrieval.add_argument(
        "--top-k",
        type=parse_top_k,
        default=5,
        help=f"Maximum retrieved chunks to score ({MIN_TOP_K}-{MAX_TOP_K}).",
    )
    certify_retrieval.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_retrieval.set_defaults(handler=run_certify_retrieval_quality)

    certify_answer_trace = certify_subcommands.add_parser(
        "answer-trace-quality",
        help="Certify cited-answer traceability against the release fixture profile.",
    )
    certify_answer_trace.add_argument(
        "--profile",
        default=ANSWER_TRACE_QUALITY_PROFILE,
        help="Named answer-trace profile to record in the release receipt.",
    )
    certify_answer_trace.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_ANSWER_TRACE_FIXTURE,
        help="Answer-trace evaluation fixture path.",
    )
    certify_answer_trace.add_argument("--min-case-pass-rate", type=float, default=1.0, help="Minimum fixture case pass rate.")
    certify_answer_trace.add_argument(
        "--allow-no-negative-cases",
        action="store_true",
        help="Do not require at least one negative citation case in the fixture.",
    )
    certify_answer_trace.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_answer_trace.set_defaults(handler=run_certify_answer_trace_quality)

    certify_gis = certify_subcommands.add_parser(
        "gis-quality",
        help="Certify GIS fixture quality against the release fixture manifest.",
    )
    certify_gis.add_argument(
        "--profile",
        default=GIS_QUALITY_PROFILE,
        help="Named GIS-quality profile to record in the release receipt.",
    )
    certify_gis.add_argument("--fixture", type=Path, default=DEFAULT_GIS_EVAL_FIXTURE, help="GIS evaluation fixture path.")
    certify_gis.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_gis.set_defaults(handler=run_certify_gis_quality)

    certify_operator_workflow = certify_subcommands.add_parser(
        "operator-workflow",
        help="Certify the local CLI operator workflow boundary.",
    )
    certify_operator_workflow.add_argument(
        "--profile",
        default=OPERATOR_WORKFLOW_PROFILE,
        help="Named operator-workflow profile to record in the release receipt.",
    )
    certify_operator_workflow.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    certify_operator_workflow.set_defaults(handler=run_certify_operator_workflow)

    sources = subcommands.add_parser("sources", help="Inspect and manage ingested sources.")
    sources_subcommands = sources.add_subparsers(dest="sources_command", required=True)

    sources_list = sources_subcommands.add_parser("list", help="List ingested sources and index status.")
    sources_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_list.set_defaults(handler=run_sources_list)

    sources_reindex = sources_subcommands.add_parser("reindex", help="Rebuild the local persistent retrieval index.")
    sources_reindex.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_reindex.set_defaults(handler=run_sources_reindex)

    sources_delete = sources_subcommands.add_parser("delete", help="Delete one ingested source by source_id.")
    sources_delete.add_argument("source_id", help="Source identifier to remove from the local source library.")
    sources_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_delete.set_defaults(handler=run_sources_delete)

    sources_ingest_web_snapshot = sources_subcommands.add_parser(
        "ingest-web-snapshot",
        help="Ingest one governed web snapshot as a citeable local source.",
    )
    sources_ingest_web_snapshot.add_argument("snapshot_path", help="Path to a mucaro web snapshot JSON file.")
    sources_ingest_web_snapshot.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    sources_ingest_web_snapshot.set_defaults(handler=run_sources_ingest_web_snapshot)

    gis = subcommands.add_parser("gis", help="Run local GIS inspections and spatial joins with receipts.")
    gis_subcommands = gis.add_subparsers(dest="gis_command", required=True)

    gis_inspect = gis_subcommands.add_parser("inspect", help="Inspect a GeoJSON FeatureCollection and write a GIS receipt.")
    gis_inspect.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_inspect.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_inspect.set_defaults(handler=run_gis_inspect)

    gis_point_in_polygon = gis_subcommands.add_parser(
        "point-in-polygon",
        help="Join point GeoJSON features to polygon GeoJSON features and write a GIS receipt.",
    )
    gis_point_in_polygon.add_argument("points_path", help="Path to point GeoJSON FeatureCollection.")
    gis_point_in_polygon.add_argument("polygons_path", help="Path to polygon GeoJSON FeatureCollection.")
    gis_point_in_polygon.add_argument(
        "--allow-crs-mismatch",
        action="store_true",
        help="Permit point/polygon CRS mismatch while preserving the mismatch in the receipt.",
    )
    gis_point_in_polygon.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_point_in_polygon.set_defaults(handler=run_gis_point_in_polygon)

    gis_compare = gis_subcommands.add_parser(
        "compare",
        help="Compare GeoJSON geometries and write an equality/topology receipt.",
    )
    gis_compare.add_argument("left_path", help="Path to the left GeoJSON FeatureCollection.")
    gis_compare.add_argument("right_path", help="Path to the right GeoJSON FeatureCollection.")
    gis_compare.add_argument(
        "--match-on",
        choices=["id", "index"],
        default="id",
        help="Feature matching key.",
    )
    gis_compare.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_compare.set_defaults(handler=run_gis_compare)

    gis_overlay = gis_subcommands.add_parser(
        "overlay",
        help="Overlay matched polygon GeoJSON features and write a derived-output receipt.",
    )
    gis_overlay.add_argument("left_path", help="Path to the left GeoJSON FeatureCollection.")
    gis_overlay.add_argument("right_path", help="Path to the right GeoJSON FeatureCollection.")
    gis_overlay.add_argument(
        "--operation",
        dest="overlay_operation",
        choices=["difference", "intersection", "symmetric_difference", "union"],
        default="intersection",
        help="Planar overlay operation to apply to matched polygon pairs.",
    )
    gis_overlay.add_argument(
        "--match-on",
        choices=["id", "index"],
        default="id",
        help="Feature matching key.",
    )
    gis_overlay.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_overlay.set_defaults(handler=run_gis_overlay)

    gis_reproject = gis_subcommands.add_parser(
        "reproject",
        help="Reproject a GeoJSON FeatureCollection and write a CRS transformation receipt.",
    )
    gis_reproject.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_reproject.add_argument("--target-crs", required=True, help="Target CRS identifier, currently EPSG:4326 or EPSG:3857.")
    gis_reproject.add_argument("--source-crs", help="Optional operator override for the source CRS.")
    gis_reproject.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_reproject.set_defaults(handler=run_gis_reproject)

    gis_reproject_crs = gis_subcommands.add_parser(
        "reproject-crs",
        help="Reproject GeoJSON with pyproj/PROJ and freeze the selected transformation pipeline.",
    )
    gis_reproject_crs.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_reproject_crs.add_argument("--target-crs", required=True, help="Any CRS identifier parseable by pyproj/PROJ.")
    gis_reproject_crs.add_argument("--source-crs", help="Optional operator override for the source CRS.")
    gis_reproject_crs.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_reproject_crs.set_defaults(handler=run_gis_reproject_crs)

    gis_csv_points = gis_subcommands.add_parser(
        "csv-points",
        help="Convert CSV longitude/latitude rows into GeoJSON point features and write a GIS receipt.",
    )
    gis_csv_points.add_argument("csv_path", help="Path to a CSV file with point coordinates.")
    gis_csv_points.add_argument("--lat-column", required=True, help="Latitude column name.")
    gis_csv_points.add_argument("--lon-column", required=True, help="Longitude column name.")
    gis_csv_points.add_argument("--id-column", help="Optional feature ID column name.")
    gis_csv_points.add_argument("--crs", default="EPSG:4326", help="CRS identifier to record on the GeoJSON output.")
    gis_csv_points.add_argument("--delimiter", default=",", help="Single-character CSV delimiter.")
    gis_csv_points.add_argument(
        "--skip-invalid-rows",
        action="store_true",
        help="Skip invalid coordinate rows and record them in the receipt.",
    )
    gis_csv_points.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_csv_points.set_defaults(handler=run_gis_csv_points)

    gis_filter_bbox = gis_subcommands.add_parser(
        "filter-bbox",
        help="Filter whole GeoJSON features by bounding-box extent and write a GIS receipt.",
    )
    gis_filter_bbox.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_filter_bbox.add_argument("--min-x", type=float, required=True, help="Minimum X/longitude of the query bounding box.")
    gis_filter_bbox.add_argument("--min-y", type=float, required=True, help="Minimum Y/latitude of the query bounding box.")
    gis_filter_bbox.add_argument("--max-x", type=float, required=True, help="Maximum X/longitude of the query bounding box.")
    gis_filter_bbox.add_argument("--max-y", type=float, required=True, help="Maximum Y/latitude of the query bounding box.")
    gis_filter_bbox.add_argument(
        "--mode",
        choices=["intersects", "within"],
        default="intersects",
        help="Retain features whose bbox intersects the query bbox, or whose bbox is fully within it.",
    )
    gis_filter_bbox.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_filter_bbox.set_defaults(handler=run_gis_filter_bbox)

    gis_clip_bbox = gis_subcommands.add_parser(
        "clip-bbox",
        help="Clip GeoJSON geometries to a bounding box and write retained/excluded accounting.",
    )
    gis_clip_bbox.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_clip_bbox.add_argument("--min-x", type=float, required=True, help="Minimum X/longitude of the clipping bbox.")
    gis_clip_bbox.add_argument("--min-y", type=float, required=True, help="Minimum Y/latitude of the clipping bbox.")
    gis_clip_bbox.add_argument("--max-x", type=float, required=True, help="Maximum X/longitude of the clipping bbox.")
    gis_clip_bbox.add_argument("--max-y", type=float, required=True, help="Maximum Y/latitude of the clipping bbox.")
    gis_clip_bbox.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_clip_bbox.set_defaults(handler=run_gis_clip_bbox)

    gis_measure = gis_subcommands.add_parser(
        "measure",
        help="Measure GeoJSON feature area/length with planar or geodesic receipts.",
    )
    gis_measure.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_measure.add_argument(
        "--measurement-mode",
        choices=["planar", "geodesic"],
        default="planar",
        help="Measurement adapter. Geodesic mode requires EPSG:4326 and reports meters.",
    )
    gis_measure.add_argument(
        "--coordinate-units",
        default="source_crs_units",
        help="Label for source coordinate units; geodesic mode records this as input units and reports meter outputs.",
    )
    gis_measure.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_measure.set_defaults(handler=run_gis_measure)

    gis_repair_topology = gis_subcommands.add_parser(
        "repair-topology",
        help="Explicitly repair invalid planar XY GeoJSON topology with before/after geometry hashes.",
    )
    gis_repair_topology.add_argument("geojson_path", help="Path to a GeoJSON FeatureCollection.")
    gis_repair_topology.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_repair_topology.set_defaults(handler=run_gis_repair_topology)

    gis_measure_ellipsoidal = gis_subcommands.add_parser(
        "measure-ellipsoidal",
        help="Measure EPSG:4326 GeoJSON on the WGS84 ellipsoid with pyproj Geod.",
    )
    gis_measure_ellipsoidal.add_argument("geojson_path", help="Path to an EPSG:4326 GeoJSON FeatureCollection.")
    gis_measure_ellipsoidal.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_measure_ellipsoidal.set_defaults(handler=run_gis_measure_ellipsoidal)

    gis_inspect_vector = gis_subcommands.add_parser(
        "inspect-vector",
        help="Inspect a GeoJSON, GeoPackage, or Shapefile source manifestation without inferring cross-format identity.",
    )
    gis_inspect_vector.add_argument("vector_path", help="Path to a supported vector source.")
    gis_inspect_vector.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_inspect_vector.set_defaults(handler=run_gis_inspect_vector)

    gis_inspect_raster = gis_subcommands.add_parser(
        "inspect-raster",
        help="Inspect a rasterio-readable local raster with band and NODATA accounting.",
    )
    gis_inspect_raster.add_argument("raster_path", help="Path to a local raster source.")
    gis_inspect_raster.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_inspect_raster.set_defaults(handler=run_gis_inspect_raster)

    gis_sample_raster = gis_subcommands.add_parser(
        "sample-raster",
        help="Sample raster values at explicit coordinates already expressed in the raster CRS.",
    )
    gis_sample_raster.add_argument("raster_path", help="Path to a local raster source.")
    gis_sample_raster.add_argument(
        "--point",
        action="append",
        nargs=2,
        type=float,
        metavar=("X", "Y"),
        required=True,
        help="Coordinate pair in the raster CRS; repeat for multiple points.",
    )
    gis_sample_raster.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_sample_raster.set_defaults(handler=run_gis_sample_raster)

    gis_clip_raster = gis_subcommands.add_parser(
        "clip-raster-bbox",
        help="Clip a raster to an axis-aligned bounding box in the raster CRS.",
    )
    gis_clip_raster.add_argument("raster_path", help="Path to a local raster source.")
    gis_clip_raster.add_argument("--min-x", type=float, required=True, help="Minimum X coordinate.")
    gis_clip_raster.add_argument("--min-y", type=float, required=True, help="Minimum Y coordinate.")
    gis_clip_raster.add_argument("--max-x", type=float, required=True, help="Maximum X coordinate.")
    gis_clip_raster.add_argument("--max-y", type=float, required=True, help="Maximum Y coordinate.")
    gis_clip_raster.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_clip_raster.set_defaults(handler=run_gis_clip_raster)

    gis_vector_raster = gis_subcommands.add_parser(
        "vector-raster",
        help="Summarize raster cells covered by GeoJSON features using explicit cell-center inclusion.",
    )
    gis_vector_raster.add_argument("vector_path", help="Path to a GeoJSON FeatureCollection.")
    gis_vector_raster.add_argument("raster_path", help="Path to a local raster source.")
    gis_vector_raster.add_argument("--band", type=int, default=1, help="One-based raster band index.")
    gis_vector_raster.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_vector_raster.set_defaults(handler=run_gis_vector_raster)

    gis_zonal_stats = gis_subcommands.add_parser(
        "zonal-stats",
        help="Compute bounded per-feature raster statistics using explicit cell-center inclusion.",
    )
    gis_zonal_stats.add_argument("vector_path", help="Path to a GeoJSON FeatureCollection.")
    gis_zonal_stats.add_argument("raster_path", help="Path to a local raster source.")
    gis_zonal_stats.add_argument("--band", type=int, default=1, help="One-based raster band index.")
    gis_zonal_stats.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_zonal_stats.set_defaults(handler=run_gis_zonal_stats)

    gis_resample_raster = gis_subcommands.add_parser(
        "resample-raster",
        help="Transactionally resample a raster to explicit dimensions while preserving extent, CRS, and NODATA metadata.",
    )
    gis_resample_raster.add_argument("raster_path", help="Path to a local raster source.")
    gis_resample_raster.add_argument("--width", type=int, required=True, help="Positive output pixel width.")
    gis_resample_raster.add_argument("--height", type=int, required=True, help="Positive output pixel height.")
    gis_resample_raster.add_argument("--method", choices=["nearest", "bilinear"], default="nearest", help="Bounded resampling method.")
    gis_resample_raster.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_resample_raster.set_defaults(handler=run_gis_resample_raster)

    gis_derive_terrain = gis_subcommands.add_parser(
        "derive-terrain",
        help="Derive slope and aspect from a projected metre elevation raster using complete 3x3 neighborhoods.",
    )
    gis_derive_terrain.add_argument("raster_path", help="Path to a local projected elevation raster.")
    gis_derive_terrain.add_argument("--band", type=int, default=1, help="One-based elevation band index.")
    gis_derive_terrain.add_argument(
        "--elevation-units",
        choices=["meters"],
        required=True,
        help="Explicit vertical units for the selected elevation band.",
    )
    gis_derive_terrain.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_derive_terrain.set_defaults(handler=run_gis_derive_terrain)

    gis_derive_hillshade = gis_subcommands.add_parser(
        "derive-hillshade",
        help="Derive bounded 0..255 hillshade from certified slope/aspect terrain lineage.",
    )
    gis_derive_hillshade.add_argument("raster_path", help="Path to a local projected elevation raster.")
    gis_derive_hillshade.add_argument("--band", type=int, default=1, help="One-based elevation band index.")
    gis_derive_hillshade.add_argument(
        "--elevation-units",
        choices=["meters"],
        required=True,
        help="Explicit vertical units for the selected elevation band.",
    )
    gis_derive_hillshade.add_argument(
        "--azimuth",
        type=float,
        default=315.0,
        help="Light-source azimuth in degrees clockwise from north, in [0,360).",
    )
    gis_derive_hillshade.add_argument(
        "--altitude",
        type=float,
        default=45.0,
        help="Light-source altitude in degrees above the horizon, in (0,90].",
    )
    gis_derive_hillshade.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_derive_hillshade.set_defaults(handler=run_gis_derive_hillshade)

    gis_condition_depressions = gis_subcommands.add_parser(
        "condition-depressions",
        help="Fill bounded enclosed raster depressions to their lowest reachable spill elevation.",
    )
    gis_condition_depressions.add_argument("raster_path", help="Path to a local projected elevation raster.")
    gis_condition_depressions.add_argument("--band", type=int, default=1, help="One-based elevation band index.")
    gis_condition_depressions.add_argument(
        "--elevation-units",
        choices=["meters"],
        required=True,
        help="Explicit vertical units for the selected elevation band.",
    )
    gis_condition_depressions.add_argument(
        "--maximum-fill-depth-m",
        type=float,
        required=True,
        help="Fail closed when any cell would need a larger fill depth in metres.",
    )
    gis_condition_depressions.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Output format.",
    )
    gis_condition_depressions.set_defaults(handler=run_gis_condition_depressions)

    gis_derive_d8_flow = gis_subcommands.add_parser(
        "derive-d8-flow",
        help="Derive bounded D8 direction, accumulation, contributing area, and explicit terminal states.",
    )
    gis_derive_d8_flow.add_argument("raster_path", help="Path to a local projected elevation raster.")
    gis_derive_d8_flow.add_argument("--band", type=int, default=1, help="One-based elevation band index.")
    gis_derive_d8_flow.add_argument(
        "--elevation-units",
        choices=["meters"],
        required=True,
        help="Explicit vertical units for the selected elevation band.",
    )
    gis_derive_d8_flow.add_argument(
        "--elevation-tolerance-m",
        type=float,
        default=0.0,
        help="Non-negative elevation difference treated as level, in metres.",
    )
    gis_derive_d8_flow.add_argument(
        "--gradient-tie-tolerance",
        type=float,
        default=1e-12,
        help="Non-negative absolute tolerance for tied top descent gradients.",
    )
    gis_derive_d8_flow.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_derive_d8_flow.set_defaults(handler=run_gis_derive_d8_flow)

    gis_validate_corpus = gis_subcommands.add_parser(
        "validate-corpus",
        help="Validate frozen authoritative GIS corpus bytes, provenance, schema, IDs, and geometries.",
    )
    gis_validate_corpus.add_argument("manifest_path", help="Path to a mucaro_gis_real_corpus_v1 manifest.")
    gis_validate_corpus.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_validate_corpus.set_defaults(handler=run_gis_validate_corpus)

    gis_map_export = gis_subcommands.add_parser(
        "map-export-manifest",
        help="Bind an existing rendered map/export artifact to its renderer, extent, CRS, and source layers.",
    )
    gis_map_export.add_argument("output_path", help="Path to the existing rendered artifact.")
    gis_map_export.add_argument(
        "--layer",
        action="append",
        nargs=2,
        metavar=("PATH", "CRS"),
        required=True,
        help="Bound source layer path and declared CRS; repeat for multiple layers.",
    )
    gis_map_export.add_argument("--extent", nargs=4, type=float, metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"), required=True)
    gis_map_export.add_argument("--output-crs", required=True, help="Declared CRS of the rendered output.")
    gis_map_export.add_argument("--renderer", required=True, help="Renderer name.")
    gis_map_export.add_argument("--renderer-version", required=True, help="Renderer version.")
    gis_map_export.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    gis_map_export.set_defaults(handler=run_gis_map_export_manifest)

    ocr = subcommands.add_parser("ocr", help="Run local OCR analysis with source-image receipts.")
    ocr_subcommands = ocr.add_subparsers(dest="ocr_command", required=True)

    ocr_analyze = ocr_subcommands.add_parser("analyze", help="Analyze a local image with a local OCR engine.")
    ocr_analyze.add_argument("image_path", help="Path to a local image file.")
    ocr_analyze.add_argument("--language", default="eng", help="OCR language code passed to the local engine.")
    ocr_analyze.add_argument(
        "--layout",
        choices=["single_block", "auto"],
        default="single_block",
        help="OCR layout mode.",
    )
    ocr_analyze.add_argument("--tesseract-path", help="Explicit path to the local tesseract executable.")
    ocr_analyze.add_argument("--timeout-seconds", type=int, default=30, help="OCR engine timeout.")
    ocr_analyze.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ocr_analyze.set_defaults(handler=run_ocr_analyze)

    ocr_structure = ocr_subcommands.add_parser(
        "structure",
        help="Derive a bounded multi-column, table, or form structure receipt from a saved image OCR receipt.",
    )
    ocr_structure.add_argument("report_path", help="Path to a saved mucaro_ocr_report_v1 JSON receipt.")
    ocr_structure.add_argument(
        "--kind",
        choices=["multi_column", "table", "form"],
        required=True,
        help="Bounded structure reconstruction mode.",
    )
    ocr_structure.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    ocr_structure.set_defaults(handler=run_ocr_structure)

    flagpole = subcommands.add_parser("flagpole", help="Flagpole evidence tooling bound to exact Git SHAs.")
    flagpole_subcommands = flagpole.add_subparsers(dest="flagpole_command", required=True)
    flagpole_inventory = flagpole_subcommands.add_parser("inventory", help="Generate or verify the exact-tree inventory.")
    flagpole_inventory_subcommands = flagpole_inventory.add_subparsers(dest="flagpole_inventory_command", required=True)

    flagpole_inventory_generate = flagpole_inventory_subcommands.add_parser("generate", help="Write an inventory of the tracked tree at HEAD.")
    flagpole_inventory_generate.add_argument("--output", default="_architecture/mucaro_flagpole_tree_inventory_v1.json", help="Output path, relative to the project root.")
    flagpole_inventory_generate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    flagpole_inventory_generate.set_defaults(handler=run_flagpole_inventory_generate)

    flagpole_inventory_verify = flagpole_inventory_subcommands.add_parser(
        "verify",
        help="Verify HEAD self-consistency, or a stored inventory against the exact commit it names.",
    )
    flagpole_inventory_verify.add_argument("--inventory", help="Stored inventory to verify as a receipt for its own source_sha.")
    flagpole_inventory_verify.add_argument("--require-clean", action="store_true", help="Fail when the worktree has modified or untracked files.")
    flagpole_inventory_verify.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    flagpole_inventory_verify.set_defaults(handler=run_flagpole_inventory_verify)

    conversations = subcommands.add_parser("conversations", help="Manage persistent local chat sessions.")
    conversations_subcommands = conversations.add_subparsers(dest="conversations_command", required=True)

    conversations_list = conversations_subcommands.add_parser("list", help="List saved conversations.")
    conversations_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_list.set_defaults(handler=run_conversations_list)

    conversations_create = conversations_subcommands.add_parser("create", help="Create a saved conversation.")
    conversations_create.add_argument("--title", default=None, help="Optional conversation title.")
    conversations_create.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_create.set_defaults(handler=run_conversations_create)

    conversations_show = conversations_subcommands.add_parser("show", help="Show one saved conversation.")
    conversations_show.add_argument("conversation_id", help="Conversation identifier.")
    conversations_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_show.set_defaults(handler=run_conversations_show)

    conversations_rename = conversations_subcommands.add_parser("rename", help="Rename one saved conversation.")
    conversations_rename.add_argument("conversation_id", help="Conversation identifier.")
    conversations_rename.add_argument("title", nargs="+", help="New conversation title.")
    conversations_rename.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_rename.set_defaults(handler=run_conversations_rename)

    for command, field, value in (
        ("pin", "pinned", True), ("unpin", "pinned", False),
        ("archive", "archived", True), ("restore", "archived", False),
    ):
        action = conversations_subcommands.add_parser(command, help=f"{command.capitalize()} a saved conversation.")
        action.add_argument("conversation_id", help="Conversation identifier.")
        action.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
        action.set_defaults(handler=run_conversations_organize, organization_field=field, organization_value=value)

    conversations_delete = conversations_subcommands.add_parser("delete", help="Delete one saved conversation.")
    conversations_delete.add_argument("conversation_id", help="Conversation identifier.")
    conversations_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_delete.set_defaults(handler=run_conversations_delete)

    conversations_ask = conversations_subcommands.add_parser("ask", help="Ask inside a saved conversation.")
    conversations_ask.add_argument("conversation_id", help="Conversation identifier.")
    conversations_ask.add_argument("query", nargs="+", help="Question to answer from ingested local sources.")
    conversations_ask.add_argument("--top-k", type=parse_top_k, default=5, help=f"Maximum retrieved chunks to use ({MIN_TOP_K}-{MAX_TOP_K}).")
    conversations_ask.add_argument("--strategy", choices=sorted(RETRIEVAL_STRATEGIES), default="hybrid", help="Retrieval strategy to use.")
    conversations_ask.add_argument("--provider", choices=model_provider_names(), help="Model provider override.")
    conversations_ask.add_argument("--model", help="Model name override.")
    conversations_ask.add_argument("--allow-mock-fallback", action="store_true", help="Permit explicit offline mock fallback when the selected route is unavailable.")
    conversations_ask.add_argument("--allow-live", action="store_true", help="Explicitly allow a live model provider route.")
    conversations_ask.add_argument("--require-offline", action="store_true", help="Require an offline model route.")
    conversations_ask.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    conversations_ask.set_defaults(handler=run_conversations_ask)

    runs = subcommands.add_parser("runs", help="Inspect persisted answer runs.")
    runs_subcommands = runs.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_subcommands.add_parser("list", help="List persisted answer runs.")
    runs_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_list.set_defaults(handler=run_runs_list)

    runs_summary = runs_subcommands.add_parser("summary", help="Summarize persisted answer runs.")
    runs_summary.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_summary.set_defaults(handler=run_runs_summary)

    runs_show = runs_subcommands.add_parser("show", help="Show one persisted answer run.")
    runs_show.add_argument("run_id", help="Run identifier.")
    runs_show.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    runs_show.set_defaults(handler=run_runs_show)

    tools = subcommands.add_parser("tools", help="Request and audit approved local tool calls.")
    tools_subcommands = tools.add_subparsers(dest="tools_command", required=True)

    tools_list = tools_subcommands.add_parser("list", help="List locally allowed tools.")
    tools_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_list.set_defaults(handler=run_tools_list)

    tools_requests = tools_subcommands.add_parser("requests", help="List tool-call requests.")
    tools_requests.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_requests.set_defaults(handler=run_tools_requests)

    tools_request = tools_subcommands.add_parser("request", help="Create a pending tool-call request.")
    tools_request.add_argument("tool_name", help="Allowed tool name.")
    tools_request.add_argument("--arguments", type=parse_json_object, default={}, help="JSON object passed to the tool.")
    tools_request.add_argument("--reason", help="Optional reason recorded in the tool audit log.")
    tools_request.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_request.set_defaults(handler=run_tools_request)

    tools_approve = tools_subcommands.add_parser("approve", help="Approve one pending tool-call request.")
    tools_approve.add_argument("tool_call_id", help="Tool call identifier.")
    tools_approve.add_argument("--reason", help="Optional approval reason.")
    tools_approve.add_argument("--arguments-sha256", help="Bind the approval to this argument digest (refuses stale views).")
    tools_approve.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_approve.set_defaults(handler=run_tools_approve)

    tools_deny = tools_subcommands.add_parser("deny", help="Deny one pending tool-call request.")
    tools_deny.add_argument("tool_call_id", help="Tool call identifier.")
    tools_deny.add_argument("--reason", help="Optional denial reason.")
    tools_deny.add_argument("--arguments-sha256", help="Bind the denial to this argument digest (refuses stale views).")
    tools_deny.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_deny.set_defaults(handler=run_tools_deny)

    tools_run = tools_subcommands.add_parser("run", help="Execute one approved tool-call request.")
    tools_run.add_argument("tool_call_id", help="Tool call identifier.")
    tools_run.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_run.set_defaults(handler=run_tools_run)

    tools_cancel = tools_subcommands.add_parser("cancel", help="Cancel a pending or approved tool call that has not started.")
    tools_cancel.add_argument("tool_call_id", help="Tool call identifier.")
    tools_cancel.add_argument("--reason", help="Optional cancellation reason.")
    tools_cancel.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_cancel.set_defaults(handler=run_tools_cancel)

    tools_verify = tools_subcommands.add_parser("verify-audit", help="Verify the tool audit hash chain.")
    tools_verify.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_verify.set_defaults(handler=run_tools_verify_audit)

    offline = subcommands.add_parser("offline", help="Report which capabilities work without a network.")
    offline_subcommands = offline.add_subparsers(dest="offline_command", required=True)
    offline_manifest = offline_subcommands.add_parser("manifest", help="Classify capabilities for offline use.")
    offline_manifest.add_argument("--assume-offline", action="store_true", help="Report states as if the network were unavailable.")
    offline_manifest.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    offline_manifest.set_defaults(handler=run_offline_manifest)

    contracts = subcommands.add_parser("contracts", help="Inspect and validate the common governed contracts.")
    contracts_subcommands = contracts.add_subparsers(dest="contracts_command", required=True)
    contracts_list = contracts_subcommands.add_parser("list", help="List contract families and their schemas.")
    contracts_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    contracts_list.set_defaults(handler=run_contracts_list)
    contracts_validate = contracts_subcommands.add_parser("validate", help="Validate a JSON record against a contract kind.")
    contracts_validate.add_argument("kind", help="Record kind, e.g. citation_record or tool_call.")
    contracts_validate.add_argument("path", help="JSON file holding one record.")
    contracts_validate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    contracts_validate.set_defaults(handler=run_contracts_validate)

    tools_audit = tools_subcommands.add_parser("audit", help="List tool runtime audit events.")
    tools_audit.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    tools_audit.set_defaults(handler=run_tools_audit)

    capabilities = subcommands.add_parser("capabilities", help="Inspect governed advanced capability contracts.")
    capabilities_subcommands = capabilities.add_subparsers(dest="capabilities_command", required=True)

    capability_contracts = capabilities_subcommands.add_parser(
        "contracts",
        help="List GIS, OCR, web browsing, coding, and transparency/objectivity capability contracts.",
    )
    capability_contracts.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    capability_contracts.set_defaults(handler=run_capability_contracts)

    capability_validate_gis = capabilities_subcommands.add_parser(
        "validate-gis-report",
        help="Validate a GIS receipt against the advanced capability output contract.",
    )
    capability_validate_gis.add_argument("report_path", type=Path, help="Path to a saved GIS report JSON file.")
    capability_validate_gis.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    capability_validate_gis.set_defaults(handler=run_capability_validate_gis_report)

    capability_validate_ocr = capabilities_subcommands.add_parser(
        "validate-ocr-report",
        help="Validate an OCR receipt against the state-of-the-art OCR output contract.",
    )
    capability_validate_ocr.add_argument("report_path", type=Path, help="Path to a saved OCR report JSON file.")
    capability_validate_ocr.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    capability_validate_ocr.set_defaults(handler=run_capability_validate_ocr_report)

    capability_validate_retrieval = capabilities_subcommands.add_parser(
        "validate-retrieval-quality",
        help="Validate a retrieval-quality release receipt against its bounded evidence contract.",
    )
    capability_validate_retrieval.add_argument("report_path", type=Path, help="Path to a retrieval-quality release receipt JSON file.")
    capability_validate_retrieval.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    capability_validate_retrieval.set_defaults(handler=run_capability_validate_retrieval_quality)

    capability_validate_web = capabilities_subcommands.add_parser(
        "validate-web-snapshot",
        help="Validate an immutable governed web snapshot and its citation policy.",
    )
    capability_validate_web.add_argument("snapshot_path", type=Path, help="Path to a saved web snapshot JSON file.")
    capability_validate_web.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    capability_validate_web.set_defaults(handler=run_capability_validate_web_snapshot)

    memory = subcommands.add_parser("memory", help="Manage explicit local assistant memory.")
    memory_subcommands = memory.add_subparsers(dest="memory_command", required=True)

    memory_list = memory_subcommands.add_parser("list", help="List saved memory items.")
    memory_list.add_argument("--active-only", action="store_true", help="Only show enabled memory items.")
    memory_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_list.set_defaults(handler=run_memory_list)

    memory_remember = memory_subcommands.add_parser("remember", help="Save an explicit memory item.")
    memory_remember.add_argument("content", nargs="+", help="Memory content to save.")
    memory_remember.add_argument(
        "--kind",
        choices=["preference", "profile", "instruction", "fact"],
        default="preference",
        help="Memory item type.",
    )
    memory_remember.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_remember.set_defaults(handler=run_memory_remember)

    memory_enable = memory_subcommands.add_parser("enable", help="Enable one memory item.")
    memory_enable.add_argument("memory_id", help="Memory identifier.")
    memory_enable.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_enable.set_defaults(handler=run_memory_enable)

    memory_disable = memory_subcommands.add_parser("disable", help="Disable one memory item without deleting it.")
    memory_disable.add_argument("memory_id", help="Memory identifier.")
    memory_disable.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_disable.set_defaults(handler=run_memory_disable)

    memory_delete = memory_subcommands.add_parser("delete", help="Delete one memory item.")
    memory_delete.add_argument("memory_id", help="Memory identifier.")
    memory_delete.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_delete.set_defaults(handler=run_memory_delete)

    memory_export = memory_subcommands.add_parser("export", help="Export all saved memory items.")
    memory_export.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    memory_export.set_defaults(handler=run_memory_export)

    security = subcommands.add_parser("security", help="Inspect production security readiness.")
    security_subcommands = security.add_subparsers(dest="security_command", required=True)

    security_status = security_subcommands.add_parser("status", help="Show local security profile status.")
    security_status.add_argument("--host", default=None, help="Optional bind host to evaluate.")
    security_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    security_status.set_defaults(handler=run_security_status)

    index = subcommands.add_parser("index", help="Inspect retrieval index health.")
    index_subcommands = index.add_subparsers(dest="index_command", required=True)

    index_status = index_subcommands.add_parser("status", help="Show local persistent index status.")
    index_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    index_status.set_defaults(handler=run_index_status)

    package_cmd = subcommands.add_parser("package", help="Inspect packaging and distribution readiness.")
    package_subcommands = package_cmd.add_subparsers(dest="package_command", required=True)

    package_status = package_subcommands.add_parser("status", help="Show packaging readiness without writing artifacts.")
    package_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_status.add_argument("--strict", action="store_true", help="Exit nonzero when warnings or failures are present.")
    package_status.set_defaults(handler=run_package_status)

    package_build = package_subcommands.add_parser("build", help="Build the governed source-release artifact bundle.")
    package_build.add_argument("--output-dir", type=Path, default=None, help="Directory for generated package artifacts.")
    package_build.add_argument("--allow-dirty", action="store_true", help="Allow artifact creation from a dirty worktree.")
    package_build.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_build.set_defaults(handler=run_package_build)

    package_verify = package_subcommands.add_parser("verify", help="Verify a governed source-release artifact manifest.")
    package_verify.add_argument("manifest", type=Path, help="Artifact manifest path to verify.")
    package_verify.add_argument("--require-current-head", action="store_true", help="Require manifest head_sha to match current Git HEAD.")
    package_verify.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_verify.set_defaults(handler=run_package_verify)

    package_macos_app = package_subcommands.add_parser(
        "macos-app",
        help="Build or verify the double-clickable local macOS Application bundle.",
    )
    package_macos_app.add_argument("--output-dir", type=Path, default=None, help="Directory where Mucaro AI.app should be built.")
    package_macos_app.add_argument("--verify", type=Path, help="Verify an existing .app bundle instead of building one.")
    package_macos_app.add_argument("--python-executable", help="Python executable to embed in the app launcher.")
    package_macos_app.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_macos_app.set_defaults(handler=run_package_macos_app)

    package_app_store_audit = package_subcommands.add_parser(
        "app-store-audit",
        help="Audit the Mac App Store security profile or one signed candidate bundle.",
    )
    package_app_store_audit.add_argument("--app", type=Path, help="Optional signed .app candidate to inspect.")
    package_app_store_audit.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_app_store_audit.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero until external App Store signing, upload, and review gates are certified.",
    )
    package_app_store_audit.set_defaults(handler=run_package_app_store_audit)

    package_portable_runtime = package_subcommands.add_parser(
        "portable-runtime",
        help="Materialize or verify a hash-locked relocatable macOS Python runtime.",
    )
    package_portable_runtime.add_argument(
        "--manifest",
        type=Path,
        help="Runtime source manifest; defaults to the governed x86_64 macOS manifest.",
    )
    package_portable_runtime.add_argument(
        "--archive",
        type=Path,
        help="Optional predownloaded archive; its exact size and SHA-256 must match the source manifest.",
    )
    package_portable_runtime.add_argument(
        "--output-dir",
        type=Path,
        help="New runtime directory; defaults under data/exports/portable_runtimes.",
    )
    package_portable_runtime.add_argument(
        "--verify",
        type=Path,
        help="Verify an existing materialized runtime instead of creating one.",
    )
    package_portable_runtime.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_portable_runtime.set_defaults(handler=run_package_portable_runtime)

    package_portable_app = package_subcommands.add_parser(
        "portable-macos-app",
        help="Build or verify a macOS app containing a relocatable embedded runtime payload.",
    )
    package_portable_app.add_argument("--runtime-dir", type=Path, help="Prevalidated relocatable runtime tree to embed.")
    package_portable_app.add_argument(
        "--runtime-manifest",
        type=Path,
        help="Runtime source manifest; defaults to the governed x86_64 macOS manifest.",
    )
    package_portable_app.add_argument("--runtime-python", default="bin/python3", help="Runtime-relative Python executable path.")
    package_portable_app.add_argument("--output-dir", type=Path, default=None, help="Directory where the portable app should be built.")
    package_portable_app.add_argument("--verify", type=Path, help="Verify an existing portable .app bundle instead of building one.")
    package_portable_app.add_argument("--expected-head", help="Optional exact Git head expected in the portable manifest.")
    package_portable_app.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_portable_app.set_defaults(handler=run_package_portable_macos_app)

    package_portable_install = package_subcommands.add_parser(
        "portable-install",
        help="Install/reinstall or rollback a verified portable macOS app transactionally.",
    )
    package_portable_install.add_argument("action", choices=["install", "rollback"], help="Installation transaction to perform.")
    package_portable_install.add_argument("--source-app", type=Path, help="Verified portable app to install or reinstall.")
    package_portable_install.add_argument("--install-root", type=Path, required=True, help="Destination application directory.")
    package_portable_install.add_argument("--install-name", default="Mucaro AI.app", help="Installed application bundle name.")
    package_portable_install.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_portable_install.set_defaults(handler=run_package_portable_install)

    package_distribution_manifest = package_subcommands.add_parser(
        "distribution-manifest",
        help="Build or verify an operator-facing local distribution handoff manifest.",
    )
    package_distribution_manifest.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    package_distribution_manifest.add_argument(
        "--save",
        action="store_true",
        help="Write the distribution manifest under data/exports/distribution_manifests.",
    )
    package_distribution_manifest.add_argument("--output", type=Path, help="Optional JSON manifest path. Implies --save.")
    package_distribution_manifest.add_argument(
        "--verify",
        type=Path,
        help="Verify a saved distribution manifest instead of building a new one.",
    )
    package_distribution_manifest.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require manifest head_sha to match current Git HEAD.",
    )
    package_distribution_manifest.add_argument(
        "--expected-sha256",
        help="With --verify, require the manifest file SHA-256 to match this value.",
    )
    package_distribution_manifest.set_defaults(handler=run_package_distribution_manifest)

    release = subcommands.add_parser("release", help="Inspect bounded release-state progress.")
    release_subcommands = release.add_subparsers(dest="release_command", required=True)

    release_state = release_subcommands.add_parser("state", help="Classify the current release path into explicit states.")
    release_state.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_state.add_argument("--strict", action="store_true", help="Exit nonzero until the governed release state is ready.")
    release_state.add_argument(
        "--include-github",
        action="store_true",
        help="Include read-only GitHub governance evidence when gh is available.",
    )
    release_state.add_argument(
        "--github-workflow",
        default="CI",
        help="GitHub Actions workflow name to use for hosted CI evidence with --include-github.",
    )
    release_state.set_defaults(handler=run_release_state)

    release_dashboard = release_subcommands.add_parser(
        "dashboard",
        help="Summarize current release readiness, handoff artifacts, receipts, and blockers.",
    )
    release_dashboard.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_dashboard.add_argument("--strict", action="store_true", help="Exit nonzero until the release dashboard is ready.")
    release_dashboard.add_argument(
        "--include-github",
        action="store_true",
        help="Include live GitHub governance diagnostics in the dashboard.",
    )
    release_dashboard.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect with --include-github.",
    )
    release_dashboard.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect with --include-github.",
    )
    release_dashboard.set_defaults(handler=run_release_dashboard)

    release_hosted_governance = release_subcommands.add_parser(
        "hosted-governance",
        help="Summarize hosted-governance blockers and exact remediation commands.",
    )
    release_hosted_governance.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_hosted_governance.add_argument("--strict", action="store_true", help="Exit nonzero until hosted governance is ready.")
    release_hosted_governance.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_hosted_governance.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_hosted_governance.set_defaults(handler=run_release_hosted_governance)

    release_incident_packet = release_subcommands.add_parser(
        "incident-packet",
        help="Build or verify a hosted-governance escalation packet.",
    )
    release_incident_packet.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_incident_packet.add_argument("--strict", action="store_true", help="Exit nonzero if the packet cannot be built or verified.")
    release_incident_packet.add_argument("--save", action="store_true", help="Write the incident packet under data/exports/hosted_incident_packets.")
    release_incident_packet.add_argument("--output", type=Path, help="Optional JSON artifact path. Implies --save.")
    release_incident_packet.add_argument("--verify", type=Path, help="Verify a saved incident packet instead of building one.")
    release_incident_packet.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require the packet head_sha to match current Git HEAD.",
    )
    release_incident_packet.add_argument(
        "--expected-sha256",
        help="With --verify, require the packet artifact SHA-256 to match this value.",
    )
    release_incident_packet.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_incident_packet.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_incident_packet.set_defaults(handler=run_release_incident_packet)

    release_next_actions = release_subcommands.add_parser(
        "next-actions",
        help="Print the prioritized release runbook for the current head.",
    )
    release_next_actions.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_next_actions.add_argument("--strict", action="store_true", help="Exit nonzero until no release actions remain open.")
    release_next_actions.add_argument("--save", action="store_true", help="Write the runbook under data/exports/release_next_actions.")
    release_next_actions.add_argument("--output", type=Path, help="Optional JSON artifact path. Implies --save.")
    release_next_actions.add_argument("--verify", type=Path, help="Verify a saved runbook instead of building a new one.")
    release_next_actions.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require the runbook head_sha to match current Git HEAD.",
    )
    release_next_actions.add_argument(
        "--expected-sha256",
        help="With --verify, require the runbook artifact SHA-256 to match this value.",
    )
    release_next_actions.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_next_actions.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_next_actions.set_defaults(handler=run_release_next_actions)

    release_handoff = release_subcommands.add_parser(
        "handoff",
        help="Save and verify the current-head release handoff artifact set.",
    )
    release_handoff.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_handoff.add_argument("--strict", action="store_true", help="Exit nonzero until the handoff is fully ready.")
    release_handoff.add_argument(
        "--refresh-certification",
        action="store_true",
        help="Run current-head local release, Python distribution, source artifact, and fresh-clone certifiers before saving handoff artifacts.",
    )
    release_handoff.add_argument(
        "--refresh-step",
        action="append",
        choices=RELEASE_HANDOFF_REFRESH_STEPS,
        default=[],
        help="Refresh one certifier step before handoff; repeat to select multiple steps. Implies --refresh-certification.",
    )
    release_handoff.add_argument(
        "--refresh-timeout-seconds",
        type=parse_non_negative_int,
        default=0,
        help="Per-certifier timeout in seconds for --refresh-certification; 0 disables the timeout.",
    )
    release_handoff.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_handoff.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_handoff.set_defaults(handler=run_release_handoff)

    release_support_bundle = release_subcommands.add_parser(
        "support-bundle",
        help="Build or verify a support-ready release evidence bundle.",
    )
    release_support_bundle.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_support_bundle.add_argument("--save", action="store_true", help="Write the support bundle under data/exports/support_bundles.")
    release_support_bundle.add_argument("--output", type=Path, help="Optional JSON artifact path. Implies --save.")
    release_support_bundle.add_argument("--verify", type=Path, help="Verify a saved support bundle instead of building one.")
    release_support_bundle.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require the bundle head_sha to match current Git HEAD.",
    )
    release_support_bundle.add_argument(
        "--expected-sha256",
        help="With --verify, require the support bundle file SHA-256 to match this value.",
    )
    release_support_bundle.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_support_bundle.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_support_bundle.set_defaults(handler=run_release_support_bundle)

    release_governance = release_subcommands.add_parser(
        "governance-report",
        help="Build a current-head governance handoff report.",
    )
    release_governance.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_governance.add_argument("--strict", action="store_true", help="Exit nonzero until governed release state is ready.")
    release_governance.add_argument("--save", action="store_true", help="Write a JSON governance report artifact under data/exports/governance_reports.")
    release_governance.add_argument("--output", type=Path, help="Optional JSON artifact path. Implies --save.")
    release_governance.add_argument("--verify", type=Path, help="Verify a saved governance report artifact instead of building a new report.")
    release_governance.add_argument(
        "--require-current-head",
        action="store_true",
        help="With --verify, require the saved report head_sha to match current Git HEAD.",
    )
    release_governance.add_argument(
        "--expected-sha256",
        help="With --verify, require the artifact file SHA-256 to match this value.",
    )
    release_governance.add_argument(
        "--github-workflow",
        default="CI",
        help="Primary GitHub Actions workflow name to inspect.",
    )
    release_governance.add_argument(
        "--probe-workflow",
        default="CI Startup Probe",
        help="Manual-only startup probe workflow name to inspect.",
    )
    release_governance.set_defaults(handler=run_release_governance_report)

    release_receipts = release_subcommands.add_parser("receipts", help="List local release certification receipts.")
    release_receipts.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_receipts.add_argument("--current-head", action="store_true", help="Only show receipts for the current Git HEAD.")
    release_receipts.set_defaults(handler=run_release_receipts)

    release_receipt = release_subcommands.add_parser("receipt", help="Show one local release certification receipt.")
    release_receipt.add_argument("receipt_id", help="Receipt file stem or JSON filename under data/exports/release_receipts.")
    release_receipt.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_receipt.set_defaults(handler=run_release_receipt_show)

    release_scope = release_subcommands.add_parser(
        "scope", help="Validate the release scope manifest, ledgers and certification verdict."
    )
    release_scope_subcommands = release_scope.add_subparsers(dest="release_scope_command", required=True)
    release_scope_validate = release_scope_subcommands.add_parser(
        "validate", help="Validate manifest, acceptance ledger, DAG, blockers, contradictions and scope deltas."
    )
    release_scope_validate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_scope_validate.set_defaults(handler=run_release_scope_validate)
    release_scope_status = release_scope_subcommands.add_parser(
        "status", help="Compute the release verdict under the terminal certification rule."
    )
    release_scope_status.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_scope_status.add_argument(
        "--write-doc", action="store_true", help="Regenerate the computed status block in docs/RELEASE_STATUS.md."
    )
    release_scope_status.set_defaults(handler=run_release_scope_status)
    release_scope_snapshot = release_scope_subcommands.add_parser(
        "snapshot", help="Capture a current-state snapshot (credential presence only, never values)."
    )
    release_scope_snapshot.add_argument("--output", help="Write to this path instead of the committed snapshot path.")
    release_scope_snapshot.add_argument("--pytest-summary", help="Recorded backend test result line, e.g. '663 passed'.")
    release_scope_snapshot.add_argument("--frontend-build", choices=["pass", "fail", "not_run"], default="not_run")
    release_scope_snapshot.add_argument("--connected-resource", action="append", default=[], help="Connected resource name.")
    release_scope_snapshot.add_argument("--boundary", action="append", default=[], help="Known architectural boundary.")
    release_scope_snapshot.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_scope_snapshot.set_defaults(handler=run_release_scope_snapshot)
    release_scope_freeze = release_scope_subcommands.add_parser(
        "freeze", help="Write an immutable ledger checkpoint used to detect silent scope loss."
    )
    release_scope_freeze.add_argument("label", help="Checkpoint label, e.g. 20260924T210000Z-baseline.")
    release_scope_freeze.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    release_scope_freeze.set_defaults(handler=run_release_scope_freeze)

    permissions = subcommands.add_parser("permissions", help="Manage user-directory access grants.")
    permissions_subcommands = permissions.add_subparsers(dest="permission_command", required=True)

    permissions_grant = permissions_subcommands.add_parser("grant", help="Grant access to a file's containing folder or a folder.")
    permissions_grant.add_argument("path", help="File or directory path to authorize.")
    permissions_grant.add_argument("--yes", action="store_true", help="Confirm the grant without an interactive prompt.")
    permissions_grant.add_argument("--reason", help="Optional reason recorded in the local permission ledger.")
    permissions_grant.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_grant.set_defaults(handler=run_permissions_grant)

    permissions_list = permissions_subcommands.add_parser("list", help="List directory access grants.")
    permissions_list.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_list.set_defaults(handler=run_permissions_list)

    permissions_check = permissions_subcommands.add_parser("check", help="Check whether a path is currently accessible.")
    permissions_check.add_argument("path", help="File or directory path to check.")
    permissions_check.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_check.set_defaults(handler=run_permissions_check)

    permissions_revoke = permissions_subcommands.add_parser("revoke", help="Revoke an exact directory access grant.")
    permissions_revoke.add_argument("path", help="File or directory path whose containing grant should be revoked.")
    permissions_revoke.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    permissions_revoke.set_defaults(handler=run_permissions_revoke)

    plan = subcommands.add_parser("plan", help="Analyze a request with the Planning Gate.")
    plan.add_argument("request", nargs="+", help="Request to analyze.")
    plan.add_argument("--input", action="append", default=[], help="Available input, repeatable.")
    plan.add_argument("--approval-state", default="NOT_REQUESTED", help="Current approval state.")
    plan.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    plan.set_defaults(handler=run_plan)

    validate = subcommands.add_parser("validate", help="Validate project JSON artifacts.")
    validate.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
    validate.set_defaults(handler=run_validate)

    serve = subcommands.add_parser("serve", help="Start the local API server.")
    serve.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    serve.add_argument("--port", type=parse_port, default=8000, help="Port to bind.")
    serve.add_argument("--reload", action="store_true", help="Restart when source files change.")
    serve.set_defaults(handler=run_serve)

    return parser


def emit_payload(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    status = payload.get("status") or payload.get("citation_status") or "OK"
    print(status)
    if status in {"CERTIFICATION_READY", "CERTIFICATION_WARN", "CERTIFICATION_FAIL"}:
        print(f"version={payload.get('version')}")
        print(f"project_root={payload.get('project_root')}")
        git = payload.get("git", {})
        if isinstance(git, dict):
            print(f"branch={git.get('branch')}")
            print(f"head_sha={git.get('head_sha')}")
            print(f"tags_at_head={','.join(git.get('tags_at_head', []))}")
            print(f"worktree_clean={git.get('worktree_clean')}")
            print(f"upstream={git.get('upstream')}")
        github = payload.get("github", {})
        if isinstance(github, dict) and github.get("enabled"):
            print(f"github_repo={github.get('repo')}")
            print(f"github_branch_protected={github.get('branch_protected')}")
            print(f"github_workflow={github.get('workflow')}")
            latest_ci = github.get("latest_ci")
            if isinstance(latest_ci, dict):
                print(f"github_latest_ci={latest_ci.get('status')}:{latest_ci.get('conclusion')}:{latest_ci.get('head_sha')}")
        for gate in payload.get("gates", []):
            detail = str(gate.get("detail")).replace("\n", "\\n")
            print(f"{gate.get('status')} {gate.get('name')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"GITHUB_ACTIONS_DIAGNOSTIC_OK", "GITHUB_ACTIONS_DIAGNOSTIC_WARN", "GITHUB_ACTIONS_DIAGNOSTIC_FAIL"}:
        print(f"repo={payload.get('repo')}")
        print(f"branch={payload.get('branch')}")
        print(f"head_sha={payload.get('head_sha')}")
        startup_blocker = payload.get("startup_blocker", {})
        if isinstance(startup_blocker, dict):
            print(f"startup_blocker_status={startup_blocker.get('status')}")
            causes = startup_blocker.get("suspected_causes", [])
            if isinstance(causes, list):
                print(f"startup_blocker_causes={','.join(str(cause) for cause in causes)}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        runs = payload.get("runs", {})
        if isinstance(runs, dict):
            for run_name in ["ci", "startup_probe"]:
                run = runs.get(run_name)
                if isinstance(run, dict):
                    print(
                        f"run={run_name} id={run.get('database_id')} status={run.get('status')} "
                        f"conclusion={run.get('conclusion')} head_sha={run.get('head_sha')} "
                        f"jobs={run.get('jobs_count')} url={run.get('url')}"
                    )
        current_head_startup_runs = payload.get("current_head_startup_runs", [])
        if isinstance(current_head_startup_runs, list):
            for run in current_head_startup_runs:
                if isinstance(run, dict):
                    print(
                        f"current_head_startup_run id={run.get('database_id')} "
                        f"name={run.get('name')} event={run.get('event')} "
                        f"status={run.get('status')} conclusion={run.get('conclusion')} "
                        f"jobs={run.get('jobs_count')} url={run.get('url')}"
                    )
        check_suites = payload.get("check_suites", {})
        if isinstance(check_suites, dict):
            print(f"check_suites_total={check_suites.get('total_count')}")
            for suite in check_suites.get("suites", []):
                if isinstance(suite, dict):
                    print(
                        f"check_suite id={suite.get('id')} app={suite.get('app')} "
                        f"status={suite.get('status')} conclusion={suite.get('conclusion')} "
                        f"check_runs={suite.get('latest_check_runs_count')} url={suite.get('url')}"
                    )
        actions_permissions = payload.get("actions_permissions", {})
        if isinstance(actions_permissions, dict):
            print(f"actions_permissions_available={actions_permissions.get('available')}")
            print(f"actions_enabled={actions_permissions.get('enabled')}")
            print(f"allowed_actions={actions_permissions.get('allowed_actions')}")
            print(f"sha_pinning_required={actions_permissions.get('sha_pinning_required')}")
        workflow_permissions = payload.get("workflow_permissions", {})
        if isinstance(workflow_permissions, dict):
            print(f"workflow_permissions_available={workflow_permissions.get('available')}")
            print(f"default_workflow_permissions={workflow_permissions.get('default_workflow_permissions')}")
            print(f"can_approve_pull_request_reviews={workflow_permissions.get('can_approve_pull_request_reviews')}")
        billing = payload.get("billing", {})
        if isinstance(billing, dict):
            print(f"billing_available={billing.get('available')}")
            if billing.get("required_scope"):
                print(f"billing_required_scope={billing.get('required_scope')}")
            if billing.get("error"):
                billing_error = str(billing.get("error")).replace("\n", "\\n")
                print(f"billing_error={billing_error}")
        support_summary = payload.get("support_summary", {})
        if isinstance(support_summary, dict):
            print(f"support_classification={support_summary.get('classification')}")
            print(f"support_ci_run_id={support_summary.get('ci_run_id')}")
            print(f"support_startup_probe_run_id={support_summary.get('startup_probe_run_id')}")
            current_head_run_ids = support_summary.get("current_head_startup_run_ids", [])
            if isinstance(current_head_run_ids, list):
                print(f"support_current_head_startup_run_ids={','.join(str(run_id) for run_id in current_head_run_ids)}")
            print(f"support_actions_enabled={support_summary.get('actions_enabled')}")
            print(f"support_allowed_actions={support_summary.get('allowed_actions')}")
            print(f"support_default_workflow_permissions={support_summary.get('default_workflow_permissions')}")
            if support_summary.get("billing_required_scope"):
                print(f"support_billing_required_scope={support_summary.get('billing_required_scope')}")
            counts = support_summary.get("check_suite_counts", {})
            if isinstance(counts, dict):
                print(
                    "support_check_suite_counts="
                    f"github_actions={counts.get('github_actions')} "
                    f"zero_check_run_startup_failures={counts.get('github_actions_zero_check_run_startup_failures')} "
                    f"queued_external={counts.get('queued_external')}"
                )
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {"HOSTED_GOVERNANCE_READY", "HOSTED_GOVERNANCE_OPEN"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tag_at_head', []))}")
        print(f"release_dashboard_status={payload.get('release_dashboard_status')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        print(f"support_classification={payload.get('support_classification')}")
        print(f"ci_run_id={payload.get('ci_run_id')}")
        print(f"ci_run_url={payload.get('ci_run_url')}")
        print(f"startup_probe_run_id={payload.get('startup_probe_run_id')}")
        print(f"startup_probe_run_url={payload.get('startup_probe_run_url')}")
        current_head_run_ids = payload.get("current_head_startup_run_ids", [])
        if isinstance(current_head_run_ids, list):
            print(f"current_head_startup_run_ids={','.join(str(run_id) for run_id in current_head_run_ids)}")
        print(f"ci_jobs_count={payload.get('ci_jobs_count')}")
        print(f"startup_probe_jobs_count={payload.get('startup_probe_jobs_count')}")
        print(f"actions_enabled={payload.get('actions_enabled')}")
        print(f"allowed_actions={payload.get('allowed_actions')}")
        print(f"default_workflow_permissions={payload.get('default_workflow_permissions')}")
        print(f"billing_available={payload.get('billing_available')}")
        if payload.get("billing_required_scope"):
            print(f"billing_required_scope={payload.get('billing_required_scope')}")
        print(f"branch_protection_available={payload.get('branch_protection_available')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(
                "counts="
                f"pass:{counts.get('pass')} "
                f"open:{counts.get('open')} "
                f"blocked:{counts.get('blocked')} "
                f"fail:{counts.get('fail')} "
                f"unverified:{counts.get('unverified')} "
                f"total:{counts.get('total')}"
            )
        startup_blocker = payload.get("startup_blocker", {})
        if isinstance(startup_blocker, dict):
            print(f"startup_blocker_status={startup_blocker.get('status')}")
            causes = startup_blocker.get("suspected_causes", [])
            if isinstance(causes, list):
                print(f"startup_blocker_causes={','.join(str(cause) for cause in causes)}")
        support_bundle = payload.get("support_bundle", {})
        if isinstance(support_bundle, dict):
            print(f"support_bundle_status={support_bundle.get('status')}")
            print(f"support_bundle_path={support_bundle.get('path')}")
        for step in payload.get("steps", []):
            if isinstance(step, dict):
                evidence = str(step.get("evidence")).replace("\n", "\\n")
                print(
                    f"{step.get('status')} {step.get('step_id')} "
                    f"owner={step.get('owner')} evidence={evidence} command={step.get('command')}"
                )
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {
        "HOSTED_INCIDENT_PACKET_READY",
        "HOSTED_INCIDENT_PACKET_FAILED",
        "HOSTED_INCIDENT_PACKET_NOT_REQUIRED",
        "HOSTED_INCIDENT_PACKET_VERIFIED",
        "HOSTED_INCIDENT_PACKET_VERIFY_FAILED",
    }:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        print(f"case_status={payload.get('case_status')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        print(f"support_classification={payload.get('support_classification')}")
        current_head_run_ids = payload.get("current_head_startup_run_ids", [])
        if isinstance(current_head_run_ids, list):
            print(f"current_head_startup_run_ids={','.join(str(run_id) for run_id in current_head_run_ids)}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
            print(f"artifact_schema_version={artifact.get('schema_version')}")
            print(f"artifact_created_utc={artifact.get('created_utc')}")
        for line in payload.get("copy_lines", []):
            print(f"incident_copy={line}")
        for command in payload.get("diagnostic_commands", []):
            print(f"diagnostic_command={command}")
        for check in payload.get("checks", []):
            if isinstance(check, dict):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {"RELEASE_NEXT_ACTIONS_READY", "RELEASE_NEXT_ACTIONS_OPEN"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tag_at_head', []))}")
        print(f"release_dashboard_status={payload.get('release_dashboard_status')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        print(f"support_classification={payload.get('support_classification')}")
        print(f"ci_run_id={payload.get('ci_run_id')}")
        print(f"ci_run_url={payload.get('ci_run_url')}")
        print(f"startup_probe_run_id={payload.get('startup_probe_run_id')}")
        print(f"startup_probe_run_url={payload.get('startup_probe_run_url')}")
        current_head_run_ids = payload.get("current_head_startup_run_ids", [])
        if isinstance(current_head_run_ids, list):
            print(f"current_head_startup_run_ids={','.join(str(run_id) for run_id in current_head_run_ids)}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(
                "counts="
                f"open:{counts.get('open')} "
                f"total:{counts.get('total')}"
            )
        artifacts = payload.get("artifact_paths", {})
        if isinstance(artifacts, dict):
            for artifact_id in ["distribution_manifest", "governance_report", "support_bundle"]:
                print(f"artifact_{artifact_id}={artifacts.get(artifact_id)}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        for command in payload.get("verification_commands", []):
            print(f"verify_command={command}")
        support_copy = payload.get("support_copy")
        if isinstance(support_copy, dict):
            print(f"support_copy_title={support_copy.get('title')}")
            for line in support_copy.get("copy_lines", []):
                print(f"support_copy={line}")
        for action in payload.get("actions", []):
            if isinstance(action, dict):
                evidence = str(action.get("evidence")).replace("\n", "\\n")
                print(
                    f"{action.get('status')} priority={action.get('priority')} "
                    f"action={action.get('action_id')} owner={action.get('owner')} "
                    f"evidence={evidence} command={action.get('command')}"
                )
    elif status in {"RELEASE_NEXT_ACTIONS_VERIFIED", "RELEASE_NEXT_ACTIONS_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        print(f"runbook_status={payload.get('runbook_status')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
            print(f"artifact_schema_version={artifact.get('schema_version')}")
            print(f"artifact_created_utc={artifact.get('created_utc')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {"RELEASE_HANDOFF_READY", "RELEASE_HANDOFF_OPEN", "RELEASE_HANDOFF_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tag_at_head', []))}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        print(f"release_dashboard_status={payload.get('release_dashboard_status')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(
                "counts="
                f"pass:{counts.get('pass')} "
                f"open:{counts.get('open')} "
                f"fail:{counts.get('fail')} "
                f"total:{counts.get('total')}"
            )
        receipts = payload.get("current_head_receipts_summary", {})
        if isinstance(receipts, dict):
            print(f"receipt_count={receipts.get('total')}")
            print(f"receipt_valid={receipts.get('valid')}")
            print(f"receipt_invalid={receipts.get('invalid')}")
        refresh = payload.get("certification_refresh")
        if isinstance(refresh, dict):
            print(f"certification_refresh_requested={refresh.get('requested')}")
            print(f"certification_refresh_status={refresh.get('status')}")
            print(f"certification_refresh_timeout_seconds={refresh.get('timeout_seconds')}")
            print(f"certification_refresh_elapsed_seconds={refresh.get('total_elapsed_seconds')}")
            log = refresh.get("log")
            if isinstance(log, dict):
                print(f"certification_refresh_log={log.get('path')}")
                print(f"certification_refresh_log_sha256={log.get('sha256')}")
            for slowest in refresh.get("slowest_steps", []):
                if isinstance(slowest, dict):
                    detail = str(slowest.get("detail")).replace("\n", "\\n")
                    print(
                        f"certification_refresh_slowest_step={slowest.get('step_id')} "
                        f"status={slowest.get('status')} "
                        f"elapsed_seconds={slowest.get('elapsed_seconds')} detail={detail}"
                    )
            for step in refresh.get("steps", []):
                if isinstance(step, dict):
                    detail = str(step.get("detail")).replace("\n", "\\n")
                    print(
                        f"certification_refresh_step={step.get('status')} "
                        f"{step.get('step_id')} returncode={step.get('returncode')} "
                        f"elapsed_seconds={step.get('elapsed_seconds')} detail={detail}"
                    )
        artifacts = payload.get("artifacts", {})
        if isinstance(artifacts, dict):
            for artifact_id in ["distribution_manifest", "governance_report", "support_bundle", "next_actions"]:
                artifact = artifacts.get(artifact_id)
                if isinstance(artifact, dict):
                    print(f"artifact_{artifact_id}={artifact.get('path')}")
                    print(f"artifact_{artifact_id}_sha256={artifact.get('sha256')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for command in payload.get("verification_commands", []):
            print(f"verify_command={command}")
    elif status in {"RETRIEVAL_EVAL_OK", "RETRIEVAL_EVAL_FAILED"}:
        print(f"fixture={payload.get('fixture')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(f"chunks={counts.get('chunks')}")
            print(f"queries={counts.get('queries')}")
            if "positive_queries" in counts:
                print(f"positive_queries={counts.get('positive_queries')}")
            if "no_context_queries" in counts:
                print(f"no_context_queries={counts.get('no_context_queries')}")
            if "expected_bindings" in counts:
                print(f"expected_bindings={counts.get('expected_bindings')}")
            if "distractor_bindings" in counts:
                print(f"distractor_bindings={counts.get('distractor_bindings')}")
            if "distractor_queries" in counts:
                print(f"distractor_queries={counts.get('distractor_queries')}")
            if "source_ids" in counts:
                print(f"source_ids={counts.get('source_ids')}")
            if "source_formats" in counts:
                print(f"source_formats={counts.get('source_formats')}")
            if "multi_binding_queries" in counts:
                print(f"multi_binding_queries={counts.get('multi_binding_queries')}")
            if "query_categories" in counts:
                print(f"query_categories={counts.get('query_categories')}")
            print(f"strategies={counts.get('strategies')}")
        fixture_validation = payload.get("fixture_validation", {})
        if isinstance(fixture_validation, dict):
            print(f"fixture_validation={fixture_validation.get('status')}")
            for check in fixture_validation.get("checks", []):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
        print(f"min_hybrid_recall={payload.get('min_hybrid_recall')}")
        print(f"min_no_context_accuracy={payload.get('min_no_context_accuracy')}")
        print(f"min_semantic_recall={payload.get('min_semantic_recall')}")
        print(f"min_semantic_no_context={payload.get('min_semantic_no_context')}")
        print(f"min_hybrid_precision={payload.get('min_hybrid_precision')}")
        print(f"min_semantic_precision={payload.get('min_semantic_precision')}")
        print(f"min_expected_binding_recall={payload.get('min_expected_binding_recall')}")
        print(f"min_multi_binding_full_hit={payload.get('min_multi_binding_full_hit')}")
        print(f"min_distractor_free_rate={payload.get('min_distractor_free_rate')}")
        print(f"min_category_recall={payload.get('min_category_recall')}")
        print(f"min_category_binding_recall={payload.get('min_category_binding_recall')}")
        print(f"min_source_formats={payload.get('min_source_formats')}")
        for line in format_retrieval_eval_text(payload)[:-1]:
            print(line)
    elif status in {"RETRIEVAL_QUALITY_CERT_OK", "RETRIEVAL_QUALITY_CERT_FAILED"}:
        for line in format_retrieval_quality_certification_text(payload)[:-1]:
            print(line)
    elif status in {"ANSWER_TRACE_EVAL_OK", "ANSWER_TRACE_EVAL_FAILED"}:
        for line in format_answer_trace_eval_text(payload)[:-1]:
            print(line)
    elif status in {"GIS_EVAL_OK", "GIS_EVAL_FAILED"}:
        for line in format_gis_eval_text(payload)[:-1]:
            print(line)
    elif status in {"GIS_QUALITY_CERT_OK", "GIS_QUALITY_CERT_FAILED"}:
        for line in format_gis_quality_certification_text(payload)[:-1]:
            print(line)
    elif status in {"ANSWER_TRACE_QUALITY_CERT_OK", "ANSWER_TRACE_QUALITY_CERT_FAILED"}:
        for line in format_answer_trace_quality_certification_text(payload)[:-1]:
            print(line)
    elif status in {"PACKAGE_READY", "PACKAGE_WARN", "PACKAGE_FAIL"}:
        print(f"project_root={payload.get('project_root')}")
        python_package = payload.get("python_package", {})
        frontend_package = payload.get("frontend_package", {})
        if isinstance(python_package, dict):
            print(f"python_package={python_package.get('name')}:{python_package.get('version')}")
            print(f"cli_entrypoint={python_package.get('entrypoint')}")
        if isinstance(frontend_package, dict):
            print(f"frontend_package={frontend_package.get('name')}:{frontend_package.get('version')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {
        "MAC_APP_STORE_READINESS_FAIL",
        "MAC_APP_STORE_READINESS_OPEN",
        "MAC_APP_STORE_CANDIDATE_PROVISIONAL",
    }:
        print(f"schema_version={payload.get('schema_version')}")
        print(f"project_root={payload.get('project_root')}")
        print(f"profile_path={payload.get('profile_path')}")
        print(f"profile_state={payload.get('profile_state')}")
        print(f"candidate_scope={payload.get('candidate_scope')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(
                "counts="
                f"pass:{counts.get('pass')} "
                f"open:{counts.get('open')} "
                f"blocked:{counts.get('blocked')} "
                f"fail:{counts.get('fail')} "
                f"total:{counts.get('total')}"
            )
        for check in payload.get("checks", []):
            if isinstance(check, dict):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
        for gate in payload.get("external_gates", []):
            if isinstance(gate, dict):
                print(f"external_gate={gate.get('state')}:{gate.get('id')}:{gate.get('title')}")
        external_gate_counts = payload.get("external_gate_counts", {})
        if isinstance(external_gate_counts, dict):
            print(
                "external_gate_counts="
                f"pass:{external_gate_counts.get('pass')} "
                f"open:{external_gate_counts.get('open')} "
                f"blocked:{external_gate_counts.get('blocked')} "
                f"fail:{external_gate_counts.get('fail')} "
                f"total:{external_gate_counts.get('total')}"
            )
        for nonclaim in payload.get("explicit_nonclaims", []):
            print(f"nonclaim={nonclaim}")
        for step in payload.get("next_steps", []):
            print(f"next_step={step}")
    elif status in {"ADVANCED_CAPABILITY_CONTRACTS_READY", "ADVANCED_CAPABILITY_CONTRACTS_FAILED"}:
        print(f"schema_version={payload.get('schema_version')}")
        print(f"contract_count={payload.get('contract_count')}")
        print(f"complete_contract_count={payload.get('complete_contract_count')}")
        states = payload.get("implementation_states", {})
        if isinstance(states, dict):
            print("implementation_states=" + ",".join(f"{key}:{value}" for key, value in sorted(states.items())))
        for contract in payload.get("contracts", []):
            if isinstance(contract, dict):
                print(
                    f"{contract.get('contract_status')} "
                    f"{contract.get('implementation_status')} "
                    f"{contract.get('capability_id')}"
                )
        print(f"next_step={payload.get('next_step')}")
    elif status in {"CAPABILITY_OUTPUT_CONTRACT_OK", "CAPABILITY_OUTPUT_CONTRACT_FAILED"}:
        print(f"schema_version={payload.get('schema_version')}")
        print(f"capability_id={payload.get('capability_id')}")
        print(f"report_path={payload.get('report_path')}")
        print(f"report_status={payload.get('report_status')}")
        print(f"operation={payload.get('operation')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(f"pass={counts.get('pass')}")
            print(f"fail={counts.get('fail')}")
            print(f"total={counts.get('total')}")
        for check in payload.get("checks", []):
            if isinstance(check, dict):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {"PACKAGE_ARTIFACT_READY", "PACKAGE_ARTIFACT_BLOCKED", "PACKAGE_ARTIFACT_VERIFIED", "PACKAGE_ARTIFACT_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        if payload.get("output_dir"):
            print(f"output_dir={payload.get('output_dir')}")
        if payload.get("manifest_path"):
            print(f"manifest_path={payload.get('manifest_path')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            if manifest.get("path"):
                print(f"manifest_path={manifest.get('path')}")
            if manifest.get("sha256"):
                print(f"manifest_sha256={manifest.get('sha256')}")
            print(f"manifest_member_count={manifest.get('member_count')}")
    elif status in {"MACOS_APP_BUNDLE_READY", "MACOS_APP_BUNDLE_FAILED", "MACOS_APP_BUNDLE_VERIFIED", "MACOS_APP_BUNDLE_VERIFY_FAILED"}:
        print(status)
        print(f"schema_version={payload.get('schema_version')}")
        print(f"project_root={payload.get('project_root')}")
        print(f"app_path={payload.get('app_path')}")
        print(f"head_sha={payload.get('head_sha')}")
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            print(f"receipt_path={receipt.get('path')}")
            print(f"receipt_sha256={receipt.get('sha256')}")
        executable = payload.get("executable")
        if isinstance(executable, dict):
            print(f"executable_path={executable.get('path')}")
            print(f"executable_sha256={executable.get('sha256')}")
            print(f"executable_mode={executable.get('mode')}")
        for check in payload.get("checks", []):
            if isinstance(check, dict):
                detail = str(check.get("detail")).replace("\n", "\\n")
                print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {
        "DISTRIBUTION_MANIFEST_READY",
        "DISTRIBUTION_MANIFEST_FAILED",
        "DISTRIBUTION_MANIFEST_VERIFIED",
        "DISTRIBUTION_MANIFEST_VERIFY_FAILED",
    }:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        if payload.get("manifest_path"):
            print(f"manifest_path={payload.get('manifest_path')}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"manifest_sha256={artifact.get('sha256')}")
            print(f"manifest_size_bytes={artifact.get('size_bytes')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        artifacts = payload.get("artifacts", {})
        if isinstance(artifacts, dict):
            for name, summary in sorted(artifacts.items()):
                if isinstance(summary, dict):
                    print(f"artifact:{name}={summary.get('path')} sha256={summary.get('sha256')}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"RELEASE_STATE_READY", "RELEASE_STATE_OPEN"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"highest_local_state={payload.get('highest_local_state')}")
        print(f"include_github={payload.get('include_github')}")
        git_payload = payload.get("git", {})
        if isinstance(git_payload, dict):
            print(f"head_sha={git_payload.get('head_sha')}")
            print(f"worktree_clean={git_payload.get('worktree_clean')}")
            print(f"upstream={git_payload.get('upstream')}")
        for state in payload.get("states", []):
            detail = str(state.get("evidence")).replace("\n", "\\n")
            print(f"{state.get('status')} {state.get('state')} {detail}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"COMPLETION_AUDIT_READY", "COMPLETION_AUDIT_OPEN", "COMPLETION_AUDIT_BLOCKED", "COMPLETION_AUDIT_FAIL"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"highest_local_state={payload.get('highest_local_state')}")
        print(f"include_github={payload.get('include_github')}")
        print(f"readiness_percent={payload.get('readiness_percent')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"release_dashboard_status={payload.get('release_dashboard_status')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        counts = payload.get("counts", {})
        if isinstance(counts, dict):
            print(
                "counts="
                f"pass:{counts.get('pass')} "
                f"open:{counts.get('open')} "
                f"blocked:{counts.get('blocked')} "
                f"fail:{counts.get('fail')} "
                f"unverified:{counts.get('unverified')} "
                f"total:{counts.get('total')}"
            )
        receipt_summary = payload.get("current_head_receipts_summary", {})
        if isinstance(receipt_summary, dict):
            print(f"receipt_count={receipt_summary.get('total')}")
            print(f"receipt_valid={receipt_summary.get('valid')}")
            print(f"receipt_invalid={receipt_summary.get('invalid')}")
        for requirement in payload.get("requirements", []):
            if isinstance(requirement, dict):
                evidence = str(requirement.get("evidence")).replace("\n", "\\n")
                print(f"{requirement.get('status')} {requirement.get('requirement_id')} {evidence}")
        for blocker in payload.get("blockers", []):
            print(f"blocker={blocker}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"GOVERNANCE_REPORT_READY", "GOVERNANCE_REPORT_OPEN"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tags_at_head', []))}")
        print(f"product_audit_status={payload.get('product_audit_status')}")
        print(f"package_status={payload.get('package_status')}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        receipt_summary = payload.get("current_head_receipts_summary", {})
        if isinstance(receipt_summary, dict):
            print(f"receipt_count={receipt_summary.get('total')}")
            print(f"receipt_valid={receipt_summary.get('valid')}")
            print(f"receipt_invalid={receipt_summary.get('invalid')}")
        startup_blocker = payload.get("startup_blocker", {})
        if isinstance(startup_blocker, dict):
            print(f"startup_blocker_status={startup_blocker.get('status')}")
            causes = startup_blocker.get("suspected_causes", [])
            if isinstance(causes, list):
                print(f"startup_blocker_causes={','.join(str(cause) for cause in causes)}")
        support_summary = payload.get("github_support_summary", {})
        if isinstance(support_summary, dict):
            print(f"github_support_classification={support_summary.get('classification')}")
            print(f"github_support_ci_run_id={support_summary.get('ci_run_id')}")
            print(f"github_support_startup_probe_run_id={support_summary.get('startup_probe_run_id')}")
            counts = support_summary.get("check_suite_counts", {})
            if isinstance(counts, dict):
                print(
                    "github_support_check_suite_counts="
                    f"github_actions={counts.get('github_actions')} "
                    f"zero_check_run_startup_failures={counts.get('github_actions_zero_check_run_startup_failures')} "
                    f"queued_external={counts.get('queued_external')}"
                )
        github_evidence = payload.get("github_governance_evidence")
        if github_evidence:
            github_evidence_text = str(github_evidence).replace("\n", "\\n")
            print(f"github_governance_evidence={github_evidence_text}")
        for receipt in payload.get("current_head_receipts", []):
            if isinstance(receipt, dict):
                print(
                    f"receipt={receipt.get('receipt_id')} {receipt.get('receipt_type')} "
                    f"{receipt.get('status')} {receipt.get('file_sha256')}"
                )
        for command in payload.get("next_commands", []):
            print(f"command={command}")
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {"GOVERNANCE_REPORT_VERIFIED", "GOVERNANCE_REPORT_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_path={artifact.get('path')}")
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
            print(f"artifact_schema_version={artifact.get('schema_version')}")
            print(f"artifact_created_utc={artifact.get('created_utc')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
    elif status in {"RELEASE_DASHBOARD_READY", "RELEASE_DASHBOARD_OPEN", "RELEASE_DASHBOARD_FAIL"}:
        print(f"current_state={payload.get('current_state')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"tag_at_head={','.join(payload.get('tags_at_head', []))}")
        print(f"release_state_status={payload.get('release_state_status')}")
        print(f"distribution_manifest_status={payload.get('distribution_manifest_status')}")
        print(f"governance_report_status={payload.get('governance_report_status')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        print(f"remote_branch_status={payload.get('remote_branch_status')}")
        print(f"hosted_governance_status={payload.get('hosted_governance_status')}")
        receipt_summary = payload.get("current_head_receipts_summary", {})
        if isinstance(receipt_summary, dict):
            print(f"receipt_count={receipt_summary.get('total')}")
            print(f"receipt_valid={receipt_summary.get('valid')}")
            print(f"receipt_invalid={receipt_summary.get('invalid')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for blocker in payload.get("blockers", []):
            print(f"blocker={blocker}")
        for command in payload.get("next_commands", []):
            print(f"command={command}")
    elif status in {"SUPPORT_BUNDLE_READY", "SUPPORT_BUNDLE_FAILED", "SUPPORT_BUNDLE_VERIFIED", "SUPPORT_BUNDLE_VERIFY_FAILED"}:
        print(f"project_root={payload.get('project_root')}")
        print(f"head_sha={payload.get('head_sha')}")
        print(f"case_status={payload.get('case_status')}")
        print(f"dashboard_status={payload.get('dashboard_status')}")
        print(f"support_classification={payload.get('support_classification')}")
        print(f"github_actions_status={payload.get('github_actions_status')}")
        if payload.get("artifact_path"):
            print(f"artifact_path={payload.get('artifact_path')}")
        export = payload.get("export")
        if isinstance(export, dict):
            print(f"export_path={export.get('path')}")
            print(f"export_sha256={export.get('sha256')}")
            print(f"export_size_bytes={export.get('size_bytes')}")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            print(f"artifact_sha256={artifact.get('sha256')}")
            print(f"artifact_size_bytes={artifact.get('size_bytes')}")
            print(f"artifact_schema_version={artifact.get('schema_version')}")
            print(f"artifact_created_utc={artifact.get('created_utc')}")
        for check in payload.get("checks", []):
            detail = str(check.get("detail")).replace("\n", "\\n")
            print(f"{check.get('status')} {check.get('name')} {detail}")
        for blocker in payload.get("blockers", []):
            print(f"blocker={blocker}")
        for action in payload.get("next_actions", []):
            print(f"next_action={action}")
    elif status in {"OPERATOR_WORKFLOW_CERT_OK", "OPERATOR_WORKFLOW_CERT_FAILED"}:
        for line in format_operator_workflow_certification_text(payload):
            print(line)
    elif status in {"RELEASE_RECEIPTS_OK", "RELEASE_RECEIPT_OK"}:
        if payload.get("receipt_id"):
            print(f"receipt_id={payload.get('receipt_id')}")
        if payload.get("head_sha"):
            print(f"head_sha={payload.get('head_sha')}")
        if payload.get("current_head"):
            print(f"current_head={payload.get('current_head')}")
        if "count" in payload:
            print(f"count={payload.get('count')}")
        summary_counts = payload.get("summary")
        if isinstance(summary_counts, dict) and "total" in summary_counts:
            print(f"valid={summary_counts.get('valid')}")
            print(f"invalid={summary_counts.get('invalid')}")
            print(f"by_type={json.dumps(summary_counts.get('by_type', {}), sort_keys=True)}")
            print(f"by_status={json.dumps(summary_counts.get('by_status', {}), sort_keys=True)}")
        rows = payload.get("receipts", [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    print(
                        f"{'VALID' if row.get('valid') else 'INVALID'} {row.get('receipt_id')} "
                        f"{row.get('receipt_type')} {row.get('status')} {row.get('head_sha')} {row.get('file_sha256')}"
                    )
        summary = payload.get("summary")
        if status == "RELEASE_RECEIPT_OK" and isinstance(summary, dict):
            print(
                f"{'VALID' if summary.get('valid') else 'INVALID'} {summary.get('receipt_id')} "
                f"{summary.get('receipt_type')} {summary.get('status')} {summary.get('head_sha')} {summary.get('file_sha256')}"
            )
            if summary.get("validation_error"):
                print(f"validation_error={summary.get('validation_error')}")
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            print(f"created_utc={receipt.get('created_utc')}")
            print(f"command={' '.join(str(part) for part in receipt.get('command', []))}")
    elif status in {"DOCTOR_OK", "DOCTOR_WARN", "DOCTOR_FAIL"}:
        summary = payload.get("summary", {})
        if isinstance(summary, dict):
            print(f"version={payload.get('version')}")
            print(f"sources={summary.get('source_count', 0)}")
            print(f"index_status={summary.get('index_status')}")
            print(f"semantic_store_status={summary.get('semantic_store_status')}")
            print(f"grants={summary.get('directory_grant_count', 0)}")
            print(f"conversations={summary.get('conversation_count', 0)}")
            print(f"runs={summary.get('run_count', 0)}")
            print(f"memories={summary.get('memory_count', 0)}")
            print(f"enabled_memories={summary.get('enabled_memory_count', 0)}")
            print(f"model_provider={summary.get('model_provider')}")
        for check in payload.get("checks", []):
            print(f"{check.get('status')} {check.get('name')} {check.get('detail')}")
        for recommendation in payload.get("recommendations", []):
            print(f"recommendation={recommendation}")
    elif status in {"MODEL_PROVIDERS_OK", "MODEL_PROVIDER_STATUS"}:
        providers = payload.get("providers")
        if isinstance(providers, list):
            print(f"providers={len(providers)}")
            for provider in providers:
                if isinstance(provider, dict):
                    warnings = ",".join(str(warning) for warning in provider.get("warnings", []))
                    print(
                        f"{provider.get('status')} {provider.get('name')} "
                        f"default_model={provider.get('default_model')} live={provider.get('live')} "
                        f"warnings={warnings or 'none'}"
                    )
        provider = payload.get("provider")
        if isinstance(provider, dict):
            warnings = ",".join(str(warning) for warning in provider.get("warnings", []))
            print(f"provider={provider.get('name')}")
            print(f"status={provider.get('status')}")
            print(f"default_model={provider.get('default_model')}")
            print(f"live={provider.get('live')}")
            print(f"requires_live_mode={provider.get('requires_live_mode')}")
            print(f"live_mode_enabled={provider.get('live_mode_enabled')}")
            print(f"api_key_env={provider.get('api_key_env')}")
            print(f"api_key_configured={provider.get('api_key_configured')}")
            print(f"optional_dependency={provider.get('optional_dependency')}")
            print(f"dependency_available={provider.get('dependency_available')}")
            print(f"warnings={warnings or 'none'}")
    elif status in {"PRODUCT_AUDIT_OK", "PRODUCT_AUDIT_OPEN", "PRODUCT_AUDIT_BLOCKED"}:
        counts = payload.get("counts", {})
        print(f"readiness_percent={payload.get('readiness_percent')}")
        if isinstance(counts, dict):
            print(f"pass={counts.get('pass', 0)}")
            print(f"open={counts.get('open', 0)}")
            print(f"blocked={counts.get('blocked', 0)}")
            print(f"total={counts.get('total', 0)}")
        print(f"next_recommended_vector={payload.get('next_recommended_vector')}")
        for requirement in payload.get("requirements", []):
            print(f"{requirement.get('status')} {requirement.get('requirement_id')} {requirement.get('gap')}")
    elif status in {"PRODUCT_ROADMAP_OK", "PRODUCT_ROADMAP_OPEN", "PRODUCT_ROADMAP_BLOCKED"}:
        audit_counts = payload.get("audit_counts", {})
        print(f"readiness_percent={payload.get('readiness_percent')}")
        if isinstance(audit_counts, dict):
            print(f"audit_pass={audit_counts.get('pass', 0)}")
            print(f"audit_open={audit_counts.get('open', 0)}")
            print(f"audit_blocked={audit_counts.get('blocked', 0)}")
            print(f"audit_total={audit_counts.get('total', 0)}")
        print(f"next_recommended_stage={payload.get('next_recommended_stage')}")
        print(f"next_recommended_vector={payload.get('next_recommended_vector')}")
        for stage in payload.get("stages", []):
            counts = stage.get("counts", {})
            if not isinstance(counts, dict):
                counts = {}
            open_requirements = ",".join(str(item) for item in stage.get("open_requirements", []))
            print(
                f"{stage.get('status')} {stage.get('stage_id')} "
                f"pass={counts.get('pass', 0)} open={counts.get('open', 0)} "
                f"blocked={counts.get('blocked', 0)} total={counts.get('total', 0)}"
            )
            if open_requirements:
                print(f"open_requirements={open_requirements}")
            if stage.get("gap"):
                print(f"gap={stage.get('gap')}")
            print(f"next_step={stage.get('next_step')}")
    elif status in {
        "GIS_INSPECT_OK",
        "GIS_INSPECT_WARN",
        "GIS_SPATIAL_JOIN_OK",
        "GIS_GEOMETRY_COMPARE_OK",
        "GIS_GEOMETRY_COMPARE_WARN",
        "GIS_OVERLAY_OK",
        "GIS_OVERLAY_WARN",
        "GIS_REPROJECT_OK",
        "GIS_REPROJECT_WARN",
        "GIS_CSV_POINTS_OK",
        "GIS_CSV_POINTS_WARN",
        "GIS_BBOX_FILTER_OK",
        "GIS_BBOX_FILTER_WARN",
        "GIS_CLIP_BBOX_OK",
        "GIS_CLIP_BBOX_WARN",
        "GIS_MEASURE_OK",
        "GIS_MEASURE_WARN",
        "GIS_CRS_REPROJECT_OK",
        "GIS_CRS_REPROJECT_WARN",
        "GIS_TOPOLOGY_REPAIR_OK",
        "GIS_TOPOLOGY_REPAIR_WARN",
        "GIS_ELLIPSOIDAL_OK",
        "GIS_ELLIPSOIDAL_WARN",
        "GIS_VECTOR_FORMAT_OK",
        "GIS_RASTER_INSPECT_OK",
        "GIS_RASTER_SAMPLE_OK",
        "GIS_RASTER_CLIP_OK",
        "GIS_RASTER_RESAMPLE_OK",
        "GIS_RASTER_TERRAIN_OK",
        "GIS_RASTER_HILLSHADE_OK",
        "GIS_RASTER_DEPRESSION_FILL_OK",
        "GIS_RASTER_D8_OK",
        "GIS_RASTER_D8_WARN",
        "GIS_VECTOR_RASTER_OK",
        "GIS_VECTOR_RASTER_WARN",
        "GIS_ZONAL_STATS_OK",
        "GIS_ZONAL_STATS_WARN",
        "GIS_CORPUS_VALIDATION_OK",
        "GIS_MAP_EXPORT_MANIFEST_OK",
    }:
        print(status)
        print(f"operation={payload.get('operation')}")
        print(f"report_id={payload.get('report_id')}")
        print(f"report_path={payload.get('report_path')}")
        print(f"report_sha256={payload.get('report_sha256')}")
        source = payload.get("source")
        if isinstance(source, dict):
            if "feature_count" in source:
                print(f"feature_count={source.get('feature_count')}")
            if "row_count" in source:
                print(f"source_row_count={source.get('row_count')}")
            if "sha256" in source:
                print(f"source_sha256={source.get('sha256')}")
            if "header_count" in source:
                print(f"header_count={source.get('header_count')}")
            if "crs" in source:
                print(f"crs={source.get('crs')}")
            if "crs_status" in source:
                print(f"crs_status={source.get('crs_status')}")
            if "format" in source:
                print(f"source_format={source.get('format')}")
            if "bytes" in source:
                print(f"source_bytes={source.get('bytes')}")
        for source_name in ("vector_source", "raster_source"):
            named_source = payload.get(source_name)
            if isinstance(named_source, dict):
                print(f"{source_name}_sha256={named_source.get('sha256')}")
                if "crs" in named_source:
                    print(f"{source_name}_crs={named_source.get('crs')}")
                if "feature_count" in named_source:
                    print(f"{source_name}_feature_count={named_source.get('feature_count')}")
        assets = payload.get("assets")
        if isinstance(assets, list):
            print(f"asset_count={len(assets)}")
            for asset in assets:
                if isinstance(asset, dict):
                    print(
                        f"asset={asset.get('asset_id')} sha256={asset.get('sha256')} "
                        f"type={asset.get('asset_type')} "
                        f"features={asset.get('feature_count')} grid={asset.get('width')}x{asset.get('height')} "
                        f"crs={asset.get('declared_crs')}"
                    )
        if payload.get("join_cardinality"):
            print(f"join_cardinality={payload.get('join_cardinality')}")
        if payload.get("match_on"):
            print(f"match_on={payload.get('match_on')}")
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            for key in (
                "measurement_mode",
                "adapter",
                "topology_adapter",
                "planar_overlay_adapter",
                "overlay_adapter",
                "coordinate_units",
                "area_units",
                "length_units",
                "mode",
                "bbox",
                "overlay_operation",
                "source_crs",
                "target_crs",
                "always_xy",
                "axis_order_policy",
                "transformer_definition_sha256",
                "accuracy",
                "ellipsoid",
                "band",
                "cell_inclusion_rule",
                "implicit_reprojection",
                "requested_bbox",
                "effective_bbox",
                "width",
                "height",
                "method",
                "elevation_units",
                "horizontal_units",
                "horizontal_unit_factor",
                "x_resolution",
                "y_resolution",
                "cell_area_square_metres",
                "maximum_fill_depth_m",
                "connectivity",
                "slope_units",
                "aspect_units",
                "flat_aspect_policy",
                "neighborhood_policy",
                "azimuth_degrees_clockwise_from_north",
                "altitude_degrees_above_horizon",
                "illumination_model",
                "output_scale",
                "nodata_policy",
                "elevation_tolerance_m",
                "gradient_tie_tolerance",
                "direction_convention",
                "boundary_policy",
                "fill_policy",
                "breach_policy",
                "sink_policy",
                "flat_policy",
                "tie_policy",
                "accumulation_policy",
            ):
                if key in parameters:
                    print(f"{key}={parameters.get(key)}")
        output = payload.get("output")
        if isinstance(output, dict):
            print(f"output_path={output.get('path')}")
            print(f"output_sha256={output.get('sha256')}")
            print(f"output_crs={output.get('crs')}")
            if "width" in output:
                print(f"output_width={output.get('width')}")
            if "height" in output:
                print(f"output_height={output.get('height')}")
        invariants = payload.get("invariants")
        if isinstance(invariants, dict):
            for key in (
                "crs_match",
                "crs_mismatch_allowed",
                "point_row_conservation",
                "source_crs",
                "target_crs",
                "crs_changed",
                "feature_row_conservation",
                "coordinate_count_conservation",
                "transformed_feature_count",
                "feature_key_conservation",
                "compared_count",
                "exact_equal_count",
                "topological_equal_count",
                "topology_unresolved_count",
                "topology_valid_count",
                "topology_invalid_count",
                "topology_planar_xy_only_count",
                "topology_not_applicable_count",
                "geometry_difference_count",
                "coordinate_dimension_mismatch_count",
                "vertex_symmetric_difference_total",
                "max_hausdorff_vertex_distance",
                "planar_overlay_measured_count",
                "planar_overlay_not_applicable_count",
                "planar_overlay_unresolved_count",
                "planar_overlay_difference_count",
                "planar_overlay_intersection_area_total",
                "planar_overlay_symmetric_difference_area_total",
                "max_planar_symmetric_difference_area",
                "required_columns_present",
                "source_row_count",
                "output_feature_count",
                "input_feature_count",
                "retained_feature_count",
                "clipped_feature_count",
                "unchanged_feature_count",
                "excluded_feature_count",
                "excluded_outside_count",
                "excluded_null_empty_count",
                "excluded_unresolved_count",
                "source_coordinate_count",
                "output_coordinate_count",
                "output_crs",
                "skipped_row_count",
                "row_accounting_closed",
                "left_feature_count",
                "right_feature_count",
                "shared_feature_key_count",
                "left_only_count",
                "right_only_count",
                "emitted_feature_count",
                "empty_input_count",
                "empty_result_count",
                "unresolved_pair_count",
                "unsupported_pair_count",
                "non_polygon_result_count",
                "feature_key_accounting_closed",
                "overlay_pair_accounting_closed",
                "result_area_total",
                "result_length_total",
                "coordinate_dimension",
                "feature_count",
                "measured_feature_count",
                "null_empty_count",
                "invalid_count",
                "unsupported_count",
                "total_coordinate_count",
                "total_part_count",
                "total_ring_count",
                "total_hole_count",
                "total_area",
                "total_length",
                "source_crs_parseable",
                "target_crs_parseable",
                "axis_order_explicit",
                "transform_pipeline_frozen",
                "output_accounting_closed",
                "repair_is_explicit",
                "total_area_m2",
                "total_length_m",
                "source_hash_present",
                "format_supported",
                "band_accounting_closed",
                "dimensions_positive",
                "point_accounting_closed",
                "source_and_output_hash_present",
                "feature_accounting_closed",
                "cell_accounting_closed",
                "crs_exactly_matched",
                "nodata_separate_from_zero",
                "no_implicit_reprojection",
                "dimensions_exact",
                "bounds_preserved",
                "band_count_preserved",
                "nodata_definition_preserved",
                "nodata_accounting_recorded",
                "transactional_output_published",
                "asset_accounting_closed",
                "all_asset_hashes_matched",
                "all_feature_counts_matched",
                "all_stable_ids_present_and_unique",
                "all_geometries_valid",
                "all_coordinate_dimensions_accounted",
                "asset_type_accounting_closed",
                "all_raster_grids_matched",
                "all_raster_band_values_accounted",
                "projected_crs_required",
                "horizontal_units_metre",
                "vertical_units_metre",
                "axis_aligned_transform",
                "dimensions_preserved",
                "transform_preserved",
                "output_band_count_exact",
                "source_cell_accounting_closed",
                "seed_accounting_closed",
                "processed_accounting_closed",
                "change_accounting_closed",
                "nodata_preserved",
                "outlet_seeds_unchanged",
                "elevations_never_lowered",
                "fill_depth_matches_elevation_delta",
                "fill_depth_nonnegative",
                "maximum_fill_depth_within_limit",
                "fill_volume_accounting_closed",
                "slope_cell_accounting_closed",
                "aspect_cell_accounting_closed",
                "neighborhood_accounting_closed",
                "flat_aspect_accounting_closed",
                "terrain_lineage_hashes_present",
                "terrain_source_identity_bound",
                "output_semantics_exact",
                "cell_accounting_closed",
                "illumination_accounting_closed",
                "hillshade_range_bounded",
                "terrain_validity_mask_preserved",
                "state_accounting_closed",
                "flow_edge_accounting_closed",
                "terminal_state_accounting_closed",
                "direction_state_accounting_closed",
                "terminal_contribution_accounting_closed",
                "terminal_area_accounting_closed",
                "strict_descent_edges",
                "accumulation_range_bounded",
                "contributing_area_matches_cell_area",
                "provenance_complete",
                "layer_count",
                "all_layer_hashes_present",
                "output_hash_present",
                "renderer_identity_present",
            ):
                if key in invariants:
                    print(f"{key}={invariants.get(key)}")
        skipped_rows = payload.get("skipped_rows")
        if isinstance(skipped_rows, list) and skipped_rows:
            for row in skipped_rows:
                if isinstance(row, dict):
                    print(f"skipped_row={row.get('row_number')} status={row.get('status')}")
        state_counts = payload.get("state_counts")
        if isinstance(state_counts, dict):
            for state, count in state_counts.items():
                print(f"{state}={count}")
        terrain_accounting = payload.get("terrain_accounting")
        if isinstance(terrain_accounting, dict):
            for key, value in terrain_accounting.items():
                print(f"{key}={value}")
        hillshade_accounting = payload.get("hillshade_accounting")
        if isinstance(hillshade_accounting, dict):
            for key, value in hillshade_accounting.items():
                print(f"{key}={value}")
        depression_fill_accounting = payload.get("depression_fill_accounting")
        if isinstance(depression_fill_accounting, dict):
            for key, value in depression_fill_accounting.items():
                print(f"{key}={value}")
        flow_accounting = payload.get("flow_accounting")
        if isinstance(flow_accounting, dict):
            for key, value in flow_accounting.items():
                print(f"{key}={value}")
        manifest = payload.get("manifest")
        if isinstance(manifest, dict):
            for key in ("driver", "width", "height", "band_count", "crs", "feature_count", "feature_table_count", "shape_type_code"):
                if key in manifest:
                    print(f"manifest_{key}={manifest.get(key)}")
        renderer = payload.get("renderer")
        if isinstance(renderer, dict):
            print(f"renderer={renderer.get('name')}")
            print(f"renderer_version={renderer.get('version')}")
        layers = payload.get("layers")
        if isinstance(layers, list):
            print(f"bound_layer_count={len(layers)}")
        warnings = payload.get("warnings")
        if warnings:
            print("warnings=" + ",".join(str(warning) for warning in warnings))
    elif status in {"OCR_ANALYSIS_OK", "OCR_ANALYSIS_NO_TEXT"}:
        print(status)
        print(f"operation={payload.get('operation')}")
        print(f"report_id={payload.get('report_id')}")
        print(f"report_path={payload.get('report_path')}")
        print(f"report_sha256={payload.get('report_sha256')}")
        source = payload.get("source")
        if isinstance(source, dict):
            print(f"source_sha256={source.get('sha256')}")
            print(f"mime_type={source.get('mime_type')}")
            print(f"dimensions={source.get('width')}x{source.get('height')}")
        engine = payload.get("engine")
        if isinstance(engine, dict):
            print(f"ocr_engine={engine.get('name')}")
            print(f"ocr_engine_version={engine.get('version')}")
            print(f"language={engine.get('language')}")
            print(f"layout_mode={engine.get('layout_mode')}")
        invariants = payload.get("invariants")
        if isinstance(invariants, dict):
            for key in ("span_count", "low_confidence_count", "empty_text_state"):
                print(f"{key}={invariants.get(key)}")
        warnings = payload.get("warnings")
        if warnings:
            print("warnings=" + ",".join(str(warning) for warning in warnings))
    elif "answer" in payload and "conversation" in payload:
        answer = payload.get("answer", {})
        conversation = payload.get("conversation", {})
        print(f"conversation_id={conversation.get('conversation_id')}")
        if isinstance(answer, dict):
            print(f"answer_status={answer.get('citation_status') or answer.get('status')}")
            if answer.get("run_id"):
                print(f"run_id={answer.get('run_id')}")
            for citation in answer.get("cited_chunks", []):
                if isinstance(citation, dict):
                    print(
                        "citation="
                        f"{citation.get('citation_order')} "
                        f"chunk_id={citation.get('chunk_id')} "
                        f"source_id={citation.get('source_id')} "
                        f"rank={citation.get('retrieval_rank')}"
                    )
            if answer.get("answer"):
                print(answer.get("answer"))
    elif "run_id" in payload:
        print(f"run_id={payload.get('run_id')}")
        print(f"citation_status={payload.get('citation_status')}")
        print(f"retrieval_strategy={payload.get('retrieval_strategy')}")
        for citation in payload.get("cited_chunks", []):
            if isinstance(citation, dict):
                print(
                    "citation="
                    f"{citation.get('citation_order')} "
                    f"chunk_id={citation.get('chunk_id')} "
                    f"source_id={citation.get('source_id')} "
                    f"rank={citation.get('retrieval_rank')}"
                )
        if payload.get("answer"):
            print(payload.get("answer"))
    elif "answer" in payload:
        print(payload["answer"])
    elif "files_ingested" in payload:
        print(f"files_ingested={payload['files_ingested']}")
        if "entries_seen" in payload:
            print(
                f"entries_seen={payload['entries_seen']} skipped={len(payload.get('skipped', []))} "
                f"failed={len(payload.get('failed', []))}"
            )
        for row in payload.get("skipped", []):
            print(f"SKIPPED {row.get('name')} {row.get('reason')}")
        for row in payload.get("failed", []):
            print(f"FAILED {row.get('name')} {row.get('status')}")
    elif "source" in payload:
        source = payload["source"]
        print(f"source_id={source.get('source_id')} chunks_added={payload.get('chunks_added', 0)}")
    elif "conversations" in payload:
        print(f"conversations={len(payload.get('conversations', []))}")
        for conversation in payload.get("conversations", []):
            print(
                f"{conversation.get('conversation_id')} {conversation.get('title')} "
                f"turns={conversation.get('turn_count', 0)} status={conversation.get('last_status')} "
                f"pinned={conversation.get('pinned', False)} archived={conversation.get('archived', False)} "
                f"revision={conversation.get('revision', 0)}"
            )
    elif "conversation_id" in payload and "turns" in payload:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"title={payload.get('title')}")
        print(f"turns={len(payload.get('turns', []))}")
        print(f"pinned={payload.get('pinned', False)} archived={payload.get('archived', False)} revision={payload.get('revision', 0)}")
        for turn in payload.get("turns", []):
            print(f"{turn.get('role')} {turn.get('status')} {turn.get('run_id') or ''}".rstrip())
    elif "runs" in payload:
        print(f"runs={len(payload.get('runs', []))}")
        for run in payload.get("runs", []):
            print(f"{run.get('run_id')} {run.get('citation_status') or run.get('status')} {run.get('retrieval_strategy')}")
    elif "tools" in payload:
        print(f"tools={len(payload.get('tools', []))}")
        for tool in payload.get("tools", []):
            print(
                f"{tool.get('name')} approval={tool.get('requires_approval')} mutates={tool.get('mutates_state')}"
            )
    elif "tool_calls" in payload:
        print(f"tool_calls={len(payload.get('tool_calls', []))}")
        for call in payload.get("tool_calls", []):
            print(f"{call.get('tool_call_id')} {call.get('status')} {call.get('tool_name')}")
    elif "tool_call" in payload:
        call = payload.get("tool_call", {})
        if isinstance(call, dict):
            print(f"tool_call_id={call.get('tool_call_id')}")
            print(f"tool_name={call.get('tool_name')}")
            print(f"tool_status={call.get('status')}")
        result = payload.get("result")
        if isinstance(result, dict):
            print(f"result_status={result.get('status')}")
    elif "events" in payload:
        print(f"events={len(payload.get('events', []))}")
        for event in payload.get("events", []):
            print(f"{event.get('event_id')} {event.get('event')} {event.get('status')}")
    elif "memories" in payload:
        print(f"memories={len(payload.get('memories', []))}")
        print(f"enabled={payload.get('enabled_count', 0)}")
        for item in payload.get("memories", []):
            state = "enabled" if item.get("enabled") else "disabled"
            print(f"{item.get('memory_id')} {state} {item.get('kind')} {item.get('content')}")
    elif "memory" in payload:
        item = payload.get("memory", {})
        if isinstance(item, dict):
            state = "enabled" if item.get("enabled") else "disabled"
            print(f"memory_id={item.get('memory_id')}")
            print(f"memory_status={state}")
            print(f"kind={item.get('kind')}")
    elif payload.get("status") in {"MEMORY_DELETE_OK", "ERROR_MEMORY_NOT_FOUND"}:
        print(f"memory_id={payload.get('memory_id')}")
        print(f"removed={payload.get('removed', 0)}")
    elif payload.get("status") in {"SECURITY_OK", "SECURITY_WARN", "SECURITY_FAIL"}:
        print(f"deployment_profile={payload.get('deployment_profile')}")
        print(f"production_ready={payload.get('production_ready')}")
        for check in payload.get("checks", []):
            print(f"{check.get('status')} {check.get('name')} {check.get('detail')}")
        for recommendation in payload.get("recommendations", []):
            print(f"recommendation={recommendation}")
    elif payload.get("status") == "RUN_HISTORY_OK":
        print(f"run_count={payload.get('run_count', 0)}")
        print(f"citation_ok={payload.get('citation_ok', 0)}")
        print(f"latest_run_id={payload.get('latest_run_id')}")
    elif "sources" in payload:
        print(f"sources={len(payload.get('sources', []))}")
        index = payload.get("index", {})
        if isinstance(index, dict):
            print(f"index_status={index.get('status')}")
            print(f"indexed_chunk_count={index.get('indexed_chunk_count', 0)}")
            semantic_store = index.get("semantic_store", {})
            if isinstance(semantic_store, dict):
                print(f"semantic_store_status={semantic_store.get('status')}")
                print(f"semantic_store_indexed_chunk_count={semantic_store.get('indexed_chunk_count', 0)}")
        for source in payload.get("sources", []):
            print(f"{source.get('source_id')} {source.get('file_name')} chunks={source.get('chunk_count', 0)}")
    elif "removed_chunks" in payload:
        print(f"source_id={payload.get('source_id')}")
        print(f"removed_chunks={payload.get('removed_chunks', 0)}")
        print(f"remaining_sources={payload.get('remaining_sources', 0)}")
    elif "index_status" in payload:
        print(f"index_status={payload.get('index_status')}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
        if payload.get("semantic_store_status"):
            print(f"semantic_store_status={payload.get('semantic_store_status')}")
            print(f"semantic_store_indexed_chunk_count={payload.get('semantic_store_indexed_chunk_count', 0)}")
    elif payload.get("status") in {"INDEX_OK", "INDEX_STALE", "INDEX_MISSING"}:
        print(f"chunk_count={payload.get('chunk_count', 0)}")
        print(f"indexed_chunk_count={payload.get('indexed_chunk_count', 0)}")
        print(f"stale={payload.get('stale')}")
        semantic_store = payload.get("semantic_store", {})
        if isinstance(semantic_store, dict):
            print(f"semantic_store_status={semantic_store.get('status')}")
            print(f"semantic_store_stale={semantic_store.get('stale')}")
    elif "readiness_percent" in payload:
        print(f"active_vector={payload.get('active_vector')}")
        print(f"recommended_mode={payload.get('recommended_mode')}")
        print(f"readiness_percent={payload.get('readiness_percent')}")
    elif "checked" in payload:
        print(f"checked={len(payload.get('checked', []))}")
    elif "grants" in payload:
        print(f"grants={len(payload.get('grants', []))}")
        for grant in payload.get("grants", []):
            print(grant.get("canonical_root"))
    elif "canonical_root" in payload:
        print(f"canonical_root={payload.get('canonical_root')}")
    elif payload.get("status") in {"CONVERSATION_DELETE_OK", "ERROR_CONVERSATION_NOT_FOUND"}:
        print(f"conversation_id={payload.get('conversation_id')}")
        print(f"removed={payload.get('removed')}")
    elif "path" in payload:
        print(f"path={payload.get('path')}")


def emit_error(status: str, detail: str, output_format: str = "json") -> None:
    payload = {"status": status, "detail": detail}
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
        return
    print(f"{status} {detail}", file=sys.stderr)


def run_ask(args: argparse.Namespace, cfg: Settings) -> int:
    result = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        allow_live=args.allow_live,
        require_offline=args.require_offline,
        cfg=cfg,
        persist=not args.no_persist,
    )
    emit_payload(result, args.format)
    if result.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if result.get("citation_status") == "CITATION_OK" else 3


def run_ingest(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.path)
    if not path.is_absolute():
        path = cfg.project_root / path
    if not path.exists():
        raise MucaroCLIError("ERROR_INPUT_PATH_NOT_FOUND", str(path))
    if not path.is_file() and not path.is_dir():
        raise MucaroCLIError("ERROR_INPUT_PATH_UNSUPPORTED", str(path))
    permission = check_directory_permission(path, cfg)
    if permission["status"] != "PERMISSION_OK":
        if not args.grant_directory_access:
            raise MucaroCLIError(str(permission["status"]), str(permission["detail"]))
        grant = prompt_for_directory_grant(Path(str(permission["canonical_root"])), cfg)
        permission = {"status": "PERMISSION_OK", "scope": "granted_directory", "grant": grant}
    result = ingest_folder(path, cfg) if path.is_dir() else ingest_file(path, cfg)
    result["directory_permission"] = permission
    emit_payload(result, args.format)
    return 0


def run_sources_ingest_web_snapshot(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.snapshot_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = ingest_web_snapshot(path, cfg)
    except FileNotFoundError as exc:
        emit_error("ERROR_WEB_SNAPSHOT_NOT_FOUND", str(exc), args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_inspect(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.geojson_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = inspect_geojson(path, cfg=cfg)
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_point_in_polygon(args: argparse.Namespace, cfg: Settings) -> int:
    points_path = Path(args.points_path)
    polygons_path = Path(args.polygons_path)
    if not points_path.is_absolute():
        points_path = cfg.project_root / points_path
    if not polygons_path.is_absolute():
        polygons_path = cfg.project_root / polygons_path
    try:
        result = spatial_join_points_to_polygons(
            points_path,
            polygons_path,
            cfg=cfg,
            require_matching_crs=not args.allow_crs_mismatch,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_compare(args: argparse.Namespace, cfg: Settings) -> int:
    left_path = Path(args.left_path)
    right_path = Path(args.right_path)
    if not left_path.is_absolute():
        left_path = cfg.project_root / left_path
    if not right_path.is_absolute():
        right_path = cfg.project_root / right_path
    try:
        result = compare_geojson_geometries(left_path, right_path, cfg=cfg, match_on=args.match_on)
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_overlay(args: argparse.Namespace, cfg: Settings) -> int:
    left_path = Path(args.left_path)
    right_path = Path(args.right_path)
    if not left_path.is_absolute():
        left_path = cfg.project_root / left_path
    if not right_path.is_absolute():
        right_path = cfg.project_root / right_path
    try:
        result = overlay_geojson_collections(
            left_path,
            right_path,
            cfg=cfg,
            operation=args.overlay_operation,
            match_on=args.match_on,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_reproject(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.geojson_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = reproject_geojson(path, target_crs=args.target_crs, cfg=cfg, source_crs=args.source_crs)
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_csv_points(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.csv_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = convert_csv_points_to_geojson(
            path,
            latitude_column=args.lat_column,
            longitude_column=args.lon_column,
            cfg=cfg,
            id_column=args.id_column,
            crs=args.crs,
            delimiter=args.delimiter,
            skip_invalid_rows=args.skip_invalid_rows,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_filter_bbox(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.geojson_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = filter_geojson_by_bbox(
            path,
            min_x=args.min_x,
            min_y=args.min_y,
            max_x=args.max_x,
            max_y=args.max_y,
            cfg=cfg,
            mode=args.mode,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_clip_bbox(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.geojson_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = clip_geojson_by_bbox(
            path,
            min_x=args.min_x,
            min_y=args.min_y,
            max_x=args.max_x,
            max_y=args.max_y,
            cfg=cfg,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_measure(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.geojson_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = measure_geojson(
            path,
            cfg=cfg,
            coordinate_units=args.coordinate_units,
            measurement_mode=args.measurement_mode,
        )
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def _resolve_project_path(value: str, cfg: Settings) -> Path:
    path = Path(value)
    return path if path.is_absolute() else cfg.project_root / path


def _run_gis_report(args: argparse.Namespace, operation: Any) -> int:
    try:
        result = operation()
    except GISProcessingError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_gis_reproject_crs(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.geojson_path, cfg)
    return _run_gis_report(
        args,
        lambda: reproject_geojson_crs(path, target_crs=args.target_crs, cfg=cfg, source_crs=args.source_crs),
    )


def run_gis_repair_topology(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.geojson_path, cfg)
    return _run_gis_report(args, lambda: repair_geojson_topology(path, cfg=cfg))


def run_gis_measure_ellipsoidal(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.geojson_path, cfg)
    return _run_gis_report(args, lambda: measure_geojson_ellipsoidal(path, cfg=cfg))


def run_gis_inspect_vector(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.vector_path, cfg)
    return _run_gis_report(args, lambda: inspect_vector_format(path, cfg=cfg))


def run_gis_inspect_raster(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(args, lambda: inspect_raster(path, cfg=cfg))


def run_gis_sample_raster(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    points = [(float(point[0]), float(point[1])) for point in args.point]
    return _run_gis_report(args, lambda: sample_raster(path, points, cfg=cfg))


def run_gis_clip_raster(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    bbox = (args.min_x, args.min_y, args.max_x, args.max_y)
    return _run_gis_report(args, lambda: clip_raster_bbox(path, bbox, cfg=cfg))


def run_gis_vector_raster(args: argparse.Namespace, cfg: Settings) -> int:
    vector_path = _resolve_project_path(args.vector_path, cfg)
    raster_path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: analyze_vector_raster_intersection(vector_path, raster_path, cfg=cfg, band=args.band),
    )


def run_gis_zonal_stats(args: argparse.Namespace, cfg: Settings) -> int:
    vector_path = _resolve_project_path(args.vector_path, cfg)
    raster_path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: compute_zonal_statistics(vector_path, raster_path, cfg=cfg, band=args.band),
    )


def run_gis_resample_raster(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: resample_raster(path, width=args.width, height=args.height, method=args.method, cfg=cfg),
    )


def run_gis_derive_terrain(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: derive_raster_terrain(
            path,
            band=args.band,
            elevation_units=args.elevation_units,
            cfg=cfg,
        ),
    )


def run_gis_derive_hillshade(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: derive_raster_hillshade(
            path,
            band=args.band,
            elevation_units=args.elevation_units,
            azimuth_degrees=args.azimuth,
            altitude_degrees=args.altitude,
            cfg=cfg,
        ),
    )


def run_gis_condition_depressions(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: condition_raster_depressions(
            path,
            band=args.band,
            elevation_units=args.elevation_units,
            maximum_fill_depth_m=args.maximum_fill_depth_m,
            cfg=cfg,
        ),
    )


def run_gis_derive_d8_flow(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.raster_path, cfg)
    return _run_gis_report(
        args,
        lambda: derive_raster_d8_flow(
            path,
            band=args.band,
            elevation_units=args.elevation_units,
            elevation_tolerance_m=args.elevation_tolerance_m,
            gradient_tie_tolerance=args.gradient_tie_tolerance,
            cfg=cfg,
        ),
    )


def run_gis_validate_corpus(args: argparse.Namespace, cfg: Settings) -> int:
    path = _resolve_project_path(args.manifest_path, cfg)
    return _run_gis_report(args, lambda: validate_real_gis_corpus(path, cfg=cfg))


def run_gis_map_export_manifest(args: argparse.Namespace, cfg: Settings) -> int:
    output_path = _resolve_project_path(args.output_path, cfg)
    layers = [
        {
            "path": str(_resolve_project_path(path_value, cfg)),
            "crs": crs,
            "name": Path(path_value).name,
            "role": "source",
        }
        for path_value, crs in args.layer
    ]
    return _run_gis_report(
        args,
        lambda: create_map_export_manifest(
            cfg=cfg,
            output_path=output_path,
            layers=layers,
            extent=list(args.extent),
            output_crs=args.output_crs,
            renderer=args.renderer,
            renderer_version=args.renderer_version,
        ),
    )


def run_ocr_analyze(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.image_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = analyze_image_ocr(
            path,
            cfg=cfg,
            language=args.language,
            layout_mode=args.layout,
            tesseract_path=args.tesseract_path,
            timeout_seconds=args.timeout_seconds,
        )
    except OCRAnalysisError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if not str(result.get("status", "")).startswith("ERROR_") else 2


def run_ocr_structure(args: argparse.Namespace, cfg: Settings) -> int:
    path = Path(args.report_path)
    if not path.is_absolute():
        path = cfg.project_root / path
    try:
        result = reconstruct_ocr_report(path, kind=args.kind, cfg=cfg)
    except OCRStructureError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0 if result.get("status") != "OCR_STRUCTURE_UNRESOLVED" else 2


def run_flagpole_inventory_generate(args: argparse.Namespace, cfg: Settings) -> int:
    output = Path(args.output)
    if not output.is_absolute():
        output = cfg.project_root / output
    inventory = write_flagpole_inventory(cfg.project_root, output)
    emit_payload(
        {
            "status": "FLAGPOLE_INVENTORY_WRITTEN",
            "output": str(output),
            "source_sha": inventory["source_sha"],
            "file_count": inventory["file_count"],
            "inventory_sha256": inventory["inventory_sha256"],
        },
        args.format,
    )
    return 0


def run_flagpole_inventory_verify(args: argparse.Namespace, cfg: Settings) -> int:
    errors: list[str] = []
    if args.require_clean:
        errors.extend(f"DIRTY_PATH:{path}" for path in dirty_paths(cfg.project_root))
    schema_path = cfg.project_root / "schemas" / "flagpole_inventory.schema.json"
    if args.inventory:
        path = Path(args.inventory)
        if not path.is_absolute():
            path = cfg.project_root / path
        mode = "RECEIPT"
        try:
            inventory = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            inventory = None
            errors.append("INVENTORY_NOT_JSON")
        if inventory is not None and not isinstance(inventory, dict):
            errors.append("INVENTORY_NOT_OBJECT")
        if isinstance(inventory, dict):
            # Semantic verification assumes the schema's shape; a malformed receipt stops at its schema errors.
            schema_errors = validate_flagpole_inventory_schema(inventory, schema_path)
            errors.extend(schema_errors)
            if not schema_errors:
                errors.extend(verify_flagpole_receipt(cfg.project_root, inventory))
    else:
        inventory = generate_flagpole_inventory(cfg.project_root)
        mode = "HEAD_SELF_CONSISTENCY"
        errors.extend(validate_flagpole_inventory_schema(inventory, schema_path))
        errors.extend(validate_flagpole_inventory(cfg.project_root, inventory))
    emit_payload(
        {
            "status": "FLAGPOLE_INVENTORY_OK" if not errors else "FLAGPOLE_INVENTORY_FAILED",
            "mode": mode,
            "source_sha": inventory.get("source_sha") if isinstance(inventory, dict) else None,
            "file_count": inventory.get("file_count") if isinstance(inventory, dict) else None,
            "errors": errors,
        },
        args.format,
    )
    for error in errors if args.format == "text" else []:
        print(error, file=sys.stderr)
    return 0 if not errors else 2


def _doctor_check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _safe_payload_count(table: str, cfg: Settings) -> int:
    if not cfg.sqlite_path.exists():
        return 0
    return len(list_payloads(table, cfg.sqlite_path))


def build_doctor_report(cfg: Settings) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    recommendations: list[str] = []

    checks.append(
        _doctor_check(
            "project_root",
            "PASS" if cfg.project_root.exists() else "FAIL",
            str(cfg.project_root),
        )
    )
    missing_dirs = [
        str(path)
        for path in [cfg.data_dir, cfg.input_dir, cfg.processed_dir, cfg.export_dir, cfg.index_dir]
        if not path.exists()
    ]
    checks.append(
        _doctor_check(
            "data_directories",
            "PASS" if not missing_dirs else "WARN",
            "all present" if not missing_dirs else f"missing={len(missing_dirs)}",
        )
    )
    if missing_dirs:
        recommendations.append("Run `mucaro ingest data/input` or start the app once to initialize local data directories.")

    sources = get_sources(cfg)
    chunks = []
    try:
        from .ingestion import get_chunks

        chunks = get_chunks(cfg)
    except Exception:
        chunks = []
    checks.append(
        _doctor_check(
            "source_library",
            "PASS" if sources and chunks else "WARN",
            f"sources={len(sources)} chunks={len(chunks)}",
        )
    )
    if not sources or not chunks:
        recommendations.append("Ingest approved local files before asking source-grounded questions.")

    index = get_index_status(cfg)
    semantic_store = index.get("semantic_store", {})
    checks.append(
        _doctor_check(
            "persistent_index",
            "PASS" if index.get("status") == "INDEX_OK" else "WARN",
            str(index.get("status")),
        )
    )
    if index.get("status") != "INDEX_OK":
        recommendations.append("Run `mucaro sources reindex` after ingesting sources to repair the persistent index.")
    checks.append(
        _doctor_check(
            "semantic_store",
            "PASS" if isinstance(semantic_store, dict) and semantic_store.get("status") == "SEMANTIC_STORE_OK" else "WARN",
            str(semantic_store.get("status") if isinstance(semantic_store, dict) else "SEMANTIC_STORE_UNKNOWN"),
        )
    )
    if not isinstance(semantic_store, dict) or semantic_store.get("status") != "SEMANTIC_STORE_OK":
        recommendations.append("Run `mucaro sources reindex` after ingesting sources to repair the semantic store.")

    try:
        grants = load_directory_grants(cfg)
        checks.append(_doctor_check("directory_permissions", "PASS", f"grants={len(grants)}"))
    except DirectoryPermissionError as exc:
        grants = []
        checks.append(_doctor_check("directory_permissions", "FAIL", exc.status))
        recommendations.append("Repair or remove the directory permission ledger under data/processed.")

    live_mode = os.getenv("MUCARO_AI_LIVE_MODE") == "1"
    try:
        configured_model_provider = model_provider_status(cfg.default_model_provider)
    except ModelProviderError as exc:
        configured_model_provider = {
            "name": cfg.default_model_provider,
            "status": exc.status,
            "live_mode_enabled": live_mode,
            "api_key_configured": None,
            "dependency_available": None,
            "warnings": [exc.detail],
        }
    provider_status = str(configured_model_provider.get("status"))
    if provider_status == "MODEL_PROVIDER_READY":
        model_status = "PASS"
    elif provider_status in {"MODEL_PROVIDER_DISABLED", "MODEL_PROVIDER_NOT_READY"}:
        model_status = "WARN"
    else:
        model_status = "FAIL"
    model_detail = (
        f"{provider_status} provider={configured_model_provider.get('name')} "
        f"live_mode={configured_model_provider.get('live_mode_enabled')} "
        f"api_key_configured={configured_model_provider.get('api_key_configured')} "
        f"dependency_available={configured_model_provider.get('dependency_available')}"
    )
    if provider_status in {"MODEL_PROVIDER_DISABLED", "MODEL_PROVIDER_NOT_READY"}:
        if configured_model_provider.get("name") == "anthropic":
            recommendations.append(
                "For live Anthropic mode, install `.[anthropic]`, set MUCARO_AI_LIVE_MODE=1, and set ANTHROPIC_API_KEY."
            )
        else:
            recommendations.append("Inspect `mucaro models list` and select a ready provider.")
    elif model_status == "FAIL":
        recommendations.append("Set MUCARO_AI_MODEL_PROVIDER to a supported provider from `mucaro models list`.")
    checks.append(_doctor_check("model_provider", model_status, model_detail))

    checks.append(
        _doctor_check(
            "operator_auth",
            "PASS" if cfg.operator_token else "WARN",
            "enabled" if cfg.operator_token else "disabled",
        )
    )
    if not cfg.operator_token:
        recommendations.append("Set MUCARO_AI_OPERATOR_TOKEN when exposing the local API beyond a private machine.")
    security = build_security_report(cfg)
    security_check_status = "FAIL" if security["status"] == "SECURITY_FAIL" else "WARN" if security["status"] == "SECURITY_WARN" else "PASS"
    checks.append(_doctor_check("security_profile", security_check_status, security["status"]))
    recommendations.extend(security.get("recommendations", []))

    conversation_count = _safe_payload_count("conversations", cfg)
    run_count = _safe_payload_count("runs", cfg)
    memory_payload = list_memory_items(cfg)
    checks.append(_doctor_check("assistant_history", "PASS", f"conversations={conversation_count} runs={run_count}"))
    checks.append(
        _doctor_check(
            "assistant_memory",
            "PASS",
            f"memories={memory_payload.get('memory_count', 0)} enabled={memory_payload.get('enabled_count', 0)}",
        )
    )
    tools = list_tools().get("tools", [])
    checks.append(_doctor_check("tool_runtime", "PASS" if tools else "WARN", f"allowed_tools={len(tools)}"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "DOCTOR_FAIL" if failed else "DOCTOR_WARN" if warned else "DOCTOR_OK"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "summary": {
            "source_count": len(sources),
            "chunk_count": len(chunks),
            "index_status": index.get("status"),
            "semantic_store_status": semantic_store.get("status") if isinstance(semantic_store, dict) else "SEMANTIC_STORE_UNKNOWN",
            "directory_grant_count": len(grants),
            "conversation_count": conversation_count,
            "run_count": run_count,
            "memory_count": memory_payload.get("memory_count", 0),
            "enabled_memory_count": memory_payload.get("enabled_count", 0),
            "tool_count": len(tools),
            "model_provider": cfg.default_model_provider,
            "live_mode": live_mode,
            "model_provider_status": provider_status,
            "auth_required": bool(cfg.operator_token),
            "deployment_profile": cfg.deployment_profile,
            "security_status": security.get("status"),
        },
        "checks": checks,
        "recommendations": sorted(set(recommendations)),
    }


def run_models_list(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    emit_payload({"status": "MODEL_PROVIDERS_OK", "providers": list_model_providers()}, args.format)
    return 0


def run_models_status(args: argparse.Namespace, cfg: Settings) -> int:
    provider = args.provider or cfg.default_model_provider
    result = {"status": "MODEL_PROVIDER_STATUS", "provider": model_provider_status(provider)}
    emit_payload(result, args.format)
    return 0


def run_doctor(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_doctor_report(cfg)
    emit_payload(report, args.format)
    if args.strict and report["status"] != "DOCTOR_OK":
        return 2
    return 0


def run_audit_product(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_product_audit(cfg)
    emit_payload(report, args.format)
    if args.strict and report["status"] != "PRODUCT_AUDIT_OK":
        return 2
    return 0


def run_audit_roadmap(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_product_roadmap(cfg)
    emit_payload(report, args.format)
    if args.strict and report["status"] != "PRODUCT_ROADMAP_OK":
        return 2
    return 0


def run_eval_retrieval(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    report = build_retrieval_eval_report(
        fixture_path=args.fixture,
        top_k=args.top_k,
        min_hybrid_recall=args.min_hybrid_recall,
        min_no_context_accuracy=args.min_no_context_accuracy,
        min_semantic_recall=args.min_semantic_recall,
        min_semantic_no_context=args.min_semantic_no_context,
        min_hybrid_precision=args.min_hybrid_precision,
        min_semantic_precision=args.min_semantic_precision,
        min_expected_binding_recall=args.min_expected_binding_recall,
        min_multi_binding_full_hit=args.min_multi_binding_full_hit,
        min_distractor_free_rate=args.min_distractor_free_rate,
        min_category_recall=args.min_category_recall,
        min_category_binding_recall=args.min_category_binding_recall,
        min_source_formats=args.min_source_formats,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == "RETRIEVAL_EVAL_OK" else 2


def run_package_status(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_package_status(cfg.project_root)
    emit_payload(report, args.format)
    if report["status"] == "PACKAGE_FAIL":
        return 2
    if args.strict and report["status"] != "PACKAGE_READY":
        return 2
    return 0


def run_package_build(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_artifacts(cfg.project_root, output_dir=args.output_dir, allow_dirty=args.allow_dirty)
    emit_payload(report, args.format)
    return 0 if report["status"] == "PACKAGE_ARTIFACT_READY" else 2


def run_package_verify(args: argparse.Namespace, cfg: Settings) -> int:
    report = verify_release_artifact(args.manifest, root=cfg.project_root, require_current_head=args.require_current_head)
    emit_payload(report, args.format)
    return 0 if report["status"] == "PACKAGE_ARTIFACT_VERIFIED" else 2


def run_package_macos_app(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify is not None:
        report = verify_macos_app_bundle(args.verify, root=cfg.project_root)
        emit_payload(report, args.format)
        return 0 if report["status"] == "MACOS_APP_BUNDLE_VERIFIED" else 2
    report = build_macos_app_bundle(
        cfg.project_root,
        output_dir=args.output_dir,
        python_executable=args.python_executable,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == "MACOS_APP_BUNDLE_READY" else 2


def run_package_app_store_audit(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_macos_app_store_readiness(cfg.project_root, app_path=args.app)
    emit_payload(report, args.format)
    if report["status"] == "MAC_APP_STORE_READINESS_FAIL":
        return 2
    if args.strict:
        return 2
    return 0


def run_package_portable_runtime(args: argparse.Namespace, cfg: Settings) -> int:
    manifest = args.manifest or (cfg.project_root / "packaging" / "runtime" / "macos-x86_64.json")
    try:
        if args.verify is not None:
            report = verify_materialized_runtime(cfg.project_root, args.verify, manifest)
        else:
            output_dir = args.output_dir or (cfg.export_dir / "portable_runtimes" / "macos-x86_64")
            report = materialize_portable_runtime(
                cfg.project_root,
                manifest,
                output_dir,
                archive_path=args.archive,
            )
    except PortableRuntimeSourceError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(report, args.format)
    return 0 if report["status"] == "PORTABLE_RUNTIME_VERIFIED" else 2


def run_package_portable_macos_app(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        if args.verify is not None:
            report = verify_portable_macos_app(args.verify, expected_head=args.expected_head)
        else:
            if args.runtime_dir is None:
                emit_error("ERROR_PORTABLE_RUNTIME_REQUIRED", "--runtime-dir is required when building.", args.format)
                return 2
            output_dir = args.output_dir or (cfg.export_dir / "portable_apps")
            runtime_manifest = args.runtime_manifest or (
                cfg.project_root / "packaging" / "runtime" / "macos-x86_64.json"
            )
            runtime_provenance = verify_materialized_runtime(
                cfg.project_root,
                args.runtime_dir,
                runtime_manifest,
            )
            if runtime_provenance["status"] != "PORTABLE_RUNTIME_VERIFIED":
                emit_error(
                    "ERROR_PORTABLE_RUNTIME_VERIFY",
                    ",".join(runtime_provenance["failed_checks"]),
                    args.format,
                )
                return 2
            report = build_portable_macos_app(
                cfg.project_root,
                args.runtime_dir,
                output_dir=output_dir,
                runtime_python=args.runtime_python,
                runtime_provenance={
                    "dependency_lock_sha256": runtime_provenance["dependency_lock_sha256"],
                    "receipt_sha256": runtime_provenance["receipt_sha256"],
                    "runtime_tree": runtime_provenance["runtime_tree"],
                    "source_manifest_sha256": runtime_provenance["source_manifest_sha256"],
                    "status": runtime_provenance["status"],
                },
            )
    except (PortableRuntimeError, PortableRuntimeSourceError) as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(report, args.format)
    return 0 if report["status"] == "PORTABLE_APP_VERIFIED" else 2


def run_package_portable_install(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    try:
        if args.action == "rollback":
            report = rollback_portable_macos_app(
                install_root=args.install_root,
                install_name=args.install_name,
            )
        else:
            if args.source_app is None:
                emit_error("ERROR_PORTABLE_INSTALL_SOURCE_REQUIRED", "--source-app is required for install.", args.format)
                return 2
            report = install_portable_macos_app(
                args.source_app,
                install_root=args.install_root,
                install_name=args.install_name,
            )
    except PortableInstallError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(report, args.format)
    return 0 if report["status"] in {"PORTABLE_INSTALL_OK", "PORTABLE_ROLLBACK_OK"} else 2


def run_package_distribution_manifest(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify is not None:
        report = verify_distribution_manifest(
            args.verify,
            root=cfg.project_root,
            expected_sha256=args.expected_sha256,
            require_current_head=args.require_current_head,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "DISTRIBUTION_MANIFEST_VERIFIED" else 2

    report = build_distribution_manifest(
        cfg.project_root,
        output_path=args.output,
        save=args.save or args.output is not None,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == "DISTRIBUTION_MANIFEST_READY" else 2


def run_release_state(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_state_report(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
    )
    emit_payload(report, args.format)
    if args.strict and report["status"] != "RELEASE_STATE_READY":
        return 2
    return 0


def build_release_governance_report(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    release_state = build_release_state_report(
        cfg,
        include_github=True,
        github_workflow=github_workflow,
    )
    github_actions = build_github_actions_diagnostic_status(
        cfg,
        workflow_name=github_workflow,
        probe_workflow_name=probe_workflow,
    )
    product_audit = build_product_audit(cfg)
    package = build_package_status(cfg.project_root)
    git_payload = release_state.get("git", {}) if isinstance(release_state.get("git"), dict) else {}
    head_sha = git_payload.get("head_sha")
    receipts = list_release_receipts(cfg.project_root)
    current_head_receipts = [row for row in receipts if row.get("head_sha") == head_sha]
    states = release_state.get("states", [])
    github_state = next(
        (state for state in states if isinstance(state, dict) and state.get("state") == "GITHUB_GOVERNANCE_OPEN"),
        {},
    )
    governed_state = next(
        (state for state in states if isinstance(state, dict) and state.get("state") == "GOVERNED_RELEASE_READY"),
        {},
    )
    startup_blocker = github_actions.get("startup_blocker", {})
    support_summary = github_actions.get("support_summary", {})
    next_commands = [str(command) for command in release_state.get("next_commands", [])]
    next_actions: list[str] = []
    for action in github_actions.get("next_actions", []):
        next_actions.append(str(action))
    governed_ready = release_state.get("status") == "RELEASE_STATE_READY" and governed_state.get("status") == "PASS"
    return {
        "status": "GOVERNANCE_REPORT_READY" if governed_ready else "GOVERNANCE_REPORT_OPEN",
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "current_state": release_state.get("current_state"),
        "head_sha": head_sha,
        "tags_at_head": git_payload.get("tags_at_head", []),
        "release_state_status": release_state.get("status"),
        "product_audit_status": product_audit.get("status"),
        "product_audit_counts": product_audit.get("counts"),
        "package_status": package.get("status"),
        "certification_status": release_state.get("certification_status"),
        "github_actions_status": github_actions.get("status"),
        "github_governance_evidence": github_state.get("evidence"),
        "governed_release_evidence": governed_state.get("evidence"),
        "startup_blocker": startup_blocker,
        "github_support_summary": support_summary,
        "git": git_payload,
        "release_states": release_state.get("states", []),
        "github_actions": github_actions,
        "current_head_receipts_summary": summarize_release_receipts(current_head_receipts),
        "current_head_receipts": current_head_receipts,
        "next_commands": sorted(set(next_commands)),
        "next_actions": sorted(set(next_actions)),
    }


def write_release_governance_report(
    cfg: Settings,
    report: dict[str, Any],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    head_sha = str(report.get("head_sha") or "unknown")
    path = output_path or cfg.project_root / GOVERNANCE_REPORT_DIR / f"governance-report-{head_sha[:12]}.json"
    if not path.is_absolute():
        path = cfg.project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": GOVERNANCE_REPORT_SCHEMA_VERSION,
        "created_utc": _utc_now(),
        "report": report,
    }
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": GOVERNANCE_REPORT_SCHEMA_VERSION,
    }


def ensure_release_governance_report_for_support_bundle(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    if not head_ok or not _is_hex_sha(head_sha):
        return {
            "status": "GOVERNANCE_REPORT_ENSURE_FAILED",
            "path": None,
            "created": False,
            "checks": [
                _certification_gate(
                    "current_head",
                    "FAIL",
                    head_sha if head_ok else head_sha or "unavailable",
                )
            ],
        }

    path = _expected_handoff_path(cfg.project_root, GOVERNANCE_REPORT_DIR, "governance-report", head_sha)
    existing = verify_release_governance_report(cfg, path, require_current_head=True)
    if existing.get("status") == "GOVERNANCE_REPORT_VERIFIED":
        return {
            "status": "GOVERNANCE_REPORT_READY_FOR_SUPPORT",
            "path": str(path),
            "created": False,
            "verification": existing,
            "checks": [_certification_gate("governance_report", "PASS", f"verified {path}")],
        }

    report = build_release_governance_report(
        cfg,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    export = write_release_governance_report(cfg, report)
    verification = verify_release_governance_report(
        cfg,
        Path(str(export["path"])),
        require_current_head=True,
        expected_sha256=str(export["sha256"]),
    )
    verified = verification.get("status") == "GOVERNANCE_REPORT_VERIFIED"
    return {
        "status": "GOVERNANCE_REPORT_READY_FOR_SUPPORT" if verified else "GOVERNANCE_REPORT_ENSURE_FAILED",
        "path": str(export["path"]),
        "created": True,
        "export": export,
        "verification": verification,
        "checks": [
            _certification_gate(
                "governance_report",
                "PASS" if verified else "FAIL",
                f"{verification.get('status')} {export['path']}",
            )
        ],
    }


def _parse_utc_timestamp(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str) or not value:
        return False, "missing created_utc"
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False, f"invalid created_utc={value}"
    if parsed.tzinfo is None:
        return False, "created_utc missing timezone"
    return True, value


def _is_hex_sha(value: Any, *, length: int = 40) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def verify_release_governance_report(
    cfg: Settings,
    artifact_path: Path,
    *,
    require_current_head: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    path = artifact_path if artifact_path.is_absolute() else cfg.project_root / artifact_path
    checks: list[dict[str, str]] = []
    artifact: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None

    if path.exists() and path.is_file():
        checks.append(_certification_gate("artifact_exists", "PASS", str(path)))
        artifact_sha256 = _sha256_file(path)
        artifact_size_bytes = path.stat().st_size
        checks.append(_certification_gate("artifact_sha256", "PASS", artifact_sha256))
        if expected_sha256:
            expected = expected_sha256.strip().lower()
            checks.append(
                _certification_gate(
                    "expected_sha256",
                    "PASS" if artifact_sha256 == expected else "FAIL",
                    f"expected={expected} actual={artifact_sha256}",
                )
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            checks.append(_certification_gate("artifact_json", "FAIL", str(exc)))
            loaded = None
        if isinstance(loaded, dict):
            artifact = loaded
            checks.append(_certification_gate("artifact_json", "PASS", "valid JSON object"))
        elif loaded is not None:
            checks.append(_certification_gate("artifact_json", "FAIL", "top-level JSON value is not an object"))
    else:
        checks.append(_certification_gate("artifact_exists", "FAIL", str(path)))

    report: dict[str, Any] | None = None
    if artifact is not None:
        schema_version = artifact.get("schema_version")
        checks.append(
            _certification_gate(
                "schema_version",
                "PASS" if schema_version == GOVERNANCE_REPORT_SCHEMA_VERSION else "FAIL",
                str(schema_version),
            )
        )
        created_ok, created_detail = _parse_utc_timestamp(artifact.get("created_utc"))
        checks.append(_certification_gate("created_utc", "PASS" if created_ok else "FAIL", created_detail))
        raw_report = artifact.get("report")
        if isinstance(raw_report, dict):
            report = raw_report
            checks.append(_certification_gate("report_payload", "PASS", "report object present"))
        else:
            checks.append(_certification_gate("report_payload", "FAIL", "report object missing"))

    head_sha: str | None = None
    if report is not None:
        raw_head_sha = report.get("head_sha")
        if _is_hex_sha(raw_head_sha):
            head_sha = str(raw_head_sha)
            checks.append(_certification_gate("report_head_sha", "PASS", head_sha))
        else:
            checks.append(_certification_gate("report_head_sha", "FAIL", str(raw_head_sha)))

        status = report.get("status")
        checks.append(
            _certification_gate(
                "report_status",
                "PASS" if status in {"GOVERNANCE_REPORT_READY", "GOVERNANCE_REPORT_OPEN"} else "FAIL",
                str(status),
            )
        )
        current_state = report.get("current_state")
        checks.append(
            _certification_gate(
                "current_state",
                "PASS" if isinstance(current_state, str) and bool(current_state) else "FAIL",
                str(current_state),
            )
        )
        receipt_summary = report.get("current_head_receipts_summary")
        checks.append(
            _certification_gate(
                "receipt_summary",
                "PASS" if isinstance(receipt_summary, dict) else "FAIL",
                "present" if isinstance(receipt_summary, dict) else "missing",
            )
        )
        startup_blocker = report.get("startup_blocker")
        checks.append(
            _certification_gate(
                "startup_blocker",
                "PASS" if isinstance(startup_blocker, dict) else "FAIL",
                "present" if isinstance(startup_blocker, dict) else "missing",
            )
        )

    current_head: str | None = None
    if require_current_head:
        head_ok, current_head_output = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = current_head_output if head_ok else None
        checks.append(
            _certification_gate(
                "current_head_binding",
                "PASS" if head_ok and head_sha == current_head_output else "FAIL",
                f"report_head={head_sha} current_head={current_head_output if head_ok else current_head_output or 'unavailable'}",
            )
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "GOVERNANCE_REPORT_VERIFIED" if not failed else "GOVERNANCE_REPORT_VERIFY_FAILED",
        "project_root": str(cfg.project_root),
        "artifact": {
            "path": str(path),
            "sha256": artifact_sha256,
            "size_bytes": artifact_size_bytes,
            "schema_version": artifact.get("schema_version") if artifact else None,
            "created_utc": artifact.get("created_utc") if artifact else None,
        },
        "head_sha": head_sha,
        "current_head": current_head,
        "checks": checks,
    }


def _release_state_row(release_state: dict[str, Any], state_name: str) -> dict[str, Any]:
    states = release_state.get("states", [])
    if not isinstance(states, list):
        return {}
    return next((state for state in states if isinstance(state, dict) and state.get("state") == state_name), {})


def _expected_handoff_path(root: Path, directory: Path, prefix: str, head_sha: str | None) -> Path:
    head_prefix = str(head_sha or "unknown")[:12]
    return root / directory / f"{prefix}-{head_prefix}.json"


def _remote_branch_check(
    cfg: Settings,
    branch: str | None,
    head_sha: str | None,
    *,
    upstream: str | None = None,
) -> dict[str, Any]:
    if not branch:
        return _certification_gate("remote_branch_binding", "WARN", "branch unavailable")
    if not head_sha:
        return _certification_gate("remote_branch_binding", "WARN", "head SHA unavailable")
    remote_name = "origin"
    remote_branch = branch
    if upstream:
        normalized_upstream = upstream.removeprefix("refs/remotes/")
        if "/" not in normalized_upstream:
            return _certification_gate(
                "remote_branch_binding",
                "WARN",
                f"upstream ref is not remote-qualified: {upstream}",
            )
        remote_name, remote_branch = normalized_upstream.split("/", 1)
    remote_ref = f"refs/heads/{remote_branch}"
    ok, output = _git_output(cfg, ["ls-remote", remote_name, remote_ref])
    if not ok:
        return _certification_gate("remote_branch_binding", "WARN", output or "remote branch lookup failed")
    remote_sha = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == remote_ref:
            remote_sha = parts[0]
            break
    if not remote_sha:
        return _certification_gate("remote_branch_binding", "FAIL", f"{remote_name}/{remote_branch} not found")
    return _certification_gate(
        "remote_branch_binding",
        "PASS" if remote_sha == head_sha else "FAIL",
        f"{remote_name}/{remote_branch}={remote_sha} head={head_sha}",
    )


def build_release_dashboard_report(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    release_state = build_release_state_report(
        cfg,
        include_github=include_github,
        github_workflow=github_workflow,
    )
    git_payload = release_state.get("git", {}) if isinstance(release_state.get("git"), dict) else {}
    head_sha = git_payload.get("head_sha") if isinstance(git_payload.get("head_sha"), str) else None
    branch = git_payload.get("branch") if isinstance(git_payload.get("branch"), str) else None
    receipts = list_release_receipts(cfg.project_root)
    all_current_head_receipts = [row for row in receipts if row.get("head_sha") == head_sha]
    receipt_payloads = release_state.get("receipts", {})
    expected_receipt_types = sorted(receipt_payloads) if isinstance(receipt_payloads, dict) else []
    current_head_receipts = [
        row for row in all_current_head_receipts if row.get("receipt_type") in expected_receipt_types
    ]
    additional_current_head_receipts = [
        row for row in all_current_head_receipts if row.get("receipt_type") not in expected_receipt_types
    ]
    receipt_summary = summarize_release_receipts(current_head_receipts)
    missing_receipts = [
        receipt_type
        for receipt_type in expected_receipt_types
        if not isinstance(receipt_payloads.get(receipt_type), dict)
    ]

    distribution_path = _expected_handoff_path(
        cfg.project_root,
        DISTRIBUTION_MANIFEST_DIR,
        "distribution-manifest",
        head_sha,
    )
    if distribution_path.exists():
        distribution_manifest = verify_distribution_manifest(
            distribution_path,
            root=cfg.project_root,
            require_current_head=True,
        )
    else:
        distribution_manifest = {
            "status": "DISTRIBUTION_MANIFEST_MISSING",
            "manifest_path": str(distribution_path),
            "checks": [_certification_gate("manifest_exists", "FAIL", str(distribution_path))],
        }

    governance_path = _expected_handoff_path(
        cfg.project_root,
        GOVERNANCE_REPORT_DIR,
        "governance-report",
        head_sha,
    )
    if governance_path.exists():
        governance_report = verify_release_governance_report(
            cfg,
            governance_path,
            require_current_head=True,
        )
    else:
        governance_report = {
            "status": "GOVERNANCE_REPORT_MISSING",
            "artifact": {"path": str(governance_path), "sha256": None, "size_bytes": None},
            "checks": [_certification_gate("artifact_exists", "FAIL", str(governance_path))],
        }

    github_actions: dict[str, Any] = {"status": "UNREQUESTED"}
    if include_github:
        github_actions = build_github_actions_diagnostic_status(
            cfg,
            workflow_name=github_workflow,
            probe_workflow_name=probe_workflow,
        )

    upstream = git_payload.get("upstream") if isinstance(git_payload.get("upstream"), str) else None
    remote_branch = _remote_branch_check(cfg, branch, head_sha, upstream=upstream)
    governed_state = _release_state_row(release_state, "GOVERNED_RELEASE_READY")
    github_state = _release_state_row(release_state, "GITHUB_GOVERNANCE_OPEN")
    receipt_expected_count = len(expected_receipt_types)
    receipts_complete = (
        receipt_summary.get("total") == receipt_expected_count
        and receipt_summary.get("valid") == receipt_expected_count
        and not missing_receipts
    )

    checks = [
        _certification_gate("current_head", "PASS" if head_sha else "FAIL", str(head_sha)),
        _certification_gate(
            "worktree_clean",
            "PASS" if git_payload.get("worktree_clean") is True else "FAIL",
            str(git_payload.get("worktree_clean")),
        ),
        _certification_gate(
            "upstream",
            "PASS" if git_payload.get("upstream") else "WARN",
            str(git_payload.get("upstream") or "missing"),
        ),
        remote_branch,
        _certification_gate(
            "tag_at_head",
            "PASS" if git_payload.get("tags_at_head") else "WARN",
            ",".join(git_payload.get("tags_at_head", [])) or "none",
        ),
        _certification_gate(
            "current_head_receipts",
            "PASS" if receipts_complete else "FAIL",
            f"expected={receipt_expected_count} total={receipt_summary.get('total')} "
            f"valid={receipt_summary.get('valid')} missing={','.join(missing_receipts) or 'none'}",
        ),
        _certification_gate(
            "distribution_manifest",
            "PASS" if distribution_manifest.get("status") == "DISTRIBUTION_MANIFEST_VERIFIED" else "FAIL",
            str(distribution_manifest.get("status")),
        ),
        _certification_gate(
            "governance_report",
            "PASS" if governance_report.get("status") == "GOVERNANCE_REPORT_VERIFIED" else "FAIL",
            str(governance_report.get("status")),
        ),
        _certification_gate(
            "release_state",
            "PASS" if release_state.get("status") == "RELEASE_STATE_READY" else "OPEN",
            str(release_state.get("current_state")),
        ),
    ]
    if include_github:
        checks.append(
            _certification_gate(
                "github_actions",
                "PASS" if github_actions.get("status") == "GITHUB_ACTIONS_DIAGNOSTIC_OK" else "OPEN",
                str(github_actions.get("status")),
            )
        )

    hard_failed = [check for check in checks if check.get("status") == "FAIL"]
    open_checks = [check for check in checks if check.get("status") in {"OPEN", "WARN"}]
    dashboard_ready = (
        not hard_failed
        and not open_checks
        and release_state.get("status") == "RELEASE_STATE_READY"
        and governed_state.get("status") == "PASS"
    )
    blockers = [f"{check['name']}={check['status']}:{check['detail']}" for check in hard_failed + open_checks]
    if github_state.get("status") == "OPEN" and github_state.get("evidence"):
        blockers.append(f"github_governance={github_state.get('evidence')}")
    if include_github:
        for action in github_actions.get("next_actions", []):
            blockers.append(str(action))

    status = "RELEASE_DASHBOARD_READY" if dashboard_ready else "RELEASE_DASHBOARD_FAIL" if hard_failed else "RELEASE_DASHBOARD_OPEN"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "current_state": release_state.get("current_state"),
        "head_sha": head_sha,
        "tags_at_head": git_payload.get("tags_at_head", []),
        "release_state_status": release_state.get("status"),
        "distribution_manifest_status": distribution_manifest.get("status"),
        "distribution_manifest": distribution_manifest,
        "governance_report_status": governance_report.get("status"),
        "governance_report": governance_report,
        "github_actions_status": github_actions.get("status"),
        "github_actions": github_actions,
        "remote_branch_status": remote_branch.get("status"),
        "hosted_governance_status": (
            "OPEN"
            if github_state.get("status") == "OPEN"
            else "PASS"
            if release_state.get("status") == "RELEASE_STATE_READY"
            else "UNVERIFIED"
        ),
        "current_head_receipts_summary": receipt_summary,
        "current_head_receipts": current_head_receipts,
        "additional_current_head_receipts": additional_current_head_receipts,
        "checks": checks,
        "blockers": sorted(set(blockers)),
        "release_states": release_state.get("states", []),
        "next_commands": sorted(set(str(command) for command in release_state.get("next_commands", []))),
    }


def run_release_dashboard(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_dashboard_report(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "RELEASE_DASHBOARD_FAIL":
        return 2
    if args.strict and report["status"] != "RELEASE_DASHBOARD_READY":
        return 2
    return 0


def _hosted_step(
    step_id: str,
    status: str,
    evidence: str,
    command: str,
    owner: str = "operator",
) -> dict[str, str]:
    return {
        "step_id": step_id,
        "status": status,
        "owner": owner,
        "evidence": evidence,
        "command": command,
    }


def _check_status(checks: list[dict[str, Any]], name: str) -> str:
    for check in checks:
        if isinstance(check, dict) and check.get("name") == name:
            return str(check.get("status") or "UNVERIFIED")
    return "UNVERIFIED"


def _check_detail(checks: list[dict[str, Any]], name: str) -> str:
    for check in checks:
        if isinstance(check, dict) and check.get("name") == name:
            return str(check.get("detail") or "")
    return ""


def _open_if_not_pass(status: str) -> str:
    return "PASS" if status == "PASS" else "OPEN" if status in {"WARN", "FAIL", "UNVERIFIED"} else status


def build_release_hosted_governance_report(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    dashboard = build_release_dashboard_report(
        cfg,
        include_github=True,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    github_actions = dashboard.get("github_actions", {})
    if not isinstance(github_actions, dict):
        github_actions = {}
    checks = [check for check in github_actions.get("checks", []) if isinstance(check, dict)]
    runs = github_actions.get("runs", {})
    if not isinstance(runs, dict):
        runs = {}
    support_summary = github_actions.get("support_summary", {})
    if not isinstance(support_summary, dict):
        support_summary = {}
    startup_blocker = github_actions.get("startup_blocker", {})
    if not isinstance(startup_blocker, dict):
        startup_blocker = {}
    billing = github_actions.get("billing", {})
    if not isinstance(billing, dict):
        billing = {}
    branch_protection = github_actions.get("branch_protection", {})
    if not isinstance(branch_protection, dict):
        branch_protection = {}

    head_sha = str(dashboard.get("head_sha") or "")
    support_bundle_path = cfg.project_root / SUPPORT_BUNDLE_DIR / f"support-bundle-{head_sha[:12]}.json" if head_sha else None
    support_bundle_status = "UNVERIFIED"
    support_bundle_evidence = "support bundle path unavailable"
    if support_bundle_path is not None:
        support_bundle_check = verify_release_support_bundle(cfg, support_bundle_path, require_current_head=True)
        support_bundle_status = "PASS" if support_bundle_check.get("status") == "SUPPORT_BUNDLE_VERIFIED" else "OPEN"
        support_bundle_evidence = f"{support_bundle_check.get('status')} {support_bundle_path}"

    actions_enabled_status = _check_status(checks, "actions_enabled")
    workflow_token_status = _check_status(checks, "workflow_token_permissions")
    ci_status = _check_status(checks, "latest_ci")
    probe_status = _check_status(checks, "startup_probe")
    check_suite_status = _check_status(checks, "github_actions_check_suites_before_runs")
    billing_status = _check_status(checks, "actions_billing")
    branch_protection_status = _check_status(checks, "branch_protection_api")
    current_head_startup_run_ids = support_summary.get("current_head_startup_run_ids", [])
    if not isinstance(current_head_startup_run_ids, list):
        current_head_startup_run_ids = []

    required_scope = billing.get("required_scope")
    billing_command = (
        f"gh auth refresh -h github.com -s {required_scope}"
        if required_scope
        else "Inspect GitHub Actions billing/minutes in account settings."
    )

    steps = [
        _hosted_step(
            "actions_repository_policy",
            _open_if_not_pass(actions_enabled_status),
            _check_detail(checks, "actions_enabled"),
            "Open GitHub repository Settings -> Actions -> General and confirm Actions is enabled for this repository.",
        ),
        _hosted_step(
            "workflow_token_policy",
            _open_if_not_pass(workflow_token_status),
            _check_detail(checks, "workflow_token_permissions"),
            "Open GitHub repository Settings -> Actions -> General and confirm workflow token permissions are acceptable.",
        ),
        _hosted_step(
            "ci_workflow_registration",
            _open_if_not_pass(_check_status(checks, f"workflow_registered:{github_workflow}")),
            _check_detail(checks, f"workflow_registered:{github_workflow}"),
            f"gh workflow view {github_workflow!r}",
        ),
        _hosted_step(
            "startup_probe_registration",
            _open_if_not_pass(_check_status(checks, f"workflow_registered:{probe_workflow}")),
            _check_detail(checks, f"workflow_registered:{probe_workflow}"),
            f"gh workflow view {probe_workflow!r}",
        ),
        _hosted_step(
            "ci_run_admission",
            _open_if_not_pass(ci_status),
            _check_detail(checks, "latest_ci"),
            f"gh run view {support_summary.get('ci_run_id') or '<ci_run_id>'} --json databaseId,name,status,conclusion,headSha,jobs,url",
        ),
        _hosted_step(
            "current_head_push_startup",
            "OPEN" if current_head_startup_run_ids else "PASS",
            f"run_ids={','.join(str(run_id) for run_id in current_head_startup_run_ids)}"
            if current_head_startup_run_ids
            else "no current-head push startup failures detected outside named workflow handles",
            f"gh run view {current_head_startup_run_ids[0]} --json databaseId,name,status,conclusion,headSha,jobs,url"
            if current_head_startup_run_ids
            else "gh run list --limit 10 --json databaseId,name,status,conclusion,headSha,url,event,createdAt",
        ),
        _hosted_step(
            "startup_probe_admission",
            _open_if_not_pass(probe_status),
            _check_detail(checks, "startup_probe"),
            f"gh run view {support_summary.get('startup_probe_run_id') or '<startup_probe_run_id>'} --json databaseId,name,status,conclusion,headSha,jobs,url",
        ),
        _hosted_step(
            "check_suite_admission",
            _open_if_not_pass(check_suite_status),
            _check_detail(checks, "github_actions_check_suites_before_runs"),
            f"gh api repos/{github_actions.get('repo') or '<owner/repo>'}/commits/{head_sha or '<head_sha>'}/check-suites",
        ),
        _hosted_step(
            "billing_visibility",
            _open_if_not_pass(billing_status),
            str(billing.get("error") or _check_detail(checks, "actions_billing")),
            billing_command,
        ),
        _hosted_step(
            "branch_protection",
            _open_if_not_pass(branch_protection_status),
            str(branch_protection.get("error") or _check_detail(checks, "branch_protection_api")),
            "Upgrade GitHub plan or make the repository public before enforcing private-repo branch protection.",
        ),
        _hosted_step(
            "support_bundle",
            support_bundle_status,
            support_bundle_evidence,
            "mucaro release support-bundle --save --format text",
        ),
    ]
    counts = {
        "pass": sum(1 for step in steps if step["status"] == "PASS"),
        "open": sum(1 for step in steps if step["status"] == "OPEN"),
        "blocked": sum(1 for step in steps if step["status"] == "BLOCKED"),
        "fail": sum(1 for step in steps if step["status"] == "FAIL"),
        "unverified": sum(1 for step in steps if step["status"] == "UNVERIFIED"),
        "total": len(steps),
    }
    hosted_ready = dashboard.get("hosted_governance_status") == "PASS" and github_actions.get("status") == "GITHUB_ACTIONS_DIAGNOSTIC_OK"
    status = "HOSTED_GOVERNANCE_READY" if hosted_ready and counts["pass"] == counts["total"] else "HOSTED_GOVERNANCE_OPEN"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "head_sha": head_sha,
        "tag_at_head": dashboard.get("tags_at_head", []),
        "release_dashboard_status": dashboard.get("status"),
        "release_state_status": dashboard.get("release_state_status"),
        "hosted_governance_status": dashboard.get("hosted_governance_status"),
        "github_actions_status": github_actions.get("status"),
        "support_classification": support_summary.get("classification"),
        "ci_run_id": support_summary.get("ci_run_id"),
        "ci_run_url": support_summary.get("ci_run_url"),
        "startup_probe_run_id": support_summary.get("startup_probe_run_id"),
        "startup_probe_run_url": support_summary.get("startup_probe_run_url"),
        "current_head_startup_runs": support_summary.get("current_head_startup_runs", []),
        "current_head_startup_run_ids": support_summary.get("current_head_startup_run_ids", []),
        "ci_jobs_count": support_summary.get("ci_jobs_count"),
        "startup_probe_jobs_count": support_summary.get("startup_probe_jobs_count"),
        "actions_enabled": support_summary.get("actions_enabled"),
        "allowed_actions": support_summary.get("allowed_actions"),
        "default_workflow_permissions": support_summary.get("default_workflow_permissions"),
        "billing_available": billing.get("available"),
        "billing_required_scope": billing.get("required_scope"),
        "branch_protection_available": branch_protection.get("available"),
        "startup_blocker": startup_blocker,
        "counts": counts,
        "steps": steps,
        "support_bundle": {
            "path": str(support_bundle_path) if support_bundle_path is not None else None,
            "status": support_bundle_status,
            "evidence": support_bundle_evidence,
        },
        "next_actions": sorted(set(str(action) for action in [*github_actions.get("next_actions", []), *(step["command"] for step in steps if step["status"] != "PASS")])),
    }


def run_release_hosted_governance(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_hosted_governance_report(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "HOSTED_GOVERNANCE_READY":
        return 0
    return 2 if args.strict else 0


def _completion_requirement_by_id(audit: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    for row in audit.get("requirements", []):
        if isinstance(row, dict) and row.get("requirement_id") == requirement_id:
            return row
    return {}


def _incident_packet_copy_lines(packet: dict[str, Any]) -> list[str]:
    lines = [
        "Mucaro AI hosted governance incident packet",
        f"head_sha={packet.get('head_sha')}",
        f"tag_at_head={_support_copy_value(packet.get('tag_at_head'))}",
        f"case_status={packet.get('case_status')}",
        f"hosted_governance_status={packet.get('hosted_governance_status')}",
        f"github_actions_status={packet.get('github_actions_status')}",
        f"support_classification={packet.get('support_classification')}",
        f"current_head_startup_run_ids={_support_copy_value(packet.get('current_head_startup_run_ids'))}",
        f"startup_blocker_causes={_support_copy_value(packet.get('startup_blocker_causes'))}",
        f"support_bundle_path={packet.get('support_bundle_path')}",
    ]
    for step in packet.get("open_steps", []):
        if isinstance(step, dict):
            lines.append(
                "open_step="
                f"{_support_copy_value(step.get('step_id'))} "
                f"status:{_support_copy_value(step.get('status'))} "
                f"evidence:{_support_copy_line_value(step.get('evidence'))} "
                f"command:{_support_copy_value(step.get('command'))}"
            )
    lines.extend(f"diagnostic_command={command}" for command in packet.get("diagnostic_commands", []))
    return lines


def build_release_incident_packet(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
    output_path: Path | None = None,
    save: bool = False,
) -> dict[str, Any]:
    hosted = build_release_hosted_governance_report(
        cfg,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    completion = build_completion_audit_report(
        cfg,
        include_github=True,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    support_bundle = hosted.get("support_bundle") if isinstance(hosted.get("support_bundle"), dict) else {}
    startup_blocker = hosted.get("startup_blocker") if isinstance(hosted.get("startup_blocker"), dict) else {}
    open_steps = [
        step
        for step in hosted.get("steps", [])
        if isinstance(step, dict) and step.get("status") != "PASS"
    ]
    diagnostic_commands = sorted(
        set(
            str(command)
            for command in [
                *hosted.get("next_actions", []),
                *(step.get("command") for step in open_steps if isinstance(step, dict)),
                "mucaro release hosted-governance --format text",
                "mucaro audit completion --include-github --format text",
                "mucaro certify github-actions --format text",
            ]
            if command
        )
    )
    hosted_requirement = _completion_requirement_by_id(completion, "hosted_governance")
    incident_open = hosted.get("status") != "HOSTED_GOVERNANCE_READY"
    checks = [
        _certification_gate("hosted_report_present", "PASS" if hosted.get("status") else "FAIL", str(hosted.get("status"))),
        _certification_gate("completion_audit_present", "PASS" if completion.get("status") else "FAIL", str(completion.get("status"))),
        _certification_gate("head_sha", "PASS" if _is_hex_sha(hosted.get("head_sha")) else "FAIL", str(hosted.get("head_sha"))),
        _certification_gate("open_steps", "PASS" if open_steps or not incident_open else "FAIL", f"count={len(open_steps)}"),
        _certification_gate("diagnostic_commands", "PASS" if diagnostic_commands else "FAIL", f"count={len(diagnostic_commands)}"),
    ]
    failed = [check for check in checks if check.get("status") == "FAIL"]
    packet = {
        "status": "HOSTED_INCIDENT_PACKET_FAILED"
        if failed
        else "HOSTED_INCIDENT_PACKET_READY"
        if incident_open
        else "HOSTED_INCIDENT_PACKET_NOT_REQUIRED",
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "head_sha": hosted.get("head_sha"),
        "tag_at_head": hosted.get("tag_at_head", []),
        "case_status": hosted.get("status"),
        "hosted_governance_status": hosted.get("hosted_governance_status"),
        "github_actions_status": hosted.get("github_actions_status"),
        "support_classification": hosted.get("support_classification"),
        "current_head_startup_run_ids": hosted.get("current_head_startup_run_ids", []),
        "startup_blocker_status": startup_blocker.get("status"),
        "startup_blocker_causes": startup_blocker.get("suspected_causes", []),
        "support_bundle_path": support_bundle.get("path"),
        "support_bundle_status": support_bundle.get("status"),
        "completion_audit_status": completion.get("status"),
        "completion_hosted_governance": hosted_requirement,
        "open_steps": open_steps,
        "diagnostic_commands": diagnostic_commands,
        "hosted_governance": hosted,
        "completion_audit": {
            "status": completion.get("status"),
            "current_state": completion.get("current_state"),
            "readiness_percent": completion.get("readiness_percent"),
            "counts": completion.get("counts"),
            "hosted_governance": hosted_requirement,
        },
        "checks": checks,
    }
    packet["copy_lines"] = _incident_packet_copy_lines(packet)
    if save or output_path is not None:
        packet["export"] = write_release_incident_packet(cfg, packet, output_path=output_path)
    return packet


def write_release_incident_packet(
    cfg: Settings,
    packet: dict[str, Any],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    head_sha = str(packet.get("head_sha") or "unknown")
    path = output_path or cfg.project_root / HOSTED_INCIDENT_PACKET_DIR / f"incident-packet-{head_sha[:12]}.json"
    if not path.is_absolute():
        path = cfg.project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": HOSTED_INCIDENT_PACKET_SCHEMA_VERSION,
        "created_utc": _utc_now(),
        "packet": packet,
    }
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": HOSTED_INCIDENT_PACKET_SCHEMA_VERSION,
    }


def verify_release_incident_packet(
    cfg: Settings,
    artifact_path: Path,
    *,
    require_current_head: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    path = artifact_path if artifact_path.is_absolute() else cfg.project_root / artifact_path
    checks: list[dict[str, str]] = []
    artifact: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None

    if path.exists() and path.is_file():
        checks.append(_certification_gate("artifact_exists", "PASS", str(path)))
        artifact_sha256 = _sha256_file(path)
        artifact_size_bytes = path.stat().st_size
        checks.append(_certification_gate("artifact_sha256", "PASS", artifact_sha256))
        if expected_sha256:
            expected = expected_sha256.strip().lower()
            checks.append(
                _certification_gate(
                    "expected_sha256",
                    "PASS" if artifact_sha256 == expected else "FAIL",
                    f"expected={expected} actual={artifact_sha256}",
                )
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            loaded = None
            checks.append(_certification_gate("artifact_json", "FAIL", str(exc)))
        if isinstance(loaded, dict):
            artifact = loaded
            checks.append(_certification_gate("artifact_json", "PASS", "valid JSON object"))
        elif loaded is not None:
            checks.append(_certification_gate("artifact_json", "FAIL", "top-level JSON value is not an object"))
    else:
        checks.append(_certification_gate("artifact_exists", "FAIL", str(path)))

    packet: dict[str, Any] | None = None
    head_sha: str | None = None
    case_status: str | None = None
    hosted_governance_status: str | None = None
    support_classification: str | None = None
    current_head_startup_run_ids: list[Any] = []
    if artifact is not None:
        schema_version = artifact.get("schema_version")
        checks.append(
            _certification_gate(
                "schema_version",
                "PASS" if schema_version == HOSTED_INCIDENT_PACKET_SCHEMA_VERSION else "FAIL",
                str(schema_version),
            )
        )
        created_ok, created_detail = _parse_utc_timestamp(artifact.get("created_utc"))
        checks.append(_certification_gate("created_utc", "PASS" if created_ok else "FAIL", created_detail))
        raw_packet = artifact.get("packet")
        if isinstance(raw_packet, dict):
            packet = raw_packet
            checks.append(_certification_gate("packet_payload", "PASS", "packet object present"))
        else:
            checks.append(_certification_gate("packet_payload", "FAIL", "packet object missing"))

    if packet is not None:
        raw_head_sha = packet.get("head_sha")
        if _is_hex_sha(raw_head_sha):
            head_sha = str(raw_head_sha)
            checks.append(_certification_gate("packet_head_sha", "PASS", head_sha))
        else:
            checks.append(_certification_gate("packet_head_sha", "FAIL", str(raw_head_sha)))
        packet_status = str(packet.get("status") or "")
        checks.append(
            _certification_gate(
                "packet_status",
                "PASS" if packet_status in {"HOSTED_INCIDENT_PACKET_READY", "HOSTED_INCIDENT_PACKET_NOT_REQUIRED"} else "FAIL",
                packet_status,
            )
        )
        copy_lines = packet.get("copy_lines")
        checks.append(
            _certification_gate(
                "copy_lines",
                "PASS" if isinstance(copy_lines, list) and copy_lines else "FAIL",
                "present" if isinstance(copy_lines, list) and copy_lines else "missing",
            )
        )
        diagnostic_commands = packet.get("diagnostic_commands")
        checks.append(
            _certification_gate(
                "diagnostic_commands",
                "PASS" if isinstance(diagnostic_commands, list) and diagnostic_commands else "FAIL",
                "present" if isinstance(diagnostic_commands, list) and diagnostic_commands else "missing",
            )
        )
        case_status = str(packet.get("case_status") or "")
        hosted_governance_status = str(packet.get("hosted_governance_status") or "")
        support_classification = str(packet.get("support_classification") or "")
        startup_ids = packet.get("current_head_startup_run_ids", [])
        current_head_startup_run_ids = startup_ids if isinstance(startup_ids, list) else []

    current_head: str | None = None
    if require_current_head:
        head_ok, current_head_output = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = current_head_output if head_ok else None
        checks.append(
            _certification_gate(
                "current_head_binding",
                "PASS" if head_ok and head_sha == current_head_output else "FAIL",
                f"packet_head={head_sha} current_head={current_head_output if head_ok else current_head_output or 'unavailable'}",
            )
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "HOSTED_INCIDENT_PACKET_VERIFIED" if not failed else "HOSTED_INCIDENT_PACKET_VERIFY_FAILED",
        "project_root": str(cfg.project_root),
        "artifact_path": str(path),
        "artifact": {
            "path": str(path),
            "sha256": artifact_sha256,
            "size_bytes": artifact_size_bytes,
            "schema_version": artifact.get("schema_version") if artifact else None,
            "created_utc": artifact.get("created_utc") if artifact else None,
        },
        "head_sha": head_sha,
        "current_head": current_head,
        "case_status": case_status,
        "hosted_governance_status": hosted_governance_status,
        "support_classification": support_classification,
        "current_head_startup_run_ids": current_head_startup_run_ids,
        "copy_lines": packet.get("copy_lines", []) if packet else [],
        "diagnostic_commands": packet.get("diagnostic_commands", []) if packet else [],
        "checks": checks,
    }


def run_release_incident_packet(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify:
        report = verify_release_incident_packet(
            cfg,
            args.verify,
            require_current_head=args.require_current_head,
            expected_sha256=args.expected_sha256,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "HOSTED_INCIDENT_PACKET_VERIFIED" else 2
    if args.require_current_head or args.expected_sha256:
        raise MucaroCLIError("ERROR_HOSTED_INCIDENT_PACKET_VERIFY_OPTION", "--require-current-head and --expected-sha256 require --verify")
    report = build_release_incident_packet(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
        output_path=args.output,
        save=args.save or args.output is not None,
    )
    emit_payload(report, args.format)
    if report["status"] == "HOSTED_INCIDENT_PACKET_FAILED":
        return 2
    return 0


_NEXT_ACTION_PRIORITIES = {
    "local_release_evidence": 5,
    "billing_visibility": 10,
    "actions_repository_policy": 20,
    "workflow_token_policy": 30,
    "branch_protection": 40,
    "ci_run_admission": 50,
    "current_head_push_startup": 55,
    "startup_probe_admission": 60,
    "check_suite_admission": 70,
    "ci_workflow_registration": 80,
    "startup_probe_registration": 90,
    "support_bundle": 100,
}


_NEXT_ACTION_REASONS = {
    "local_release_evidence": "The release dashboard must be open or ready before hosted remediation can be treated as the only remaining lane.",
    "actions_repository_policy": "Repository Actions policy must allow workflows before hosted CI can be trusted.",
    "workflow_token_policy": "Workflow token permissions must be visible and acceptable for release governance.",
    "ci_workflow_registration": "The primary CI workflow must be registered before run admission can be proven.",
    "startup_probe_registration": "The startup probe must be registered to isolate hosted runner startup from project code.",
    "ci_run_admission": "The current-head CI run must create jobs and finish successfully before hosted governance can pass.",
    "current_head_push_startup": "Current-head push runs failed before jobs and must be adjudicated before hosted governance can pass.",
    "startup_probe_admission": "The startup probe must create jobs so startup failures are not mistaken for project test failures.",
    "check_suite_admission": "GitHub check suites must expose check runs for current-head workflow evidence.",
    "billing_visibility": "Billing visibility is needed to adjudicate hosted startup failures before jobs.",
    "branch_protection": "Branch-protection status must be visible before the governed release lane can be certified.",
    "support_bundle": "A current support bundle preserves exact evidence for GitHub or account-policy escalation.",
}


def _release_next_action_from_step(step: dict[str, Any]) -> dict[str, Any]:
    step_id = str(step.get("step_id") or "unknown_step")
    return {
        "action_id": step_id,
        "priority": _NEXT_ACTION_PRIORITIES.get(step_id, 999),
        "status": str(step.get("status") or "UNVERIFIED"),
        "owner": str(step.get("owner") or "operator"),
        "reason": _NEXT_ACTION_REASONS.get(step_id, "This hosted governance step is not yet passing."),
        "evidence": str(step.get("evidence") or ""),
        "command": str(step.get("command") or ""),
    }


def _support_copy_value(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _support_copy_line_value(value: Any) -> str:
    return _support_copy_value(value).replace("\n", "\\n")


def _compact_id_list(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return ",".join(str(value) for value in values if value is not None)


def _build_release_next_actions_support_copy(runbook: dict[str, Any]) -> dict[str, Any]:
    summary_keys = [
        "head_sha",
        "tag_at_head",
        "release_dashboard_status",
        "release_state_status",
        "github_actions_status",
        "hosted_governance_status",
        "support_classification",
    ]
    run_handle_keys = [
        "ci_run_id",
        "ci_run_url",
        "ci_jobs_count",
        "startup_probe_run_id",
        "startup_probe_run_url",
        "startup_probe_jobs_count",
        "current_head_startup_run_ids",
    ]
    summary = {key: runbook.get(key) for key in summary_keys}
    run_handles = {key: runbook.get(key) for key in run_handle_keys}
    artifact_paths = runbook.get("artifact_paths") if isinstance(runbook.get("artifact_paths"), dict) else {}
    verification_commands = [
        str(command)
        for command in runbook.get("verification_commands", [])
        if command
    ]
    open_actions = [
        {
            "priority": action.get("priority"),
            "action_id": action.get("action_id"),
            "status": action.get("status"),
            "owner": action.get("owner"),
            "command": action.get("command"),
            "evidence": action.get("evidence"),
        }
        for action in runbook.get("actions", [])
        if isinstance(action, dict)
    ]

    copy_lines = ["Mucaro AI release governance handoff"]
    copy_lines.extend(f"{key}={_support_copy_value(summary[key])}" for key in summary_keys)
    copy_lines.extend(f"{key}={_support_copy_value(run_handles[key])}" for key in run_handle_keys)
    for artifact_id in ["distribution_manifest", "governance_report", "support_bundle"]:
        copy_lines.append(f"artifact_{artifact_id}={_support_copy_value(artifact_paths.get(artifact_id))}")
    copy_lines.extend(f"verify_command={command}" for command in verification_commands)
    copy_lines.extend(
        "open_action="
        f"priority:{_support_copy_value(action.get('priority'))} "
        f"action:{_support_copy_value(action.get('action_id'))} "
        f"status:{_support_copy_value(action.get('status'))} "
        f"owner:{_support_copy_value(action.get('owner'))} "
        f"command:{_support_copy_value(action.get('command'))} "
        f"evidence:{_support_copy_line_value(action.get('evidence'))}"
        for action in open_actions
    )
    return {
        "title": "Mucaro AI release governance handoff",
        "summary": summary,
        "run_handles": run_handles,
        "artifact_paths": artifact_paths,
        "verification_commands": verification_commands,
        "open_actions": open_actions,
        "copy_lines": copy_lines,
    }


def build_release_next_actions_report(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    hosted = build_release_hosted_governance_report(
        cfg,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    head_sha = str(hosted.get("head_sha") or "")
    head_prefix = head_sha[:12]
    artifact_paths = {
        "distribution_manifest": str(cfg.project_root / DISTRIBUTION_MANIFEST_DIR / f"distribution-manifest-{head_prefix}.json")
        if head_prefix
        else None,
        "governance_report": str(cfg.project_root / GOVERNANCE_REPORT_DIR / f"governance-report-{head_prefix}.json")
        if head_prefix
        else None,
        "support_bundle": hosted.get("support_bundle", {}).get("path")
        if isinstance(hosted.get("support_bundle"), dict)
        else None,
    }
    verification_commands = [
        "mucaro release receipts --current-head --format text",
        "mucaro release dashboard --include-github --format text",
        "mucaro release hosted-governance --format text",
        "mucaro audit completion --include-github --format text",
    ]
    if artifact_paths["distribution_manifest"]:
        verification_commands.append(
            f"mucaro package distribution-manifest --verify {artifact_paths['distribution_manifest']} "
            "--require-current-head --format text"
        )
    if artifact_paths["governance_report"]:
        verification_commands.append(
            f"mucaro release governance-report --verify {artifact_paths['governance_report']} "
            "--require-current-head --format text"
        )
    if artifact_paths["support_bundle"]:
        verification_commands.append(
            f"mucaro release support-bundle --verify {artifact_paths['support_bundle']} "
            "--require-current-head --format text"
        )

    actions = [
        _release_next_action_from_step(step)
        for step in hosted.get("steps", [])
        if isinstance(step, dict) and step.get("status") != "PASS"
    ]
    if hosted.get("release_dashboard_status") == "RELEASE_DASHBOARD_FAIL":
        actions.append(
            {
                "action_id": "local_release_evidence",
                "priority": _NEXT_ACTION_PRIORITIES["local_release_evidence"],
                "status": "OPEN",
                "owner": "operator",
                "reason": _NEXT_ACTION_REASONS["local_release_evidence"],
                "evidence": "release_dashboard_status=RELEASE_DASHBOARD_FAIL",
                "command": "mucaro release dashboard --include-github --format text",
            }
        )
    actions.sort(key=lambda action: (int(action["priority"]), str(action["action_id"])))
    counts = {
        "open": len(actions),
        "total": len(actions),
    }
    status = (
        "RELEASE_NEXT_ACTIONS_READY"
        if hosted.get("status") == "HOSTED_GOVERNANCE_READY" and not actions
        else "RELEASE_NEXT_ACTIONS_OPEN"
    )
    report = {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "head_sha": head_sha,
        "tag_at_head": hosted.get("tag_at_head", []),
        "hosted_governance_status": hosted.get("hosted_governance_status"),
        "release_dashboard_status": hosted.get("release_dashboard_status"),
        "release_state_status": hosted.get("release_state_status"),
        "github_actions_status": hosted.get("github_actions_status"),
        "support_classification": hosted.get("support_classification"),
        "ci_run_id": hosted.get("ci_run_id"),
        "ci_run_url": hosted.get("ci_run_url"),
        "startup_probe_run_id": hosted.get("startup_probe_run_id"),
        "startup_probe_run_url": hosted.get("startup_probe_run_url"),
        "current_head_startup_runs": hosted.get("current_head_startup_runs", []),
        "current_head_startup_run_ids": hosted.get("current_head_startup_run_ids", []),
        "ci_jobs_count": hosted.get("ci_jobs_count"),
        "startup_probe_jobs_count": hosted.get("startup_probe_jobs_count"),
        "artifact_paths": artifact_paths,
        "verification_commands": verification_commands,
        "actions": actions,
        "counts": counts,
    }
    report["support_copy"] = _build_release_next_actions_support_copy(report)
    return report


def write_release_next_actions_report(
    cfg: Settings,
    report: dict[str, Any],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    head_sha = str(report.get("head_sha") or "unknown")
    path = output_path or cfg.project_root / NEXT_ACTIONS_DIR / f"next-actions-{head_sha[:12]}.json"
    if not path.is_absolute():
        path = cfg.project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": NEXT_ACTIONS_SCHEMA_VERSION,
        "created_utc": _utc_now(),
        "runbook": report,
    }
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "schema_version": NEXT_ACTIONS_SCHEMA_VERSION,
    }


def verify_release_next_actions_report(
    cfg: Settings,
    artifact_path: Path,
    *,
    require_current_head: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    path = artifact_path if artifact_path.is_absolute() else cfg.project_root / artifact_path
    checks: list[dict[str, str]] = []
    artifact: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None

    if path.exists() and path.is_file():
        checks.append(_certification_gate("artifact_exists", "PASS", str(path)))
        artifact_sha256 = _sha256_file(path)
        artifact_size_bytes = path.stat().st_size
        checks.append(_certification_gate("artifact_sha256", "PASS", artifact_sha256))
        if expected_sha256:
            expected = expected_sha256.strip().lower()
            checks.append(
                _certification_gate(
                    "expected_sha256",
                    "PASS" if artifact_sha256 == expected else "FAIL",
                    f"expected={expected} actual={artifact_sha256}",
                )
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            loaded = None
            checks.append(_certification_gate("artifact_json", "FAIL", str(exc)))
        if isinstance(loaded, dict):
            artifact = loaded
            checks.append(_certification_gate("artifact_json", "PASS", "valid JSON object"))
        elif loaded is not None:
            checks.append(_certification_gate("artifact_json", "FAIL", "top-level JSON value is not an object"))
    else:
        checks.append(_certification_gate("artifact_exists", "FAIL", str(path)))

    runbook: dict[str, Any] | None = None
    if artifact is not None:
        schema_version = artifact.get("schema_version")
        checks.append(
            _certification_gate(
                "schema_version",
                "PASS" if schema_version == NEXT_ACTIONS_SCHEMA_VERSION else "FAIL",
                str(schema_version),
            )
        )
        created_ok, created_detail = _parse_utc_timestamp(artifact.get("created_utc"))
        checks.append(_certification_gate("created_utc", "PASS" if created_ok else "FAIL", created_detail))
        raw_runbook = artifact.get("runbook")
        if isinstance(raw_runbook, dict):
            runbook = raw_runbook
            checks.append(_certification_gate("runbook_payload", "PASS", "runbook object present"))
        else:
            checks.append(_certification_gate("runbook_payload", "FAIL", "runbook object missing"))

    head_sha: str | None = None
    runbook_status: str | None = None
    if runbook is not None:
        raw_head_sha = runbook.get("head_sha")
        if _is_hex_sha(raw_head_sha):
            head_sha = str(raw_head_sha)
            checks.append(_certification_gate("runbook_head_sha", "PASS", head_sha))
        else:
            checks.append(_certification_gate("runbook_head_sha", "FAIL", str(raw_head_sha)))
        runbook_status = str(runbook.get("status") or "")
        checks.append(
            _certification_gate(
                "runbook_status",
                "PASS" if runbook_status in {"RELEASE_NEXT_ACTIONS_OPEN", "RELEASE_NEXT_ACTIONS_READY"} else "FAIL",
                runbook_status,
            )
        )
        checks.append(
            _certification_gate(
                "actions_payload",
                "PASS" if isinstance(runbook.get("actions"), list) else "FAIL",
                "present" if isinstance(runbook.get("actions"), list) else "missing",
            )
        )
        checks.append(
            _certification_gate(
                "verification_commands",
                "PASS" if isinstance(runbook.get("verification_commands"), list) else "FAIL",
                "present" if isinstance(runbook.get("verification_commands"), list) else "missing",
            )
        )
        checks.append(
            _certification_gate(
                "counts_payload",
                "PASS" if isinstance(runbook.get("counts"), dict) else "FAIL",
                "present" if isinstance(runbook.get("counts"), dict) else "missing",
            )
        )
        support_copy = runbook.get("support_copy")
        checks.append(
            _certification_gate(
                "support_copy_payload",
                "PASS" if isinstance(support_copy, dict) else "FAIL",
                "present" if isinstance(support_copy, dict) else "missing",
            )
        )
        support_copy_lines = support_copy.get("copy_lines") if isinstance(support_copy, dict) else None
        checks.append(
            _certification_gate(
                "support_copy_lines",
                "PASS" if isinstance(support_copy_lines, list) and support_copy_lines else "FAIL",
                "present" if isinstance(support_copy_lines, list) and support_copy_lines else "missing",
            )
        )

    current_head: str | None = None
    if require_current_head:
        head_ok, current_head_output = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = current_head_output if head_ok else None
        checks.append(
            _certification_gate(
                "current_head_binding",
                "PASS" if head_ok and head_sha == current_head_output else "FAIL",
                f"runbook_head={head_sha} current_head={current_head_output if head_ok else current_head_output or 'unavailable'}",
            )
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "RELEASE_NEXT_ACTIONS_VERIFIED" if not failed else "RELEASE_NEXT_ACTIONS_VERIFY_FAILED",
        "project_root": str(cfg.project_root),
        "artifact": {
            "path": str(path),
            "sha256": artifact_sha256,
            "size_bytes": artifact_size_bytes,
            "schema_version": artifact.get("schema_version") if artifact else None,
            "created_utc": artifact.get("created_utc") if artifact else None,
        },
        "head_sha": head_sha,
        "current_head": current_head,
        "runbook_status": runbook_status,
        "checks": checks,
    }


def run_release_next_actions(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify:
        report = verify_release_next_actions_report(
            cfg,
            args.verify,
            require_current_head=args.require_current_head,
            expected_sha256=args.expected_sha256,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "RELEASE_NEXT_ACTIONS_VERIFIED" else 2
    if args.require_current_head or args.expected_sha256:
        raise MucaroCLIError("ERROR_RELEASE_NEXT_ACTIONS_VERIFY_OPTION", "--require-current-head and --expected-sha256 require --verify")
    report = build_release_next_actions_report(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    if args.save or args.output:
        report["export"] = write_release_next_actions_report(cfg, report, output_path=args.output)
    emit_payload(report, args.format)
    if report["status"] == "RELEASE_NEXT_ACTIONS_READY":
        return 0
    return 2 if args.strict else 0


def _artifact_export(payload: dict[str, Any]) -> dict[str, Any]:
    export = payload.get("export")
    if isinstance(export, dict):
        return {
            "path": export.get("path"),
            "sha256": export.get("sha256"),
            "size_bytes": export.get("size_bytes"),
            "schema_version": export.get("schema_version"),
        }
    artifact = payload.get("artifact")
    if isinstance(artifact, dict):
        return {
            "path": artifact.get("path"),
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "schema_version": artifact.get("schema_version"),
        }
    return {"path": None, "sha256": None, "size_bytes": None, "schema_version": None}


def _release_handoff_certification_command(
    step_id: str,
    command: list[str],
    cfg: Settings,
    *,
    timeout_seconds: int = 0,
) -> dict[str, Any]:
    timeout = timeout_seconds if timeout_seconds > 0 else None
    started_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=cfg.project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        ended_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        elapsed_seconds = round(max(0.0, time.monotonic() - started), 3)
        output = (exc.output or "").strip() if isinstance(exc.output, str) else ""
        output_lines = output.splitlines()
        return {
            "step_id": step_id,
            "status": "FAIL",
            "returncode": None,
            "command": " ".join(command),
            "detail": f"TIMEOUT_AFTER_SECONDS:{timeout_seconds}",
            "output_tail": output_lines[-20:],
            "timed_out": True,
            "started_utc": started_utc,
            "ended_utc": ended_utc,
            "elapsed_seconds": elapsed_seconds,
        }
    ended_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    elapsed_seconds = round(max(0.0, time.monotonic() - started), 3)
    output = proc.stdout.strip()
    output_lines = output.splitlines()
    return {
        "step_id": step_id,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "returncode": proc.returncode,
        "command": " ".join(command),
        "detail": output_lines[-1] if output_lines else f"returncode={proc.returncode}",
        "output_tail": output_lines[-20:],
        "timed_out": False,
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "elapsed_seconds": elapsed_seconds,
    }


def _selected_release_handoff_refresh_steps(refresh_steps: Sequence[str] | None = None) -> list[str]:
    selected = set(refresh_steps or RELEASE_HANDOFF_REFRESH_STEPS)
    return [step for step in RELEASE_HANDOFF_REFRESH_STEPS if step in selected]


def _release_handoff_slowest_steps(steps: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    timed_steps = [
        step
        for step in steps
        if isinstance(step.get("elapsed_seconds"), int | float)
    ]
    ordered = sorted(timed_steps, key=lambda step: float(step["elapsed_seconds"]), reverse=True)
    return [
        {
            "step_id": step.get("step_id"),
            "status": step.get("status"),
            "elapsed_seconds": step.get("elapsed_seconds"),
            "detail": step.get("detail"),
        }
        for step in ordered[:limit]
    ]


def write_release_handoff_refresh_log(
    cfg: Settings,
    refresh: dict[str, Any],
    head_sha: str,
) -> dict[str, Any]:
    created = datetime.now(UTC)
    created_utc = created.isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": RELEASE_REFRESH_LOG_SCHEMA_VERSION,
        "created_utc": created_utc,
        "head_sha": head_sha,
        "refresh": refresh,
    }
    stamp = created.strftime("%Y%m%dT%H%M%S%fZ")
    path = cfg.project_root / RELEASE_REFRESH_LOG_DIR / f"refresh-{head_sha[:12]}-{stamp}.json"
    counter = 1
    while path.exists():
        path = cfg.project_root / RELEASE_REFRESH_LOG_DIR / f"refresh-{head_sha[:12]}-{stamp}-{counter}.json"
        counter += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(encoded)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "schema_version": RELEASE_REFRESH_LOG_SCHEMA_VERSION,
    }


def run_release_handoff_certification_refresh(
    cfg: Settings,
    head_sha: str,
    *,
    refresh_steps: Sequence[str] | None = None,
    timeout_seconds: int = 0,
) -> dict[str, Any]:
    refresh_started_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    refresh_started = time.monotonic()
    head_prefix = head_sha[:12]
    source_manifest = (
        cfg.project_root
        / "data"
        / "exports"
        / "packages"
        / f"mucaro-ai-{package_version()}-{head_prefix}-artifact-manifest.json"
    )
    commands = {
        "local_release": [sys.executable, "scripts/certify_release.py"],
        "python_dist": [sys.executable, "scripts/certify_python_distribution.py"],
        "source_artifact": [sys.executable, "scripts/certify_source_artifact.py", str(source_manifest)],
        "fresh_clone": [sys.executable, "scripts/certify_fresh_clone.py", "--ref", head_sha],
    }
    requested_steps = _selected_release_handoff_refresh_steps(refresh_steps)
    steps = []
    for step_id in requested_steps:
        step = _release_handoff_certification_command(
            step_id,
            commands[step_id],
            cfg,
            timeout_seconds=timeout_seconds,
        )
        steps.append(step)
        if step.get("status") != "PASS":
            break
    failed = [step for step in steps if step.get("status") != "PASS"]
    refresh_ended_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    total_elapsed_seconds = round(max(0.0, time.monotonic() - refresh_started), 3)
    refresh = {
        "requested": True,
        "status": "CERTIFICATION_REFRESH_FAILED" if failed else "CERTIFICATION_REFRESH_OK",
        "requested_steps": requested_steps,
        "timeout_seconds": timeout_seconds,
        "started_utc": refresh_started_utc,
        "ended_utc": refresh_ended_utc,
        "total_elapsed_seconds": total_elapsed_seconds,
        "slowest_steps": _release_handoff_slowest_steps(steps),
        "steps": steps,
    }
    refresh["log"] = write_release_handoff_refresh_log(cfg, refresh, head_sha)
    return refresh


def build_release_handoff_report(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
    refresh_certification: bool = False,
    refresh_steps: Sequence[str] | None = None,
    refresh_timeout_seconds: int = 0,
) -> dict[str, Any]:
    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    head_sha = head_sha if head_ok and _is_hex_sha(head_sha) else ""
    dirty_ok, dirty_output = _git_output(cfg, ["status", "--short"])
    worktree_clean = dirty_ok and not dirty_output.strip()
    receipts = list_release_receipts(cfg.project_root)
    current_head_receipts = [row for row in receipts if row.get("head_sha") == head_sha]
    receipt_summary = summarize_release_receipts(current_head_receipts)
    preflight_checks = [
        _certification_gate("current_head", "PASS" if head_sha else "FAIL", head_sha or "unavailable"),
        _certification_gate(
            "worktree_clean",
            "PASS" if worktree_clean else "FAIL",
            "clean" if worktree_clean else dirty_output or "unavailable",
        ),
    ]
    if any(check.get("status") == "FAIL" for check in preflight_checks):
        return {
            "status": "RELEASE_HANDOFF_FAILED",
            "version": package_version(),
            "project_root": str(cfg.project_root),
            "head_sha": head_sha or None,
            "tag_at_head": [],
            "hosted_governance_status": "UNVERIFIED",
            "release_dashboard_status": "UNVERIFIED",
            "release_state_status": "UNVERIFIED",
            "github_actions_status": "UNVERIFIED",
            "current_head_receipts_summary": receipt_summary,
            "certification_refresh": {
                "requested": refresh_certification,
                "status": "SKIPPED",
                "requested_steps": _selected_release_handoff_refresh_steps(refresh_steps) if refresh_certification else [],
                "timeout_seconds": refresh_timeout_seconds,
                "steps": [],
            },
            "artifacts": {},
            "artifact_verification": {},
            "checks": preflight_checks,
            "counts": {
                "pass": sum(1 for check in preflight_checks if check.get("status") == "PASS"),
                "open": 0,
                "fail": sum(1 for check in preflight_checks if check.get("status") == "FAIL"),
                "total": len(preflight_checks),
            },
            "verification_commands": [
                "git status --short",
                "mucaro release receipts --current-head --format text",
            ],
        }

    certification_refresh = (
        run_release_handoff_certification_refresh(
            cfg,
            head_sha,
            refresh_steps=refresh_steps,
            timeout_seconds=refresh_timeout_seconds,
        )
        if refresh_certification
        else {
            "requested": False,
            "status": "SKIPPED",
            "requested_steps": [],
            "timeout_seconds": refresh_timeout_seconds,
            "steps": [],
        }
    )
    refresh_checks = [
        _certification_gate(
            f"certification_refresh:{step.get('step_id')}",
            str(step.get("status")),
            str(step.get("detail") or ""),
        )
        for step in certification_refresh.get("steps", [])
        if isinstance(step, dict)
    ]
    if any(check.get("status") == "FAIL" for check in refresh_checks):
        checks = [*preflight_checks, *refresh_checks]
        return {
            "status": "RELEASE_HANDOFF_FAILED",
            "version": package_version(),
            "project_root": str(cfg.project_root),
            "head_sha": head_sha or None,
            "tag_at_head": [],
            "hosted_governance_status": "UNVERIFIED",
            "release_dashboard_status": "UNVERIFIED",
            "release_state_status": "UNVERIFIED",
            "github_actions_status": "UNVERIFIED",
            "current_head_receipts_summary": receipt_summary,
            "certification_refresh": certification_refresh,
            "artifacts": {},
            "artifact_verification": {},
            "checks": checks,
            "counts": {
                "pass": sum(1 for check in checks if check.get("status") == "PASS"),
                "open": 0,
                "fail": sum(1 for check in checks if check.get("status") == "FAIL"),
                "total": len(checks),
            },
            "verification_commands": [
                "mucaro release handoff --refresh-certification --format text",
                "mucaro release receipts --current-head --format text",
            ],
        }

    if refresh_certification:
        receipts = list_release_receipts(cfg.project_root)
        current_head_receipts = [row for row in receipts if row.get("head_sha") == head_sha]
        receipt_summary = summarize_release_receipts(current_head_receipts)

    distribution = build_distribution_manifest(cfg.project_root, save=True)
    distribution_export = _artifact_export(distribution)
    distribution_verify = verify_distribution_manifest(
        Path(str(distribution_export["path"])),
        root=cfg.project_root,
        expected_sha256=str(distribution_export["sha256"]),
        require_current_head=True,
    )

    support_bundle = build_release_support_bundle(
        cfg,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
        save=True,
    )
    support_export = _artifact_export(support_bundle)
    support_verify = verify_release_support_bundle(
        cfg,
        Path(str(support_export["path"])),
        expected_sha256=str(support_export["sha256"]),
        require_current_head=True,
    )

    governance_report = support_bundle.get("governance_report")
    governance_path = (
        Path(str(governance_report.get("path")))
        if isinstance(governance_report, dict) and governance_report.get("path")
        else cfg.project_root / GOVERNANCE_REPORT_DIR / f"governance-report-{head_sha[:12]}.json"
    )
    governance_verify = verify_release_governance_report(cfg, governance_path, require_current_head=True)
    governance_export = _artifact_export(governance_verify)

    next_actions = build_release_next_actions_report(
        cfg,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    next_actions_export = write_release_next_actions_report(cfg, next_actions)
    next_actions_verify = verify_release_next_actions_report(
        cfg,
        Path(str(next_actions_export["path"])),
        expected_sha256=str(next_actions_export["sha256"]),
        require_current_head=True,
    )

    expected_receipt_count = len(REQUIRED_DISTRIBUTION_RECEIPTS)
    checks = [
        *preflight_checks,
        *refresh_checks,
        _certification_gate(
            "current_head_receipts",
            (
                "PASS"
                if receipt_summary.get("total") == expected_receipt_count
                and receipt_summary.get("valid") == expected_receipt_count
                else "FAIL"
            ),
            f"total={receipt_summary.get('total')} valid={receipt_summary.get('valid')} invalid={receipt_summary.get('invalid')}",
        ),
        _certification_gate(
            "distribution_manifest",
            "PASS" if distribution_verify.get("status") == "DISTRIBUTION_MANIFEST_VERIFIED" else "FAIL",
            str(distribution_verify.get("status")),
        ),
        _certification_gate(
            "governance_report",
            "PASS" if governance_verify.get("status") == "GOVERNANCE_REPORT_VERIFIED" else "FAIL",
            str(governance_verify.get("status")),
        ),
        _certification_gate(
            "support_bundle",
            "PASS" if support_verify.get("status") == "SUPPORT_BUNDLE_VERIFIED" else "FAIL",
            str(support_verify.get("status")),
        ),
        _certification_gate(
            "next_actions",
            "PASS" if next_actions_verify.get("status") == "RELEASE_NEXT_ACTIONS_VERIFIED" else "FAIL",
            str(next_actions_verify.get("status")),
        ),
    ]
    hosted_status = str(next_actions.get("hosted_governance_status") or support_bundle.get("case_status") or "UNVERIFIED")
    if hosted_status != "PASS":
        checks.append(_certification_gate("hosted_governance", "OPEN", hosted_status))

    failed = [check for check in checks if check.get("status") == "FAIL"]
    open_checks = [check for check in checks if check.get("status") in {"OPEN", "WARN", "UNVERIFIED"}]
    status = "RELEASE_HANDOFF_FAILED" if failed else "RELEASE_HANDOFF_OPEN" if open_checks else "RELEASE_HANDOFF_READY"
    artifacts = {
        "distribution_manifest": distribution_export,
        "governance_report": governance_export,
        "support_bundle": support_export,
        "next_actions": next_actions_export,
    }
    verification_commands = [
        "mucaro release receipts --current-head --format text",
        "mucaro release dashboard --include-github --format text",
        "mucaro release hosted-governance --format text",
        "mucaro audit completion --include-github --format text",
        f"mucaro package distribution-manifest --verify {artifacts['distribution_manifest']['path']} "
        f"--expected-sha256 {artifacts['distribution_manifest']['sha256']} --require-current-head --format text",
        f"mucaro release governance-report --verify {artifacts['governance_report']['path']} "
        "--require-current-head --format text",
        f"mucaro release support-bundle --verify {artifacts['support_bundle']['path']} "
        f"--expected-sha256 {artifacts['support_bundle']['sha256']} --require-current-head --format text",
        f"mucaro release next-actions --verify {artifacts['next_actions']['path']} "
        f"--expected-sha256 {artifacts['next_actions']['sha256']} --require-current-head --format text",
    ]
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "head_sha": head_sha or None,
        "tag_at_head": next_actions.get("tag_at_head", []),
        "hosted_governance_status": next_actions.get("hosted_governance_status"),
        "release_dashboard_status": next_actions.get("release_dashboard_status"),
        "release_state_status": next_actions.get("release_state_status"),
        "github_actions_status": next_actions.get("github_actions_status"),
        "current_head_receipts_summary": receipt_summary,
        "certification_refresh": certification_refresh,
        "artifacts": artifacts,
        "artifact_verification": {
            "distribution_manifest": distribution_verify,
            "governance_report": governance_verify,
            "support_bundle": support_verify,
            "next_actions": next_actions_verify,
        },
        "checks": checks,
        "counts": {
            "pass": sum(1 for check in checks if check.get("status") == "PASS"),
            "open": sum(1 for check in checks if check.get("status") in {"OPEN", "WARN", "UNVERIFIED"}),
            "fail": len(failed),
            "total": len(checks),
        },
        "verification_commands": verification_commands,
    }


def run_release_handoff(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_release_handoff_report(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
        refresh_certification=args.refresh_certification or bool(args.refresh_step),
        refresh_steps=args.refresh_step,
        refresh_timeout_seconds=args.refresh_timeout_seconds,
    )
    emit_payload(report, args.format)
    if report["status"] == "RELEASE_HANDOFF_FAILED":
        return 2
    if args.strict and report["status"] != "RELEASE_HANDOFF_READY":
        return 2
    return 0


def _audit_status(*statuses: str | None) -> str:
    normalized = {str(status or "UNVERIFIED") for status in statuses}
    if "FAIL" in normalized:
        return "FAIL"
    if "BLOCKED" in normalized:
        return "BLOCKED"
    if "OPEN" in normalized:
        return "OPEN"
    if "WARN" in normalized:
        return "OPEN"
    if "UNVERIFIED" in normalized:
        return "UNVERIFIED"
    return "PASS"


def _audit_status_from_value(status: str | None, *, pass_values: set[str]) -> str:
    normalized = str(status or "UNVERIFIED")
    if normalized in pass_values:
        return "PASS"
    if normalized in {"PASS", "OPEN", "BLOCKED", "FAIL", "UNVERIFIED"}:
        return normalized
    if normalized == "WARN":
        return "OPEN"
    if "BLOCKED" in normalized:
        return "BLOCKED"
    if "FAIL" in normalized or "FAILED" in normalized or "ERROR" in normalized:
        return "FAIL"
    if "OPEN" in normalized or "MISSING" in normalized or "STALE" in normalized:
        return "OPEN"
    return "UNVERIFIED"


def _release_state_status(release_state: dict[str, Any], state_name: str) -> str:
    for state in release_state.get("states", []):
        if isinstance(state, dict) and state.get("state") == state_name:
            status = state.get("status")
            return str(status) if status else "UNVERIFIED"
    return "UNVERIFIED"


def _release_state_evidence(release_state: dict[str, Any], state_name: str) -> str:
    for state in release_state.get("states", []):
        if isinstance(state, dict) and state.get("state") == state_name:
            evidence = state.get("evidence")
            return str(evidence) if evidence else "No evidence recorded."
    return "Release state was not present in the release-state report."


def _product_requirement_row(product_audit: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    for row in product_audit.get("requirements", []):
        if isinstance(row, dict) and row.get("requirement_id") == requirement_id:
            return row
    return {"requirement_id": requirement_id, "status": "UNVERIFIED", "evidence": "Requirement was not present."}


def _completion_requirement(
    requirement_id: str,
    requirement: str,
    status: str,
    evidence: str,
    gap: str,
    next_step: str,
) -> dict[str, str]:
    return {
        "requirement_id": requirement_id,
        "requirement": requirement,
        "status": status,
        "evidence": evidence,
        "gap": gap,
        "next_step": next_step,
    }


def _completion_requirement_from_product(row: dict[str, Any]) -> dict[str, str]:
    requirement_id = str(row.get("requirement_id") or "unverified_product_requirement")
    return _completion_requirement(
        requirement_id,
        str(row.get("requirement") or requirement_id),
        str(row.get("status") or "UNVERIFIED"),
        str(row.get("evidence") or ""),
        str(row.get("gap") or ""),
        str(row.get("next_step") or "Complete and verify this product-audit requirement."),
    )


def _completion_hosted_governance_evidence(
    *,
    include_github: bool,
    hosted_governance_status: str,
    dashboard: dict[str, Any],
    release_state: dict[str, Any],
) -> str:
    github_actions = dashboard.get("github_actions")
    if not isinstance(github_actions, dict):
        github_actions = {}
    support_summary = github_actions.get("support_summary")
    if not isinstance(support_summary, dict):
        support_summary = {}
    startup_blocker = github_actions.get("startup_blocker")
    if not isinstance(startup_blocker, dict):
        startup_blocker = {}
    check_suite_counts = support_summary.get("check_suite_counts")
    if not isinstance(check_suite_counts, dict):
        check_suite_counts = {}

    current_head_run_ids = _compact_id_list(support_summary.get("current_head_startup_run_ids"))
    suspected_causes = _compact_id_list(startup_blocker.get("suspected_causes"))
    parts = [
        f"include_github={include_github}",
        f"hosted_governance_status={hosted_governance_status}",
    ]
    if support_summary.get("classification"):
        parts.append(f"support_classification={support_summary.get('classification')}")
    if current_head_run_ids:
        parts.append(f"current_head_startup_run_ids={current_head_run_ids}")
    if check_suite_counts:
        parts.append(
            "check_suite_counts="
            f"github_actions:{check_suite_counts.get('github_actions')} "
            f"zero_check_run_startup_failures:{check_suite_counts.get('github_actions_zero_check_run_startup_failures')} "
            f"queued_external:{check_suite_counts.get('queued_external')}"
        )
    if suspected_causes:
        parts.append(f"suspected_causes={suspected_causes}")
    parts.extend(
        [
            f"github_governance={_release_state_evidence(release_state, 'GITHUB_GOVERNANCE_OPEN')}",
            f"governed_release={_release_state_evidence(release_state, 'GOVERNED_RELEASE_READY')}",
        ]
    )
    return " ".join(parts)


def build_completion_audit_report(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
) -> dict[str, Any]:
    product_audit = build_product_audit(cfg)
    release_state = build_release_state_report(
        cfg,
        include_github=include_github,
        github_workflow=github_workflow,
    )
    dashboard = build_release_dashboard_report(
        cfg,
        include_github=include_github,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    git_payload = release_state.get("git", {}) if isinstance(release_state.get("git"), dict) else {}
    receipt_summary = dashboard.get("current_head_receipts_summary", {})
    if not isinstance(receipt_summary, dict):
        receipt_summary = {}

    installable_cli = _product_requirement_row(product_audit, "installable_cli")
    readiness_diagnostics = _product_requirement_row(product_audit, "readiness_diagnostics")
    permissioned_sources = _product_requirement_row(product_audit, "permission_gated_user_directory")
    document_ingestion = _product_requirement_row(product_audit, "document_ingestion")
    citations = _product_requirement_row(product_audit, "source_lineage_citations")
    retrieval = _product_requirement_row(product_audit, "local_retrieval_strategies")
    semantic_store = _product_requirement_row(product_audit, "semantic_vector_store")
    memory = _product_requirement_row(product_audit, "personalization_memory")
    tools = _product_requirement_row(product_audit, "tool_use_and_actions")
    package = _product_requirement_row(product_audit, "source_artifact_packaging")
    advanced_capability_requirements = [
        _product_requirement_row(product_audit, "advanced_gis_processing"),
        _product_requirement_row(product_audit, "state_of_art_ocr_analysis"),
        _product_requirement_row(product_audit, "governed_web_browsing"),
        _product_requirement_row(product_audit, "coding_programming_expertise"),
        _product_requirement_row(product_audit, "transparency_objectivity_governance"),
    ]

    local_release_status = _release_state_status(release_state, "LOCAL_RELEASE_CERTIFIED")
    fresh_clone_status = _release_state_status(release_state, "FRESH_CLONE_CERTIFIED")
    source_artifact_packaging_status = _release_state_status(release_state, "SOURCE_ARTIFACT_PACKAGING")
    source_artifact_status = _release_state_status(release_state, "SOURCE_ARTIFACT_CERTIFIED")
    python_dist_status = _release_state_status(release_state, "PYTHON_DIST_CERTIFIED")
    answer_trace_status = _release_state_status(release_state, "ANSWER_TRACE_QUALITY_CERTIFIED")
    retrieval_quality_status = _release_state_status(release_state, "RETRIEVAL_QUALITY_CERTIFIED")
    operator_workflow_status = _release_state_status(release_state, "OPERATOR_WORKFLOW_CERTIFIED")
    github_governance_status = _release_state_status(release_state, "GITHUB_GOVERNANCE_OPEN")
    governed_release_status = _release_state_status(release_state, "GOVERNED_RELEASE_READY")

    remote_branch_status = str(dashboard.get("remote_branch_status") or "UNVERIFIED")
    distribution_manifest_status = str(dashboard.get("distribution_manifest_status") or "UNVERIFIED")
    governance_report_status = str(dashboard.get("governance_report_status") or "UNVERIFIED")
    hosted_governance_status = str(dashboard.get("hosted_governance_status") or "UNVERIFIED")
    remote_branch_audit_status = (
        "PASS"
        if remote_branch_status == "PASS"
        else "UNVERIFIED"
        if remote_branch_status == "UNVERIFIED"
        else "OPEN"
    )
    distribution_manifest_audit_status = _audit_status_from_value(
        distribution_manifest_status,
        pass_values={"DISTRIBUTION_MANIFEST_VERIFIED"},
    )
    governance_report_audit_status = _audit_status_from_value(
        governance_report_status,
        pass_values={"GOVERNANCE_REPORT_VERIFIED"},
    )
    hosted_governance_audit_status = _audit_status_from_value(hosted_governance_status, pass_values={"PASS"})

    current_head_receipts_status = (
        "PASS"
        if receipt_summary.get("total", 0) >= len(REQUIRED_DISTRIBUTION_RECEIPTS)
        and receipt_summary.get("valid") == receipt_summary.get("total")
        and receipt_summary.get("invalid") == 0
        else "OPEN"
    )

    requirements = [
        _completion_requirement(
            "canonical_repo_state",
            "Development starts from the canonical mucaro-ai repository with clean Git state, upstream binding, and current-head release receipts.",
            _audit_status(
                _release_state_status(release_state, "CANONICAL_LOCAL"),
                remote_branch_audit_status,
                current_head_receipts_status,
            ),
            (
                f"head_sha={git_payload.get('head_sha')} "
                f"upstream={git_payload.get('upstream')} "
                f"worktree_clean={git_payload.get('worktree_clean')} "
                f"remote_branch_status={remote_branch_status} "
                f"receipt_count={receipt_summary.get('total')}"
            ),
            "" if current_head_receipts_status == "PASS" else "Current HEAD does not yet have the full expected exact-head receipt set.",
            "Rerun exact-head certification receipts after every commit and push the canonical branch.",
        ),
        _completion_requirement(
            "hardened_cli_workflows",
            "CLI workflows are installable, documented through supported commands, locally certified, and safe for automation.",
            _audit_status(
                str(installable_cli.get("status")),
                str(readiness_diagnostics.get("status")),
                local_release_status,
            ),
            (
                f"installable_cli={installable_cli.get('status')} "
                f"readiness_diagnostics={readiness_diagnostics.get('status')} "
                f"local_release={_release_state_evidence(release_state, 'LOCAL_RELEASE_CERTIFIED')}"
            ),
            "" if local_release_status == "PASS" else "The current HEAD has not passed local release certification.",
            "Run python3 scripts/certify_release.py on the exact current HEAD.",
        ),
        _completion_requirement(
            "measurable_retrieval_quality",
            "Retrieval quality is measured by exact-head evaluation receipts, not inferred from implementation presence.",
            _audit_status(str(retrieval.get("status")), str(semantic_store.get("status")), retrieval_quality_status),
            (
                f"local_retrieval={retrieval.get('status')} "
                f"semantic_store={semantic_store.get('status')} "
                f"quality={_release_state_evidence(release_state, 'RETRIEVAL_QUALITY_CERTIFIED')}"
            ),
            "" if retrieval_quality_status == "PASS" else "Retrieval quality receipt is missing or stale for current HEAD.",
            "Run python3 scripts/certify_retrieval_quality.py and expand fixtures before claiming larger-corpus quality.",
        ),
        _completion_requirement(
            "permissioned_source_ingestion",
            "Source ingestion is permission-gated and certified through the operator workflow path.",
            _audit_status(
                str(permissioned_sources.get("status")),
                str(document_ingestion.get("status")),
                operator_workflow_status,
            ),
            (
                f"directory_permission={permissioned_sources.get('status')} "
                f"document_ingestion={document_ingestion.get('status')} "
                f"operator_workflow={_release_state_evidence(release_state, 'OPERATOR_WORKFLOW_CERTIFIED')}"
            ),
            str(document_ingestion.get("gap") or "") if operator_workflow_status == "PASS" else "Operator workflow certification is missing or stale for current HEAD.",
            "Keep new parsers behind explicit grants and source-lineage manifests.",
        ),
        _completion_requirement(
            "cited_answer_traceability",
            "Generated answers preserve citations, chunk bindings, and answer traces with exact-head quality certification.",
            _audit_status(str(citations.get("status")), answer_trace_status),
            (
                f"source_lineage_citations={citations.get('status')} "
                f"quality={_release_state_evidence(release_state, 'ANSWER_TRACE_QUALITY_CERTIFIED')}"
            ),
            "" if answer_trace_status == "PASS" else "Answer-trace quality receipt is missing or stale for current HEAD.",
            "Run python3 scripts/certify_answer_trace_quality.py after answer or citation changes.",
        ),
        _completion_requirement(
            "explicit_memory_controls",
            "Memory is explicit, user-controlled, locally auditable, and covered by operator workflow certification.",
            _audit_status(str(memory.get("status")), operator_workflow_status),
            (
                f"personalization_memory={memory.get('status')} "
                f"operator_workflow={_release_state_evidence(release_state, 'OPERATOR_WORKFLOW_CERTIFIED')}"
            ),
            "" if operator_workflow_status == "PASS" else "Operator workflow receipt is missing or stale for current HEAD.",
            "Keep memory controls separate from cited source evidence and rerun operator certification after memory changes.",
        ),
        _completion_requirement(
            "approved_tool_execution",
            "Tool execution is allowlisted, approval-gated, audited, and covered by operator workflow certification.",
            _audit_status(str(tools.get("status")), operator_workflow_status),
            (
                f"tool_use_and_actions={tools.get('status')} "
                f"operator_workflow={_release_state_evidence(release_state, 'OPERATOR_WORKFLOW_CERTIFIED')}"
            ),
            "" if operator_workflow_status == "PASS" else "Tool approval/audit workflow receipt is missing or stale for current HEAD.",
            "Add write/action tools only with explicit policy, approval, denial, and audit tests.",
        ),
        *[
            _completion_requirement_from_product(row)
            for row in advanced_capability_requirements
        ],
        _completion_requirement(
            "repeatable_fresh_clone_certification",
            "A clean clone can reproduce the launcher and release gates from the current HEAD.",
            fresh_clone_status,
            _release_state_evidence(release_state, "FRESH_CLONE_CERTIFIED"),
            "" if fresh_clone_status == "PASS" else "Fresh-clone certification is missing or stale for current HEAD.",
            f"Run python3 scripts/certify_fresh_clone.py --ref {git_payload.get('head_sha') or 'main'}.",
        ),
        _completion_requirement(
            "production_quality_packaging_path",
            "Source artifact, Python distribution, distribution manifest, and governance handoff artifacts are reproducible and current-head verified.",
            _audit_status(
                str(package.get("status")),
                source_artifact_packaging_status,
                source_artifact_status,
                python_dist_status,
                distribution_manifest_audit_status,
                governance_report_audit_status,
            ),
            (
                f"package_requirement={package.get('status')} "
                f"source_packaging={_release_state_evidence(release_state, 'SOURCE_ARTIFACT_PACKAGING')} "
                f"source_artifact={_release_state_evidence(release_state, 'SOURCE_ARTIFACT_CERTIFIED')} "
                f"python_dist={_release_state_evidence(release_state, 'PYTHON_DIST_CERTIFIED')} "
                f"distribution_manifest={distribution_manifest_status} "
                f"governance_report={governance_report_status}"
            ),
            "" if distribution_manifest_status == "DISTRIBUTION_MANIFEST_VERIFIED" else "Distribution manifest or governance artifact is not verified for current HEAD.",
            "Rebuild and verify package artifacts, distribution manifest, and governance report after every release-support change.",
        ),
        _completion_requirement(
            "hosted_governance",
            "Hosted CI, tag binding, branch governance, and GitHub diagnostics prove the governed release boundary.",
            _audit_status(
                hosted_governance_audit_status if include_github else "UNVERIFIED",
                github_governance_status,
                governed_release_status,
            ),
            _completion_hosted_governance_evidence(
                include_github=include_github,
                hosted_governance_status=hosted_governance_status,
                dashboard=dashboard,
                release_state=release_state,
            ),
            "Hosted governance is not proven at current HEAD." if hosted_governance_status != "PASS" else "",
            "Repair hosted Actions startup, enable branch protection when available, and rerun with --include-github.",
        ),
    ]

    counts = {
        "pass": sum(1 for row in requirements if row["status"] == "PASS"),
        "open": sum(1 for row in requirements if row["status"] == "OPEN"),
        "blocked": sum(1 for row in requirements if row["status"] == "BLOCKED"),
        "fail": sum(1 for row in requirements if row["status"] == "FAIL"),
        "unverified": sum(1 for row in requirements if row["status"] == "UNVERIFIED"),
        "total": len(requirements),
    }
    status = (
        "COMPLETION_AUDIT_FAIL"
        if counts["fail"]
        else "COMPLETION_AUDIT_BLOCKED"
        if counts["blocked"]
        else "COMPLETION_AUDIT_READY"
        if counts["pass"] == counts["total"]
        else "COMPLETION_AUDIT_OPEN"
    )
    blockers = [
        f"{row['requirement_id']}={row['status']}:{row['gap'] or row['next_step']}"
        for row in requirements
        if row["status"] != "PASS"
    ]
    next_commands = sorted(
        set(
            str(command)
            for command in [
                *release_state.get("next_commands", []),
                *dashboard.get("next_commands", []),
                "mucaro audit completion --include-github --format text",
            ]
        )
    )
    return {
        "status": status,
        "objective": product_audit.get("objective"),
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "include_github": include_github,
        "readiness_percent": round((counts["pass"] / counts["total"]) * 100, 1) if counts["total"] else 0.0,
        "counts": counts,
        "current_state": release_state.get("current_state"),
        "highest_local_state": release_state.get("highest_local_state"),
        "head_sha": git_payload.get("head_sha"),
        "release_state_status": release_state.get("status"),
        "release_dashboard_status": dashboard.get("status"),
        "hosted_governance_status": hosted_governance_status,
        "current_head_receipts_summary": receipt_summary,
        "requirements": requirements,
        "blockers": sorted(set(blockers)),
        "next_commands": next_commands,
    }


def run_audit_completion(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_completion_audit_report(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] in {"COMPLETION_AUDIT_FAIL", "COMPLETION_AUDIT_BLOCKED"}:
        return 2
    if args.strict and report["status"] != "COMPLETION_AUDIT_READY":
        return 2
    return 0


def build_release_support_bundle(
    cfg: Settings,
    *,
    github_workflow: str = "CI",
    probe_workflow: str = "CI Startup Probe",
    output_path: Path | None = None,
    save: bool = False,
) -> dict[str, Any]:
    governance_report = None
    if save or output_path is not None:
        governance_report = ensure_release_governance_report_for_support_bundle(
            cfg,
            github_workflow=github_workflow,
            probe_workflow=probe_workflow,
        )

    dashboard = build_release_dashboard_report(
        cfg,
        include_github=True,
        github_workflow=github_workflow,
        probe_workflow=probe_workflow,
    )
    github_actions = dashboard.get("github_actions", {})
    if not isinstance(github_actions, dict):
        github_actions = {}
    support_summary = github_actions.get("support_summary", {})
    if not isinstance(support_summary, dict):
        support_summary = {}
    startup_blocker = github_actions.get("startup_blocker", {})
    if not isinstance(startup_blocker, dict):
        startup_blocker = {}

    checks = [
        _certification_gate(
            "dashboard_not_failed",
            "PASS" if dashboard.get("status") != "RELEASE_DASHBOARD_FAIL" else "FAIL",
            str(dashboard.get("status")),
        ),
        _certification_gate(
            "github_diagnostic_present",
            "PASS" if github_actions.get("status") else "FAIL",
            str(github_actions.get("status")),
        ),
        _certification_gate(
            "support_classification",
            "PASS" if support_summary.get("classification") else "WARN",
            str(support_summary.get("classification") or "missing"),
        ),
    ]
    if governance_report is not None:
        checks.append(
            _certification_gate(
                "governance_report_ready",
                "PASS"
                if governance_report.get("status") == "GOVERNANCE_REPORT_READY_FOR_SUPPORT"
                else "FAIL",
                str(governance_report.get("status")),
            )
        )
    failed = [check for check in checks if check.get("status") == "FAIL"]
    case_status = (
        "HOSTED_GOVERNANCE_OPEN"
        if dashboard.get("hosted_governance_status") == "OPEN"
        else "GOVERNED_RELEASE_READY"
        if dashboard.get("status") == "RELEASE_DASHBOARD_READY"
        else "LOCAL_EVIDENCE_INCOMPLETE"
    )
    next_actions = sorted(
        set(
            str(action)
            for action in [
                *github_actions.get("next_actions", []),
                *dashboard.get("blockers", []),
            ]
        )
    )
    report = {
        "status": "SUPPORT_BUNDLE_FAILED" if failed else "SUPPORT_BUNDLE_READY",
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "head_sha": dashboard.get("head_sha"),
        "case_status": case_status,
        "dashboard_status": dashboard.get("status"),
        "support_classification": support_summary.get("classification"),
        "github_actions_status": github_actions.get("status"),
        "startup_blocker": startup_blocker,
        "support_summary": support_summary,
        "governance_report": governance_report,
        "dashboard": dashboard,
        "checks": checks,
        "blockers": dashboard.get("blockers", []),
        "next_actions": next_actions,
        "reproduction_commands": [
            "mucaro release dashboard --include-github --format text",
            "mucaro certify github-actions --format text",
            "mucaro release governance-report --save --format text",
            "mucaro package distribution-manifest --save --format text",
        ],
    }
    if save or output_path is not None:
        head_sha = str(report.get("head_sha") or "unknown")
        path = output_path or cfg.project_root / SUPPORT_BUNDLE_DIR / f"support-bundle-{head_sha[:12]}.json"
        if not path.is_absolute():
            path = cfg.project_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
            "created_utc": _utc_now(),
            "bundle": report,
        }
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["export"] = {
            "path": str(path),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
            "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
        }
    return report


def verify_release_support_bundle(
    cfg: Settings,
    artifact_path: Path,
    *,
    require_current_head: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    path = artifact_path if artifact_path.is_absolute() else cfg.project_root / artifact_path
    checks: list[dict[str, str]] = []
    artifact: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    artifact_size_bytes: int | None = None

    if path.exists() and path.is_file():
        checks.append(_certification_gate("artifact_exists", "PASS", str(path)))
        artifact_sha256 = _sha256_file(path)
        artifact_size_bytes = path.stat().st_size
        checks.append(_certification_gate("artifact_sha256", "PASS", artifact_sha256))
        if expected_sha256:
            expected = expected_sha256.strip().lower()
            checks.append(
                _certification_gate(
                    "expected_sha256",
                    "PASS" if artifact_sha256 == expected else "FAIL",
                    f"expected={expected} actual={artifact_sha256}",
                )
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            loaded = None
            checks.append(_certification_gate("artifact_json", "FAIL", str(exc)))
        if isinstance(loaded, dict):
            artifact = loaded
            checks.append(_certification_gate("artifact_json", "PASS", "valid JSON object"))
        elif loaded is not None:
            checks.append(_certification_gate("artifact_json", "FAIL", "top-level JSON value is not an object"))
    else:
        checks.append(_certification_gate("artifact_exists", "FAIL", str(path)))

    bundle: dict[str, Any] | None = None
    if artifact is not None:
        schema_version = artifact.get("schema_version")
        checks.append(
            _certification_gate(
                "schema_version",
                "PASS" if schema_version == SUPPORT_BUNDLE_SCHEMA_VERSION else "FAIL",
                str(schema_version),
            )
        )
        created_ok, created_detail = _parse_utc_timestamp(artifact.get("created_utc"))
        checks.append(_certification_gate("created_utc", "PASS" if created_ok else "FAIL", created_detail))
        raw_bundle = artifact.get("bundle")
        if isinstance(raw_bundle, dict):
            bundle = raw_bundle
            checks.append(_certification_gate("bundle_payload", "PASS", "bundle object present"))
        else:
            checks.append(_certification_gate("bundle_payload", "FAIL", "bundle object missing"))

    head_sha: str | None = None
    case_status = None
    dashboard_status = None
    support_classification = None
    github_actions_status = None
    if bundle is not None:
        raw_head_sha = bundle.get("head_sha")
        if _is_hex_sha(raw_head_sha):
            head_sha = str(raw_head_sha)
            checks.append(_certification_gate("bundle_head_sha", "PASS", head_sha))
        else:
            checks.append(_certification_gate("bundle_head_sha", "FAIL", str(raw_head_sha)))
        case_status = bundle.get("case_status")
        checks.append(
            _certification_gate(
                "case_status",
                "PASS" if isinstance(case_status, str) and bool(case_status) else "FAIL",
                str(case_status),
            )
        )
        dashboard = bundle.get("dashboard")
        dashboard_status = bundle.get("dashboard_status")
        checks.append(
            _certification_gate(
                "dashboard_payload",
                "PASS" if isinstance(dashboard, dict) and dashboard_status else "FAIL",
                str(dashboard_status),
            )
        )
        support_classification = bundle.get("support_classification")
        github_actions_status = bundle.get("github_actions_status")

    current_head: str | None = None
    if require_current_head:
        head_ok, current_head_output = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = current_head_output if head_ok else None
        checks.append(
            _certification_gate(
                "current_head_binding",
                "PASS" if head_ok and head_sha == current_head_output else "FAIL",
                f"bundle_head={head_sha} current_head={current_head_output if head_ok else current_head_output or 'unavailable'}",
            )
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": "SUPPORT_BUNDLE_VERIFIED" if not failed else "SUPPORT_BUNDLE_VERIFY_FAILED",
        "project_root": str(cfg.project_root),
        "artifact_path": str(path),
        "artifact": {
            "path": str(path),
            "sha256": artifact_sha256,
            "size_bytes": artifact_size_bytes,
            "schema_version": artifact.get("schema_version") if artifact else None,
            "created_utc": artifact.get("created_utc") if artifact else None,
        },
        "head_sha": head_sha,
        "current_head": current_head,
        "case_status": case_status,
        "dashboard_status": dashboard_status,
        "support_classification": support_classification,
        "github_actions_status": github_actions_status,
        "checks": checks,
    }


def run_release_support_bundle(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify:
        report = verify_release_support_bundle(
            cfg,
            args.verify,
            require_current_head=args.require_current_head,
            expected_sha256=args.expected_sha256,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "SUPPORT_BUNDLE_VERIFIED" else 2
    if args.require_current_head or args.expected_sha256:
        raise MucaroCLIError("ERROR_SUPPORT_BUNDLE_VERIFY_OPTION", "--require-current-head and --expected-sha256 require --verify")
    report = build_release_support_bundle(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
        output_path=args.output,
        save=args.save or args.output is not None,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == "SUPPORT_BUNDLE_READY" else 2


def run_release_governance_report(args: argparse.Namespace, cfg: Settings) -> int:
    if args.verify:
        report = verify_release_governance_report(
            cfg,
            args.verify,
            require_current_head=args.require_current_head,
            expected_sha256=args.expected_sha256,
        )
        emit_payload(report, args.format)
        return 0 if report["status"] == "GOVERNANCE_REPORT_VERIFIED" else 2
    if args.require_current_head or args.expected_sha256:
        raise MucaroCLIError("ERROR_GOVERNANCE_REPORT_VERIFY_OPTION", "--require-current-head and --expected-sha256 require --verify")
    report = build_release_governance_report(
        cfg,
        github_workflow=args.github_workflow,
        probe_workflow=args.probe_workflow,
    )
    if args.save or args.output:
        report = {**report, "export": write_release_governance_report(cfg, report, output_path=args.output)}
    emit_payload(report, args.format)
    if args.strict and report["status"] != "GOVERNANCE_REPORT_READY":
        return 2
    return 0


def run_release_receipts(args: argparse.Namespace, cfg: Settings) -> int:
    receipts = list_release_receipts(cfg.project_root)
    current_head = None
    if args.current_head:
        head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
        current_head = head_sha if head_ok else None
        receipts = [row for row in receipts if row.get("head_sha") == current_head]
    payload = {
        "status": "RELEASE_RECEIPTS_OK",
        "project_root": str(cfg.project_root),
        "current_head": current_head,
        "count": len(receipts),
        "summary": summarize_release_receipts(receipts),
        "receipts": receipts,
    }
    emit_payload(payload, args.format)
    return 0


def _emit_release_scope_text(payload: dict[str, Any]) -> None:
    print(payload.get("status"))
    if payload.get("release_status_line"):
        print(payload["release_status_line"])
    denominator = payload.get("denominator") or {}
    if denominator:
        states = denominator.get("by_state", {})
        print(f"denominator={denominator.get('total')} " + " ".join(f"{key}={value}" for key, value in states.items()))
        print(f"arithmetic_closes={denominator.get('arithmetic_closes')}")
    for name, value in (payload.get("conditions") or {}).items():
        print(f"condition {name}={value}")
    for name, ids in (payload.get("residue") or {}).items():
        print(f"residue {name}={len(ids)} {','.join(ids)}")
    for error in payload.get("errors", []):
        print(f"{error['code']} {error['detail']}")


def run_release_scope_validate(args: argparse.Namespace, cfg: Settings) -> int:
    payload = validate_release_scope(cfg.project_root)
    if args.format == "json":
        emit_payload(payload, "json")
    else:
        _emit_release_scope_text(payload)
    return 0 if payload["status"] == "RELEASE_SCOPE_VALID" else 1


def run_release_scope_status(args: argparse.Namespace, cfg: Settings) -> int:
    payload = compute_release_status(cfg.project_root)
    if getattr(args, "write_doc", False) and payload["status"] != "MUCARO_AI_RELEASE_SCOPE_INVALID":
        payload["status_doc"] = str(write_status_doc(cfg.project_root, payload))
    if args.format == "json":
        emit_payload(payload, "json")
    else:
        _emit_release_scope_text({**payload, "errors": payload["validation"].get("errors", [])})
    return 0 if payload["status"] != "MUCARO_AI_RELEASE_SCOPE_INVALID" else 1


def run_release_scope_snapshot(args: argparse.Namespace, cfg: Settings) -> int:
    tests = {"recorded": bool(args.pytest_summary), "backend_pytest": args.pytest_summary}
    build = {
        "frontend_build": args.frontend_build,
        "frontend_dist_present": (cfg.project_root / "frontend" / "dist" / "index.html").is_file(),
    }
    snapshot = capture_state_snapshot(
        cfg.project_root,
        tests=tests,
        build=build,
        connected_resources=args.connected_resource,
        known_boundaries=args.boundary,
    )
    path = write_state_snapshot(cfg.project_root, snapshot, Path(args.output) if args.output else None)
    payload = {"status": "RELEASE_STATE_SNAPSHOT_OK", "path": str(path), "snapshot": snapshot}
    emit_payload(payload if args.format == "json" else {"status": payload["status"]}, args.format)
    return 0


def run_release_scope_freeze(args: argparse.Namespace, cfg: Settings) -> int:
    index = freeze_release_scope(cfg.project_root, args.label)
    emit_payload({"status": "RELEASE_SCOPE_FROZEN", **index}, args.format)
    return 0


def run_release_receipt_show(args: argparse.Namespace, cfg: Settings) -> int:
    payload = find_release_receipt(cfg.project_root, args.receipt_id)
    if payload is None:
        raise MucaroCLIError("ERROR_RELEASE_RECEIPT_NOT_FOUND", args.receipt_id)
    payload = {
        "status": "RELEASE_RECEIPT_OK",
        "project_root": str(cfg.project_root),
        "receipt_id": args.receipt_id,
        **payload,
    }
    emit_payload(payload, args.format)
    return 0


def _git_output(cfg: Settings, args: list[str]) -> tuple[bool, str]:
    return _command_output(cfg, ["git", *args])


def _command_output(cfg: Settings, args: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=cfg.project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    return proc.returncode == 0, proc.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_gh_error(output: str) -> str:
    if not output:
        return "gh command failed"
    try:
        payload, _end = json.JSONDecoder().raw_decode(output)
    except json.JSONDecodeError:
        return output
    if not isinstance(payload, dict):
        return output
    message = payload.get("message")
    status = payload.get("status")
    if not message:
        return output
    prefix = f"HTTP {status}: " if status else ""
    details = []
    for line in output[_end:].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("gh: "):
            line = line[4:].strip()
        duplicate_status = f"{message} (HTTP {status})" if status else message
        if line in {message, duplicate_status}:
            continue
        if line and line not in details:
            details.append(line)
    suffix = f"; {'; '.join(details)}" if details else ""
    return f"{prefix}{message}{suffix}"


def _gh_error_required_scope(error: str | None) -> str | None:
    if not error:
        return None
    match = re.search(r'needs the "([^"]+)" scope', error)
    return match.group(1) if match else None


def _redact_remote_url(remote_url: str) -> str:
    if "://" not in remote_url:
        return remote_url
    scheme, rest = remote_url.split("://", 1)
    if "@" not in rest:
        return remote_url
    return f"{scheme}://<redacted>@{rest.split('@', 1)[1]}"


def _certification_gate(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _github_repo_from_origin(remote_url: str | None) -> str | None:
    if not remote_url:
        return None
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _gh_json(cfg: Settings, args: list[str]) -> tuple[bool, Any, str]:
    ok, output = _command_output(cfg, ["gh", *args])
    if not ok:
        return False, None, _format_gh_error(output)
    try:
        return True, json.loads(output), output
    except json.JSONDecodeError as exc:
        return False, None, f"invalid gh JSON: {exc}"


def _find_remote_tag_commit(cfg: Settings, tag_name: str, head_sha: str | None) -> tuple[bool, str]:
    ok, output = _git_output(cfg, ["ls-remote", "origin", f"refs/tags/{tag_name}", f"refs/tags/{tag_name}^{{}}"])
    if not ok:
        return False, output or "remote tag lookup failed"
    dereferenced = None
    direct = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            dereferenced = sha
        else:
            direct = sha
    resolved = dereferenced or direct
    if not resolved:
        return False, "remote tag not found"
    if head_sha and resolved != head_sha:
        return False, f"{tag_name} -> {resolved}, expected {head_sha}"
    return True, f"{tag_name} -> {resolved}"


def _run_failed_before_jobs(latest: dict[str, Any], jobs_count: int | None) -> bool:
    return (
        latest.get("status") == "completed"
        and latest.get("conclusion") == "startup_failure"
        and jobs_count == 0
    )


def _github_startup_blocker(
    *,
    ci_latest: dict[str, Any] | None,
    ci_jobs_count: int | None,
    probe_latest: dict[str, Any] | None = None,
    probe_jobs_count: int | None = None,
    branch_protection_error: str | None = None,
    billing_error: str | None = None,
    self_hosted_runner_count: int | None = None,
) -> dict[str, Any]:
    ci_before_jobs = bool(ci_latest and _run_failed_before_jobs(ci_latest, ci_jobs_count))
    probe_before_jobs = bool(probe_latest and _run_failed_before_jobs(probe_latest, probe_jobs_count))
    isolated = ci_before_jobs and (probe_latest is None or probe_before_jobs)
    suspected_causes: list[str] = []
    next_actions: list[str] = []
    if isolated:
        suspected_causes.extend(["actions_billing_or_spending_limit", "hosted_runner_availability", "account_or_repository_policy"])
        next_actions.append("Inspect repository Settings -> Actions -> General and Settings -> Actions -> Runners.")
    if branch_protection_error and ("Upgrade to GitHub Pro" in branch_protection_error or "make this repository public" in branch_protection_error):
        suspected_causes.append("private_branch_protection_plan_gate")
        next_actions.append("Upgrade GitHub plan or make the repository public before enforcing private-repo branch protection.")
    if billing_error:
        suspected_causes.append("actions_billing_visibility_unverified")
        next_actions.append("Run `gh auth refresh -h github.com -s user` and inspect GitHub Actions billing/minutes.")
    return {
        "status": "FAIL" if isolated else "OPEN" if suspected_causes else "PASS",
        "ci_before_jobs": ci_before_jobs,
        "probe_before_jobs": probe_before_jobs,
        "isolated_before_jobs": isolated,
        "self_hosted_runner_count": self_hosted_runner_count,
        "branch_protection_error": branch_protection_error,
        "billing_error": billing_error,
        "suspected_causes": sorted(set(suspected_causes)),
        "next_actions": sorted(set(next_actions)),
    }


def build_github_governance_status(
    cfg: Settings,
    *,
    repo: str | None,
    branch: str | None,
    head_sha: str | None,
    tags_at_head: list[str],
    workflow_name: str = "CI",
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    github: dict[str, Any] = {
        "enabled": True,
        "repo": repo,
        "branch": branch,
        "workflow": workflow_name,
        "branch_protected": None,
        "required_status_checks": None,
        "latest_ci": None,
        "branch_protection_api": None,
        "startup_blocker": None,
        "remote_tag_bindings": [],
        "errors": [],
    }
    gates: list[dict[str, str]] = []

    if not repo:
        gates.append(_certification_gate("github_repo", "WARN", "origin is not a github.com repository"))
        return github, gates
    gates.append(_certification_gate("github_repo", "PASS", repo))

    if not branch:
        gates.append(_certification_gate("github_branch", "WARN", "branch unavailable"))
        return github, gates

    branch_ok, branch_payload, branch_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}"])
    if not branch_ok or not isinstance(branch_payload, dict):
        github["errors"].append(branch_error)
        gates.append(_certification_gate("github_branch_protection", "WARN", branch_error or "branch lookup failed"))
    else:
        protected = bool(branch_payload.get("protected"))
        github["branch_protected"] = protected
        protection = branch_payload.get("protection", {})
        required_checks = False
        if isinstance(protection, dict):
            required = protection.get("required_status_checks", {})
            if isinstance(required, dict):
                contexts = required.get("contexts") or []
                checks = required.get("checks") or []
                enforcement = required.get("enforcement_level")
                required_checks = bool(contexts or checks or enforcement == "non_admins")
        github["required_status_checks"] = required_checks
        detail = "protected" if protected else "unprotected"
        if protected and required_checks:
            detail = "protected with required status checks"
        gates.append(_certification_gate("github_branch_protection", "PASS" if protected else "WARN", detail))

    protection_error_for_blocker: str | None = None
    protection_api_ok, protection_api_payload, protection_api_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}/protection"])
    github["branch_protection_api"] = {"available": protection_api_ok}
    if protection_api_ok and isinstance(protection_api_payload, dict):
        github["branch_protection_api"]["required_status_checks"] = protection_api_payload.get("required_status_checks")
        gates.append(_certification_gate("github_branch_protection_api", "PASS", "branch protection endpoint readable"))
    else:
        protection_error_for_blocker = protection_api_error
        github["branch_protection_api"]["error"] = protection_api_error
        gates.append(_certification_gate("github_branch_protection_api", "WARN", protection_api_error or "branch protection endpoint unavailable"))

    ci_ok, ci_payload, ci_error = _gh_json(
        cfg,
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow_name,
            "--branch",
            branch,
            "--limit",
            "1",
            "--json",
            "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
        ],
    )
    if not ci_ok or not isinstance(ci_payload, list) or not ci_payload:
        github["errors"].append(ci_error)
        gates.append(_certification_gate("github_latest_ci", "WARN", ci_error or "latest CI lookup failed"))
    else:
        latest = ci_payload[0]
        if isinstance(latest, dict):
            jobs_count: int | None = None
            jobs_error: str | None = None
            if latest.get("status") == "completed" and latest.get("conclusion") == "startup_failure":
                jobs_count, jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=latest.get("databaseId"))
            latest_ci = {
                "database_id": latest.get("databaseId"),
                "status": latest.get("status"),
                "conclusion": latest.get("conclusion"),
                "head_sha": latest.get("headSha"),
                "url": latest.get("url"),
                "jobs_count": jobs_count,
                "startup_layer": "before_jobs" if _run_failed_before_jobs(latest, jobs_count) else None,
            }
            github["latest_ci"] = latest_ci
            ci_matches_head = not head_sha or latest.get("headSha") == head_sha
            ci_passed = latest.get("status") == "completed" and latest.get("conclusion") == "success"
            if ci_matches_head and ci_passed:
                run_evidence = _run_evidence(latest)
                detail = f"success {latest.get('headSha')}"
                if run_evidence:
                    detail = f"{detail} {run_evidence}"
                gates.append(_certification_gate("github_latest_ci", "PASS", detail))
            elif ci_matches_head and latest.get("status") == "completed":
                run_evidence = _run_evidence(latest)
                detail = f"{latest.get('status')} {latest.get('conclusion') or 'unknown'} {latest.get('headSha')}"
                if run_evidence:
                    detail = f"{detail} {run_evidence}"
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "FAIL",
                        detail,
                    )
                )
                if jobs_error:
                    gates.append(_certification_gate("github_ci_jobs_lookup", "WARN", jobs_error))
                elif _run_failed_before_jobs(latest, jobs_count):
                    gates.append(
                        _certification_gate(
                            "github_hosted_runner_startup",
                            "FAIL",
                            "workflow failed before GitHub created jobs; inspect Actions billing, spending limit, runner availability, and account policy",
                        )
                    )
                    github["startup_blocker"] = _github_startup_blocker(
                        ci_latest=latest,
                        ci_jobs_count=jobs_count,
                        branch_protection_error=protection_error_for_blocker,
                    )
            elif ci_matches_head:
                run_evidence = _run_evidence(latest)
                detail = f"{latest.get('status')} {latest.get('conclusion') or 'pending'} {latest.get('headSha')}"
                if run_evidence:
                    detail = f"{detail} {run_evidence}"
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "WARN",
                        detail,
                    )
                )
            else:
                run_evidence = _run_evidence(latest)
                detail = f"latest {latest.get('headSha')} does not match HEAD {head_sha}"
                if run_evidence:
                    detail = f"{detail} {run_evidence}"
                gates.append(
                    _certification_gate(
                        "github_latest_ci",
                        "FAIL",
                        detail,
                    )
                )

    if not tags_at_head:
        gates.append(_certification_gate("github_tag_binding", "WARN", "no local tag at HEAD"))
    else:
        for tag_name in tags_at_head:
            tag_ok, tag_detail = _find_remote_tag_commit(cfg, tag_name, head_sha)
            github["remote_tag_bindings"].append({"tag": tag_name, "status": "PASS" if tag_ok else "FAIL", "detail": tag_detail})
            gates.append(_certification_gate("github_tag_binding", "PASS" if tag_ok else "FAIL", tag_detail))

    return github, gates


def _latest_workflow_run(
    cfg: Settings,
    *,
    repo: str,
    workflow_name: str,
    branch: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    args = [
        "run",
        "list",
        "--repo",
        repo,
        "--workflow",
        workflow_name,
        "--limit",
        "1",
        "--json",
        "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
    ]
    if branch:
        args.extend(["--branch", branch])
    ok, payload, error = _gh_json(cfg, args)
    if not ok or not isinstance(payload, list) or not payload:
        return None, error or "workflow run lookup failed"
    latest = payload[0]
    if not isinstance(latest, dict):
        return None, "workflow run payload invalid"
    return latest, None


def _current_head_startup_failure_runs(
    cfg: Settings,
    *,
    repo: str,
    branch: str | None,
    head_sha: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    if not head_sha:
        return [], "head SHA unavailable"
    args = [
        "run",
        "list",
        "--repo",
        repo,
        "--limit",
        "10",
        "--json",
        "databaseId,status,conclusion,headSha,url,name,event,createdAt,updatedAt",
    ]
    if branch:
        args.extend(["--branch", branch])
    ok, payload, error = _gh_json(cfg, args)
    if not ok or not isinstance(payload, list):
        return [], error or "current-head run lookup failed"

    runs: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        if row.get("headSha") != head_sha:
            continue
        if row.get("event") != "push":
            continue
        if row.get("status") != "completed" or row.get("conclusion") != "startup_failure":
            continue
        jobs_count, jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=row.get("databaseId"))
        runs.append(
            {
                "database_id": row.get("databaseId"),
                "name": row.get("name"),
                "event": row.get("event"),
                "status": row.get("status"),
                "conclusion": row.get("conclusion"),
                "head_sha": row.get("headSha"),
                "url": row.get("url"),
                "created_at": row.get("createdAt"),
                "updated_at": row.get("updatedAt"),
                "jobs_count": jobs_count,
                "jobs_error": jobs_error,
                "startup_layer": "before_jobs" if _run_failed_before_jobs(row, jobs_count) else None,
            }
        )
    return runs, None


def _workflow_run_jobs_count(cfg: Settings, *, repo: str, run_id: Any) -> tuple[int | None, str | None]:
    if not run_id:
        return None, "run id unavailable"
    ok, payload, error = _gh_json(cfg, ["api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
    if not ok or not isinstance(payload, dict):
        return None, error or "workflow jobs lookup failed"
    total_count = payload.get("total_count")
    return total_count if isinstance(total_count, int) else None, None


def _commit_check_suites(cfg: Settings, *, repo: str, head_sha: str | None) -> tuple[dict[str, Any], str | None]:
    if not head_sha:
        return {"available": False, "total_count": None, "suites": []}, "head SHA unavailable"
    ok, payload, error = _gh_json(cfg, ["api", f"repos/{repo}/commits/{head_sha}/check-suites"])
    if not ok or not isinstance(payload, dict):
        return {"available": False, "total_count": None, "suites": []}, error or "check-suite lookup failed"
    raw_suites = payload.get("check_suites", [])
    suites = []
    if isinstance(raw_suites, list):
        for row in raw_suites:
            if not isinstance(row, dict):
                continue
            app = row.get("app")
            app_slug = app.get("slug") if isinstance(app, dict) else None
            suites.append(
                {
                    "id": row.get("id"),
                    "app": app_slug,
                    "status": row.get("status"),
                    "conclusion": row.get("conclusion"),
                    "latest_check_runs_count": row.get("latest_check_runs_count"),
                    "url": row.get("url"),
                    "check_runs_url": row.get("check_runs_url"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
    total_count = payload.get("total_count")
    return {
        "available": True,
        "total_count": total_count if isinstance(total_count, int) else len(suites),
        "suites": suites,
    }, None


def _github_actions_check_suite_blocker(check_suites: dict[str, Any]) -> bool:
    suites = check_suites.get("suites", [])
    if not isinstance(suites, list):
        return False
    github_actions_suites = [suite for suite in suites if isinstance(suite, dict) and suite.get("app") == "github-actions"]
    return bool(github_actions_suites) and all(
        suite.get("status") == "completed"
        and suite.get("conclusion") == "startup_failure"
        and suite.get("latest_check_runs_count") == 0
        for suite in github_actions_suites
    )


def _github_actions_check_suite_counts(check_suites: dict[str, Any]) -> dict[str, int]:
    suites = check_suites.get("suites", [])
    if not isinstance(suites, list):
        suites = []
    github_actions_suites = [suite for suite in suites if isinstance(suite, dict) and suite.get("app") == "github-actions"]
    zero_check_run_startup_failures = [
        suite
        for suite in github_actions_suites
        if suite.get("status") == "completed"
        and suite.get("conclusion") == "startup_failure"
        and suite.get("latest_check_runs_count") == 0
    ]
    queued_external_suites = [
        suite
        for suite in suites
        if isinstance(suite, dict) and suite.get("app") != "github-actions" and suite.get("status") == "queued"
    ]
    return {
        "total": len(suites),
        "github_actions": len(github_actions_suites),
        "github_actions_zero_check_run_startup_failures": len(zero_check_run_startup_failures),
        "queued_external": len(queued_external_suites),
    }


def _run_summary(latest: dict[str, Any] | None, jobs_count: int | None = None) -> dict[str, Any] | None:
    if latest is None:
        return None
    return {
        "database_id": latest.get("databaseId"),
        "status": latest.get("status"),
        "conclusion": latest.get("conclusion"),
        "head_sha": latest.get("headSha"),
        "url": latest.get("url"),
        "jobs_count": jobs_count,
    }


def _git_branch_for_head(cfg: Settings, head_sha: str | None) -> tuple[bool, str]:
    branch_ok, branch = _git_output(cfg, ["branch", "--show-current"])
    if branch_ok and branch:
        return True, branch
    if not head_sha:
        return False, ""

    remote_ok, remote_ref = _git_output(cfg, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if not remote_ok or not remote_ref.startswith("origin/"):
        return False, ""
    remote_head_ok, remote_head = _git_output(cfg, ["rev-parse", remote_ref])
    if not remote_head_ok or remote_head != head_sha:
        return False, ""
    return True, remote_ref.removeprefix("origin/")


def _git_upstream_for_head(
    cfg: Settings,
    head_sha: str | None,
    branch: str | None,
) -> tuple[bool, str]:
    upstream_ok, upstream = _git_output(
        cfg,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
    )
    if upstream_ok and upstream:
        return True, upstream
    if not head_sha or not branch:
        return False, ""

    remote_ok, remote_ref = _git_output(cfg, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    if not remote_ok or remote_ref != f"origin/{branch}":
        return False, ""
    remote_head_ok, remote_head = _git_output(cfg, ["rev-parse", remote_ref])
    if not remote_head_ok or remote_head != head_sha:
        return False, ""
    return True, remote_ref


def _run_evidence(latest: dict[str, Any]) -> str:
    parts = []
    if latest.get("databaseId"):
        parts.append(f"run_id={latest.get('databaseId')}")
    if latest.get("url"):
        parts.append(f"url={latest.get('url')}")
    return " ".join(parts)


def _classify_latest_run(
    *,
    name: str,
    latest: dict[str, Any] | None,
    error: str | None,
    head_sha: str | None,
    jobs_count: int | None,
) -> dict[str, str]:
    if latest is None:
        return _certification_gate(name, "WARN", error or "latest workflow run unavailable")
    run_head = latest.get("headSha")
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    jobs_detail = f" jobs={jobs_count}" if jobs_count is not None else ""
    run_evidence = _run_evidence(latest)
    evidence_detail = f" {run_evidence}" if run_evidence else ""
    if head_sha and run_head != head_sha:
        return _certification_gate(name, "FAIL", f"latest {run_head} does not match HEAD {head_sha}{jobs_detail}{evidence_detail}")
    if status == "completed" and conclusion == "success":
        return _certification_gate(name, "PASS", f"success {run_head}{jobs_detail}{evidence_detail}")
    if status == "completed":
        return _certification_gate(name, "FAIL", f"{status} {conclusion or 'unknown'} {run_head}{jobs_detail}{evidence_detail}")
    return _certification_gate(name, "WARN", f"{status} {conclusion or 'pending'} {run_head}{jobs_detail}{evidence_detail}")


def _classify_startup_probe(
    *,
    ci_latest: dict[str, Any] | None,
    ci_jobs_count: int | None,
    probe_latest: dict[str, Any] | None,
    probe_error: str | None,
    head_sha: str | None,
    probe_jobs_count: int | None,
) -> dict[str, str]:
    exact_head_ci_passed = bool(
        head_sha
        and ci_latest
        and ci_latest.get("headSha") == head_sha
        and ci_latest.get("status") == "completed"
        and ci_latest.get("conclusion") == "success"
        and isinstance(ci_jobs_count, int)
        and ci_jobs_count > 0
    )
    if exact_head_ci_passed:
        probe_evidence = _run_evidence(probe_latest) if probe_latest else ""
        suffix = f"; latest_probe {probe_evidence}" if probe_evidence else ""
        return _certification_gate(
            "startup_probe",
            "PASS",
            f"NOT_REQUIRED exact-head CI passed with jobs={ci_jobs_count}{suffix}",
        )
    return _classify_latest_run(
        name="startup_probe",
        latest=probe_latest,
        error=probe_error,
        head_sha=head_sha,
        jobs_count=probe_jobs_count,
    )


def _github_actions_support_summary(
    *,
    status: str,
    repo: str,
    branch: str | None,
    head_sha: str | None,
    runs: dict[str, Any],
    check_suites: dict[str, Any],
    startup_blocker: dict[str, Any],
    branch_protection: dict[str, Any],
    actions_permissions: dict[str, Any],
    workflow_permissions: dict[str, Any],
    billing: dict[str, Any],
    next_actions: list[str],
    current_head_startup_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ci_run = runs.get("ci") if isinstance(runs.get("ci"), dict) else {}
    startup_probe_run = runs.get("startup_probe") if isinstance(runs.get("startup_probe"), dict) else {}
    current_head_startup_runs = current_head_startup_runs or []
    current_head_startup_run_ids = [
        row.get("database_id")
        for row in current_head_startup_runs
        if row.get("database_id")
    ]
    return {
        "status": status,
        "repo": repo,
        "branch": branch,
        "head_sha": head_sha,
        "classification": "HOSTED_STARTUP_BEFORE_JOBS"
        if startup_blocker.get("isolated_before_jobs") or current_head_startup_run_ids
        else "HOSTED_GOVERNANCE_REVIEW",
        "ci_run_id": ci_run.get("database_id"),
        "ci_run_url": ci_run.get("url"),
        "ci_jobs_count": ci_run.get("jobs_count"),
        "startup_probe_run_id": startup_probe_run.get("database_id"),
        "startup_probe_run_url": startup_probe_run.get("url"),
        "startup_probe_jobs_count": startup_probe_run.get("jobs_count"),
        "current_head_startup_runs": current_head_startup_runs,
        "current_head_startup_run_ids": current_head_startup_run_ids,
        "check_suite_counts": _github_actions_check_suite_counts(check_suites),
        "branch_protection_available": branch_protection.get("available"),
        "branch_protection_error": branch_protection.get("error"),
        "actions_permissions_available": actions_permissions.get("available"),
        "actions_enabled": actions_permissions.get("enabled"),
        "allowed_actions": actions_permissions.get("allowed_actions"),
        "workflow_permissions_available": workflow_permissions.get("available"),
        "default_workflow_permissions": workflow_permissions.get("default_workflow_permissions"),
        "billing_available": billing.get("available"),
        "billing_error": billing.get("error"),
        "billing_required_scope": billing.get("required_scope"),
        "suspected_causes": startup_blocker.get("suspected_causes", []),
        "next_actions": next_actions,
    }


def build_github_actions_diagnostic_status(
    cfg: Settings,
    *,
    workflow_name: str = "CI",
    probe_workflow_name: str = "CI Startup Probe",
) -> dict[str, Any]:
    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    branch_ok, branch = _git_branch_for_head(cfg, head_sha if head_ok else None)
    remote_ok, remote_url = _git_output(cfg, ["remote", "get-url", "origin"])
    repo = _github_repo_from_origin(remote_url if remote_ok else None)
    checks: list[dict[str, str]] = []
    next_actions: list[str] = []

    if not repo:
        checks.append(_certification_gate("github_repo", "FAIL", "origin is not a github.com repository"))
        return {
            "status": "GITHUB_ACTIONS_DIAGNOSTIC_FAIL",
            "repo": None,
            "branch": branch if branch_ok else None,
            "head_sha": head_sha if head_ok else None,
            "checks": checks,
            "workflows": {},
            "next_actions": ["Configure origin to a GitHub repository before running hosted governance diagnostics."],
        }
    checks.append(_certification_gate("github_repo", "PASS", repo))

    actions_ok, actions_payload, actions_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/permissions"])
    actions_permissions: dict[str, Any] = {"available": actions_ok}
    if actions_ok and isinstance(actions_payload, dict):
        enabled = actions_payload.get("enabled") is True
        allowed_actions = actions_payload.get("allowed_actions")
        actions_permissions.update(
            {
                "enabled": enabled,
                "allowed_actions": allowed_actions,
                "sha_pinning_required": actions_payload.get("sha_pinning_required"),
            }
        )
        checks.append(_certification_gate("actions_enabled", "PASS" if enabled else "FAIL", f"enabled={enabled} allowed_actions={allowed_actions}"))
        if not enabled:
            next_actions.append("Enable GitHub Actions in repository Settings -> Actions -> General.")
    else:
        actions_permissions["error"] = actions_error
        checks.append(_certification_gate("actions_enabled", "WARN", actions_error or "actions permission lookup failed"))

    token_ok, token_payload, token_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/permissions/workflow"])
    workflow_permissions: dict[str, Any] = {"available": token_ok}
    if token_ok and isinstance(token_payload, dict):
        default_permissions = token_payload.get("default_workflow_permissions")
        workflow_permissions.update(
            {
                "default_workflow_permissions": default_permissions,
                "can_approve_pull_request_reviews": token_payload.get("can_approve_pull_request_reviews"),
            }
        )
        checks.append(_certification_gate("workflow_token_permissions", "PASS", f"default_workflow_permissions={default_permissions}"))
    else:
        workflow_permissions["error"] = token_error
        checks.append(_certification_gate("workflow_token_permissions", "WARN", token_error or "workflow token permission lookup failed"))

    branch_protection: dict[str, Any] = {"available": False}
    branch_protection_error_for_blocker: str | None = None
    if not branch_ok:
        branch_protection["error"] = "branch unavailable"
        branch_protection_error_for_blocker = "branch unavailable"
        checks.append(_certification_gate("branch_protection_api", "WARN", "branch unavailable"))
    else:
        protection_ok, protection_payload, protection_error = _gh_json(cfg, ["api", f"repos/{repo}/branches/{branch}/protection"])
        branch_protection["available"] = protection_ok
        if protection_ok and isinstance(protection_payload, dict):
            branch_protection["required_status_checks"] = protection_payload.get("required_status_checks")
            checks.append(_certification_gate("branch_protection_api", "PASS", "branch protection endpoint readable"))
        else:
            branch_protection["error"] = protection_error
            branch_protection_error_for_blocker = protection_error
            checks.append(_certification_gate("branch_protection_api", "WARN", protection_error or "branch protection endpoint unavailable"))
            if protection_error and ("Upgrade to GitHub Pro" in protection_error or "make this repository public" in protection_error):
                next_actions.append("Upgrade GitHub plan or make the repository public before enforcing private-repo branch protection.")

    runners_ok, runners_payload, runners_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/runners"])
    runner_inventory: dict[str, Any] = {"available": runners_ok}
    self_hosted_runner_count: int | None = None
    if runners_ok and isinstance(runners_payload, dict):
        total_count = runners_payload.get("total_count")
        self_hosted_runner_count = total_count if isinstance(total_count, int) else None
        runner_inventory["self_hosted_total_count"] = total_count
        checks.append(
            _certification_gate(
                "self_hosted_runner_inventory",
                "PASS",
                f"self_hosted_total_count={total_count}; ubuntu-latest uses GitHub-hosted runner capacity",
            )
        )
    else:
        runner_inventory["error"] = runners_error
        checks.append(_certification_gate("self_hosted_runner_inventory", "WARN", runners_error or "runner inventory lookup failed"))

    workflows_ok, workflows_payload, workflows_error = _gh_json(cfg, ["api", f"repos/{repo}/actions/workflows"])
    workflows: dict[str, Any] = {}
    if workflows_ok and isinstance(workflows_payload, dict):
        workflow_rows = workflows_payload.get("workflows", [])
        if isinstance(workflow_rows, list):
            for row in workflow_rows:
                if isinstance(row, dict):
                    workflows[str(row.get("name"))] = {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "path": row.get("path"),
                        "state": row.get("state"),
                    }
        for expected in [workflow_name, probe_workflow_name]:
            row = workflows.get(expected)
            if row and row.get("state") == "active":
                checks.append(_certification_gate(f"workflow_registered:{expected}", "PASS", f"{row.get('path')} active"))
            else:
                checks.append(_certification_gate(f"workflow_registered:{expected}", "WARN", "workflow not registered active"))
    else:
        checks.append(_certification_gate("workflow_registry", "WARN", workflows_error or "workflow registry lookup failed"))

    ci_latest, ci_error = _latest_workflow_run(cfg, repo=repo, workflow_name=workflow_name, branch=branch if branch_ok else None)
    ci_jobs_count, ci_jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=ci_latest.get("databaseId") if ci_latest else None)
    if ci_jobs_error and ci_latest:
        checks.append(_certification_gate("ci_jobs_lookup", "WARN", ci_jobs_error))
    checks.append(
        _classify_latest_run(
            name="latest_ci",
            latest=ci_latest,
            error=ci_error,
            head_sha=head_sha if head_ok else None,
            jobs_count=ci_jobs_count,
        )
    )

    probe_latest, probe_error = _latest_workflow_run(
        cfg,
        repo=repo,
        workflow_name=probe_workflow_name,
        branch=branch if branch_ok else None,
    )
    probe_jobs_count, probe_jobs_error = _workflow_run_jobs_count(cfg, repo=repo, run_id=probe_latest.get("databaseId") if probe_latest else None)
    if probe_jobs_error and probe_latest:
        checks.append(_certification_gate("probe_jobs_lookup", "WARN", probe_jobs_error))
    checks.append(
        _classify_startup_probe(
            ci_latest=ci_latest,
            ci_jobs_count=ci_jobs_count,
            probe_latest=probe_latest,
            probe_error=probe_error,
            head_sha=head_sha if head_ok else None,
            probe_jobs_count=probe_jobs_count,
        )
    )

    current_head_startup_runs, current_head_startup_error = _current_head_startup_failure_runs(
        cfg,
        repo=repo,
        branch=branch if branch_ok else None,
        head_sha=head_sha if head_ok else None,
    )
    before_job_run_ids = [
        run.get("database_id")
        for run in current_head_startup_runs
        if run.get("startup_layer") == "before_jobs" and run.get("database_id")
    ]
    if before_job_run_ids:
        checks.append(
            _certification_gate(
                "current_head_push_startup",
                "FAIL",
                f"current HEAD push startup_failure before jobs run_ids={','.join(str(run_id) for run_id in before_job_run_ids)}",
            )
        )
        next_actions.append(f"Inspect current-head push startup run {before_job_run_ids[0]}.")
    elif current_head_startup_error:
        checks.append(_certification_gate("current_head_push_startup", "WARN", current_head_startup_error))

    check_suites, check_suites_error = _commit_check_suites(cfg, repo=repo, head_sha=head_sha if head_ok else None)
    if check_suites_error:
        checks.append(_certification_gate("check_suites_lookup", "WARN", check_suites_error))
    else:
        checks.append(
            _certification_gate(
                "check_suites_lookup",
                "PASS",
                f"total_count={check_suites.get('total_count')}",
            )
        )
    if _github_actions_check_suite_blocker(check_suites):
        checks.append(
            _certification_gate(
                "github_actions_check_suites_before_runs",
                "FAIL",
                "GitHub Actions check suites completed startup_failure with latest_check_runs_count=0",
            )
        )

    owner = repo.split("/", 1)[0]
    billing_ok, billing_payload, billing_error = _gh_json(cfg, ["api", f"users/{owner}/settings/billing/actions"])
    billing: dict[str, Any] = {"available": billing_ok}
    if billing_ok and isinstance(billing_payload, dict):
        billing.update(billing_payload)
        checks.append(_certification_gate("actions_billing", "PASS", "billing endpoint readable"))
    else:
        required_scope = _gh_error_required_scope(billing_error)
        billing["error"] = billing_error
        if required_scope:
            billing["required_scope"] = required_scope
        billing_detail = billing_error or "billing endpoint unavailable"
        if required_scope and f'needs the "{required_scope}" scope' not in billing_detail and f"requires {required_scope} scope" not in billing_detail:
            billing_detail = f"{billing_detail}; requires {required_scope} scope"
        checks.append(_certification_gate("actions_billing", "WARN", billing_detail))
        if required_scope:
            next_actions.append(f"Run `gh auth refresh -h github.com -s {required_scope}` and inspect GitHub Actions billing/minutes.")
        else:
            next_actions.append("Inspect GitHub Actions billing/minutes from account settings.")

    startup_blocker = _github_startup_blocker(
        ci_latest=ci_latest,
        ci_jobs_count=ci_jobs_count,
        probe_latest=probe_latest,
        probe_jobs_count=probe_jobs_count,
        branch_protection_error=branch_protection_error_for_blocker,
        billing_error=billing_error,
        self_hosted_runner_count=self_hosted_runner_count,
    )
    if (
        ci_latest
        and probe_latest
        and ci_latest.get("status") == "completed"
        and probe_latest.get("status") == "completed"
        and ci_latest.get("conclusion") == "startup_failure"
        and probe_latest.get("conclusion") == "startup_failure"
        and _run_failed_before_jobs(ci_latest, ci_jobs_count)
        and _run_failed_before_jobs(probe_latest, probe_jobs_count)
    ):
        checks.append(
            _certification_gate(
                "startup_layer_isolated",
                "FAIL",
                "CI and startup probe both failed before jobs; inspect account billing, spending limit, and runner availability",
            )
        )
        next_actions.append("Inspect repository Settings -> Actions -> General and Settings -> Actions -> Runners.")
    next_actions.extend(startup_blocker["next_actions"])

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "GITHUB_ACTIONS_DIAGNOSTIC_FAIL" if failed else "GITHUB_ACTIONS_DIAGNOSTIC_WARN" if warned else "GITHUB_ACTIONS_DIAGNOSTIC_OK"
    runs = {
        "ci": _run_summary(ci_latest, ci_jobs_count),
        "startup_probe": _run_summary(probe_latest, probe_jobs_count),
    }
    next_actions = sorted(set(next_actions))
    support_summary = _github_actions_support_summary(
        status=status,
        repo=repo,
        branch=branch if branch_ok else None,
        head_sha=head_sha if head_ok else None,
        runs=runs,
        check_suites=check_suites,
        startup_blocker=startup_blocker,
        branch_protection=branch_protection,
        actions_permissions=actions_permissions,
        workflow_permissions=workflow_permissions,
        billing=billing,
        next_actions=next_actions,
        current_head_startup_runs=current_head_startup_runs,
    )
    return {
        "status": status,
        "repo": repo,
        "branch": branch if branch_ok else None,
        "head_sha": head_sha if head_ok else None,
        "checks": checks,
        "actions_permissions": actions_permissions,
        "workflow_permissions": workflow_permissions,
        "branch_protection": branch_protection,
        "runner_inventory": runner_inventory,
        "check_suites": check_suites,
        "startup_blocker": startup_blocker,
        "workflows": workflows,
        "runs": runs,
        "current_head_startup_runs": current_head_startup_runs,
        "billing": billing,
        "support_summary": support_summary,
        "next_actions": next_actions,
    }


def build_certification_status(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
) -> dict[str, Any]:
    audit = build_product_audit(cfg)
    doctor = build_doctor_report(cfg)
    validation = validate_project_json(cfg)

    head_ok, head_sha = _git_output(cfg, ["rev-parse", "HEAD"])
    branch_ok, branch = _git_branch_for_head(cfg, head_sha if head_ok else None)
    status_ok, worktree_status = _git_output(cfg, ["status", "--short"])
    tags_ok, tags_output = _git_output(cfg, ["tag", "--points-at", "HEAD"])
    upstream_ok, upstream = _git_upstream_for_head(
        cfg,
        head_sha if head_ok else None,
        branch if branch_ok else None,
    )
    remote_ok, remote_url = _git_output(cfg, ["remote", "get-url", "origin"])

    tags_at_head = [line for line in tags_output.splitlines() if line.strip()] if tags_ok else []
    worktree_clean = status_ok and not worktree_status
    repo = _github_repo_from_origin(remote_url if remote_ok else None)

    gates = [
        _certification_gate(
            "product_audit",
            "PASS" if audit.get("status") == "PRODUCT_AUDIT_OK" else "FAIL",
            str(audit.get("status")),
        ),
        _certification_gate(
            "doctor",
            "PASS" if doctor.get("status") == "DOCTOR_OK" else "WARN" if doctor.get("status") == "DOCTOR_WARN" else "FAIL",
            str(doctor.get("status")),
        ),
        _certification_gate(
            "json_validation",
            "PASS" if validation.get("status") == "JSON_OK" else "FAIL",
            str(validation.get("status")),
        ),
        _certification_gate(
            "git_repository",
            "PASS" if head_ok else "WARN",
            head_sha if head_ok else "git metadata unavailable",
        ),
        _certification_gate(
            "worktree_clean",
            "PASS" if worktree_clean else "FAIL" if status_ok else "WARN",
            "clean" if worktree_clean else worktree_status or "git status unavailable",
        ),
        _certification_gate(
            "baseline_tag",
            "PASS" if tags_at_head else "WARN",
            ",".join(tags_at_head) if tags_at_head else "no tag points at HEAD",
        ),
        _certification_gate(
            "upstream",
            "PASS" if upstream_ok else "WARN",
            upstream if upstream_ok else "no upstream configured",
        ),
    ]
    github: dict[str, Any] = {"enabled": False}
    if include_github:
        github, github_gates = build_github_governance_status(
            cfg,
            repo=repo,
            branch=branch if branch_ok else None,
            head_sha=head_sha if head_ok else None,
            tags_at_head=tags_at_head,
            workflow_name=github_workflow,
        )
        gates.extend(github_gates)

    failed = [gate for gate in gates if gate["status"] == "FAIL"]
    warned = [gate for gate in gates if gate["status"] == "WARN"]
    status = "CERTIFICATION_FAIL" if failed else "CERTIFICATION_WARN" if warned else "CERTIFICATION_READY"

    git_payload = {
        "branch": branch if branch_ok else None,
        "head_sha": head_sha if head_ok else None,
        "tags_at_head": tags_at_head,
        "worktree_clean": worktree_clean if status_ok else None,
        "upstream": upstream if upstream_ok else None,
        "origin": _redact_remote_url(remote_url) if remote_ok else None,
    }
    ref = tags_at_head[0] if tags_at_head else head_sha if head_ok else "main"
    return {
        "status": status,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "git": git_payload,
        "gates": gates,
        "doctor_status": doctor.get("status"),
        "product_audit_status": audit.get("status"),
        "json_status": validation.get("status"),
        "github": github,
        "next_commands": [
            "python3 scripts/certify_release.py",
            f"python3 scripts/certify_fresh_clone.py --ref {ref}",
        ],
    }


def _gate_by_name(gates: list[dict[str, str]], name: str) -> dict[str, str] | None:
    return next((gate for gate in gates if gate.get("name") == name), None)


def _requirement_status(audit: dict[str, Any], requirement_id: str) -> str | None:
    for row in audit.get("requirements", []):
        if isinstance(row, dict) and row.get("requirement_id") == requirement_id:
            status = row.get("status")
            return status if isinstance(status, str) else None
    return None


def _release_state(state: str, status: str, evidence: str, next_step: str) -> dict[str, str]:
    return {
        "state": state,
        "status": status,
        "evidence": evidence,
        "next_step": next_step,
    }


def _receipt_state(receipt: dict[str, Any] | None, ok_status: str) -> tuple[str, str]:
    if receipt is None:
        return "UNVERIFIED", "No exact-HEAD receipt found."
    receipt_status = str(receipt.get("status"))
    evidence = f"receipt={receipt.get('created_utc')} status={receipt_status}"
    if receipt_status == ok_status:
        return "PASS", evidence
    return "FAIL", evidence


def build_release_state_report(
    cfg: Settings,
    *,
    include_github: bool = False,
    github_workflow: str = "CI",
) -> dict[str, Any]:
    audit = build_product_audit(cfg)
    package = build_package_status(cfg.project_root)
    certification = build_certification_status(
        cfg,
        include_github=include_github,
        github_workflow=github_workflow,
    )
    gates = certification.get("gates", [])
    gate_rows = [gate for gate in gates if isinstance(gate, dict)]

    git_repository = _gate_by_name(gate_rows, "git_repository")
    worktree_clean = _gate_by_name(gate_rows, "worktree_clean")
    upstream = _gate_by_name(gate_rows, "upstream")
    github_gate_names = {
        "github_branch",
        "github_branch_protection",
        "github_branch_protection_api",
        "github_ci_jobs_lookup",
        "github_hosted_runner_startup",
        "github_latest_ci",
        "github_tag_binding",
    }

    package_not_failed = package.get("status") != "PACKAGE_FAIL"
    head_sha = certification.get("git", {}).get("head_sha") if isinstance(certification.get("git"), dict) else None
    local_release_receipt = load_release_receipt(cfg.project_root, receipt_type="local_release", head_sha=head_sha)
    fresh_clone_receipt = load_release_receipt(cfg.project_root, receipt_type="fresh_clone", head_sha=head_sha)
    source_artifact_receipt = load_release_receipt(cfg.project_root, receipt_type="source_artifact", head_sha=head_sha)
    python_dist_receipt = load_release_receipt(cfg.project_root, receipt_type="python_dist", head_sha=head_sha)
    operator_workflow_receipt = load_release_receipt(cfg.project_root, receipt_type="operator_workflow", head_sha=head_sha)
    answer_trace_quality_receipt = load_release_receipt(
        cfg.project_root,
        receipt_type="answer_trace_quality",
        head_sha=head_sha,
    )
    retrieval_quality_receipt = load_release_receipt(
        cfg.project_root,
        receipt_type="retrieval_quality",
        head_sha=head_sha,
    )
    gis_quality_receipt = load_release_receipt(
        cfg.project_root,
        receipt_type="gis_quality",
        head_sha=head_sha,
    )
    local_release_status, local_release_evidence = _receipt_state(local_release_receipt, "CERTIFY_RELEASE_OK")
    fresh_clone_status, fresh_clone_evidence = _receipt_state(fresh_clone_receipt, "FRESH_CLONE_CERT_OK")
    source_artifact_status, source_artifact_evidence = _receipt_state(source_artifact_receipt, "SOURCE_ARTIFACT_CERT_OK")
    python_dist_status, python_dist_evidence = _receipt_state(python_dist_receipt, "PYTHON_DIST_CERT_OK")
    operator_workflow_status, operator_workflow_evidence = _receipt_state(
        operator_workflow_receipt,
        "OPERATOR_WORKFLOW_CERT_OK",
    )
    answer_trace_quality_status, answer_trace_quality_evidence = _receipt_state(
        answer_trace_quality_receipt,
        "ANSWER_TRACE_QUALITY_CERT_OK",
    )
    retrieval_quality_status, retrieval_quality_evidence = _receipt_state(
        retrieval_quality_receipt,
        "RETRIEVAL_QUALITY_CERT_OK",
    )
    gis_quality_status, gis_quality_evidence = _receipt_state(
        gis_quality_receipt,
        "GIS_QUALITY_CERT_OK",
    )
    canonical_local_pass = (
        git_repository is not None
        and git_repository.get("status") == "PASS"
        and worktree_clean is not None
        and worktree_clean.get("status") == "PASS"
        and upstream is not None
        and upstream.get("status") == "PASS"
        and package_not_failed
    )
    source_artifact_pass = _requirement_status(audit, "source_artifact_packaging") == "PASS"
    source_artifact_certified = source_artifact_status == "PASS"
    github_requirement_open = _requirement_status(audit, "github_release_governance") == "OPEN"
    github_gate_blockers = [gate for gate in gate_rows if gate.get("name") in github_gate_names and gate.get("status") != "PASS"]
    github_open = include_github and (github_requirement_open or bool(github_gate_blockers))
    source_artifact_open = include_github and not source_artifact_certified
    answer_trace_quality_certified = answer_trace_quality_status == "PASS"
    answer_trace_quality_open = include_github and not answer_trace_quality_certified
    retrieval_quality_certified = retrieval_quality_status == "PASS"
    retrieval_quality_open = include_github and not retrieval_quality_certified
    gis_quality_certified = gis_quality_status == "PASS"
    gis_quality_open = include_github and not gis_quality_certified
    operator_workflow_certified = operator_workflow_status == "PASS"
    operator_workflow_open = include_github and not operator_workflow_certified
    governed_ready = (
        include_github
        and certification.get("status") == "CERTIFICATION_READY"
        and source_artifact_certified
        and answer_trace_quality_certified
        and retrieval_quality_certified
        and gis_quality_certified
        and operator_workflow_certified
    )

    highest_local_state = "CANONICAL_LOCAL" if canonical_local_pass else "PROTOTYPE"
    if governed_ready:
        current_state = "GOVERNED_RELEASE_READY"
    elif github_open:
        current_state = "GITHUB_GOVERNANCE_OPEN"
    elif source_artifact_open:
        current_state = "SOURCE_ARTIFACT_CERTIFICATION_OPEN"
    elif answer_trace_quality_open:
        current_state = "ANSWER_TRACE_QUALITY_CERTIFICATION_OPEN"
    elif retrieval_quality_open:
        current_state = "RETRIEVAL_QUALITY_CERTIFICATION_OPEN"
    elif gis_quality_open:
        current_state = "GIS_QUALITY_CERTIFICATION_OPEN"
    elif operator_workflow_open:
        current_state = "OPERATOR_WORKFLOW_CERTIFICATION_OPEN"
    else:
        current_state = highest_local_state

    states = [
        _release_state(
            "CANONICAL_LOCAL",
            "PASS" if canonical_local_pass else "OPEN",
            (
                "git repository, clean worktree, upstream, and package inputs are present"
                if canonical_local_pass
                else "one or more canonical local gates are missing or failing"
            ),
            "Repair local Git/package gates before release certification." if not canonical_local_pass else "Keep local canonical state clean.",
        ),
        _release_state(
            "LOCAL_RELEASE_CERTIFIED",
            local_release_status,
            local_release_evidence,
            "Run python3 scripts/certify_release.py on the exact current HEAD.",
        ),
        _release_state(
            "FRESH_CLONE_CERTIFIED",
            fresh_clone_status,
            fresh_clone_evidence,
            f"Run python3 scripts/certify_fresh_clone.py --ref {head_sha or 'main'}.",
        ),
        _release_state(
            "SOURCE_ARTIFACT_PACKAGING",
            "PASS" if source_artifact_pass and package_not_failed else "OPEN",
            str(package.get("status")),
            "Run mucaro package build --format text from a clean worktree.",
        ),
        _release_state(
            "SOURCE_ARTIFACT_CERTIFIED",
            source_artifact_status,
            source_artifact_evidence,
            "Run python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json.",
        ),
        _release_state(
            "PYTHON_DIST_CERTIFIED",
            python_dist_status,
            python_dist_evidence,
            "Run python3 scripts/certify_python_distribution.py.",
        ),
        _release_state(
            "ANSWER_TRACE_QUALITY_CERTIFIED",
            answer_trace_quality_status,
            answer_trace_quality_evidence,
            "Run python3 scripts/certify_answer_trace_quality.py.",
        ),
        _release_state(
            "RETRIEVAL_QUALITY_CERTIFIED",
            retrieval_quality_status,
            retrieval_quality_evidence,
            "Run python3 scripts/certify_retrieval_quality.py.",
        ),
        _release_state(
            "GIS_QUALITY_CERTIFIED",
            gis_quality_status,
            gis_quality_evidence,
            "Run python3 scripts/certify_gis_quality.py.",
        ),
        _release_state(
            "OPERATOR_WORKFLOW_CERTIFIED",
            operator_workflow_status,
            operator_workflow_evidence,
            "Run python3 scripts/certify_operator_workflow.py.",
        ),
        _release_state(
            "GITHUB_GOVERNANCE_OPEN",
            "OPEN" if github_open else "UNVERIFIED" if not include_github else "PASS",
            (
                "; ".join(f"{gate.get('name')}={gate.get('status')}:{gate.get('detail')}" for gate in github_gate_blockers)
                if github_gate_blockers
                else "GitHub checks were not requested." if not include_github else "GitHub governance gates passed."
            ),
            "Run mucaro release state --include-github after hosted CI, branch protection, and tag binding are ready.",
        ),
        _release_state(
            "GOVERNED_RELEASE_READY",
            "PASS" if governed_ready else "OPEN",
            str(certification.get("status")),
            "Do not tag until mucaro certify status --include-github reports CERTIFICATION_READY.",
        ),
        _release_state(
            "PRODUCTION_READY",
            "OPEN",
            "Production readiness also needs additional provider validation, larger-corpus retrieval evidence, packaging distribution, and sustained user workflow validation.",
            "Continue development beyond the governed local-first release baseline.",
        ),
    ]

    next_commands = [
        "python3 scripts/certify_release.py",
        f"python3 scripts/certify_fresh_clone.py --ref {certification.get('git', {}).get('head_sha') or 'main'}",
        "python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json",
        "python3 scripts/certify_python_distribution.py",
        "python3 scripts/certify_answer_trace_quality.py",
        "python3 scripts/certify_retrieval_quality.py",
        "python3 scripts/certify_gis_quality.py",
        "python3 scripts/certify_operator_workflow.py",
    ]
    if not include_github:
        next_commands.insert(0, "mucaro release state --include-github --format text")
    if github_open:
        next_commands.extend(
            [
                "mucaro certify github-actions --format text",
                "gh auth refresh -h github.com -s user",
            ]
        )

    return {
        "status": "RELEASE_STATE_READY" if governed_ready else "RELEASE_STATE_OPEN",
        "current_state": current_state,
        "highest_local_state": highest_local_state,
        "include_github": include_github,
        "version": package_version(),
        "project_root": str(cfg.project_root),
        "git": certification.get("git"),
        "receipts": {
            "local_release": local_release_receipt,
            "fresh_clone": fresh_clone_receipt,
            "source_artifact": source_artifact_receipt,
            "python_dist": python_dist_receipt,
            "operator_workflow": operator_workflow_receipt,
            "answer_trace_quality": answer_trace_quality_receipt,
            "retrieval_quality": retrieval_quality_receipt,
            "gis_quality": gis_quality_receipt,
        },
        "product_audit_status": audit.get("status"),
        "package_status": package.get("status"),
        "certification_status": certification.get("status"),
        "states": states,
        "next_commands": sorted(set(next_commands)),
    }


def run_certify_status(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_certification_status(
        cfg,
        include_github=args.include_github,
        github_workflow=args.github_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "CERTIFICATION_FAIL":
        return 2
    if args.strict and report["status"] != "CERTIFICATION_READY":
        return 2
    return 0


def run_certify_github_actions(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_github_actions_diagnostic_status(
        cfg,
        workflow_name=args.workflow,
        probe_workflow_name=args.probe_workflow,
    )
    emit_payload(report, args.format)
    if report["status"] == "GITHUB_ACTIONS_DIAGNOSTIC_FAIL":
        return 2
    if args.strict and report["status"] != "GITHUB_ACTIONS_DIAGNOSTIC_OK":
        return 2
    return 0


def run_certify_retrieval_quality(args: argparse.Namespace, cfg: Settings) -> int:
    command = ["mucaro", "certify", "retrieval-quality"]
    if args.profile != RETRIEVAL_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.top_k != 5:
        command.extend(["--top-k", str(args.top_k)])
    report = build_retrieval_quality_certification(
        cfg.project_root,
        profile=args.profile,
        top_k=args.top_k,
        command=command,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == RETRIEVAL_QUALITY_OK else 2


def run_eval_answer_trace(args: argparse.Namespace, cfg: Settings) -> int:
    report = build_answer_trace_eval_report(
        fixture_path=args.fixture,
        min_case_pass_rate=args.min_case_pass_rate,
        require_negative_cases=not args.allow_no_negative_cases,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == ANSWER_TRACE_EVAL_OK else 2


def run_eval_gis(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    report = build_gis_eval_report(fixture_path=args.fixture)
    emit_payload(report, args.format)
    return 0 if report["status"] == GIS_EVAL_OK else 2


def run_certify_gis_quality(args: argparse.Namespace, cfg: Settings) -> int:
    command = ["mucaro", "certify", "gis-quality"]
    if args.profile != GIS_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.fixture != DEFAULT_GIS_EVAL_FIXTURE:
        command.extend(["--fixture", str(args.fixture)])
    report = build_gis_quality_certification(
        cfg.project_root,
        profile=args.profile,
        fixture_path=args.fixture,
        command=command,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == GIS_QUALITY_OK else 2


def run_certify_answer_trace_quality(args: argparse.Namespace, cfg: Settings) -> int:
    command = ["mucaro", "certify", "answer-trace-quality"]
    if args.profile != ANSWER_TRACE_QUALITY_PROFILE:
        command.extend(["--profile", args.profile])
    if args.fixture != DEFAULT_ANSWER_TRACE_FIXTURE:
        command.extend(["--fixture", str(args.fixture)])
    if args.min_case_pass_rate != 1.0:
        command.extend(["--min-case-pass-rate", str(args.min_case_pass_rate)])
    if args.allow_no_negative_cases:
        command.append("--allow-no-negative-cases")
    report = build_answer_trace_quality_certification(
        cfg.project_root,
        profile=args.profile,
        fixture_path=args.fixture,
        min_case_pass_rate=args.min_case_pass_rate,
        require_negative_cases=not args.allow_no_negative_cases,
        command=command,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == ANSWER_TRACE_QUALITY_OK else 2


def run_certify_operator_workflow(args: argparse.Namespace, cfg: Settings) -> int:
    command = ["mucaro", "certify", "operator-workflow"]
    if args.profile != OPERATOR_WORKFLOW_PROFILE:
        command.extend(["--profile", args.profile])
    report = build_operator_workflow_certification(
        cfg.project_root,
        profile=args.profile,
        command=command,
    )
    emit_payload(report, args.format)
    return 0 if report["status"] == OPERATOR_WORKFLOW_OK else 2


def run_sources_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "SOURCES_OK", "sources": get_sources(cfg), "index": get_index_status(cfg)}
    emit_payload(result, args.format)
    return 0


def run_sources_reindex(args: argparse.Namespace, cfg: Settings) -> int:
    result = reindex_sources(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "REINDEX_OK" else 2


def run_sources_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_source(args.source_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "SOURCE_DELETE_OK" else 2


def run_conversations_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "CONVERSATIONS_OK", "conversations": list_conversations(cfg)}
    emit_payload(result, args.format)
    return 0


def run_conversations_create(args: argparse.Namespace, cfg: Settings) -> int:
    result = create_conversation(args.title, cfg)
    emit_payload(result, args.format)
    return 0


def run_conversations_show(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    emit_payload(conversation, args.format)
    return 0


def run_conversations_rename(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = rename_conversation(args.conversation_id, " ".join(args.title), cfg)
    except KeyError:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    except ValueError as exc:
        raise MucaroCLIError(str(exc), "Conversation title is required.") from exc
    emit_payload(result, args.format)
    return 0


def run_conversations_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_conversation(args.conversation_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "CONVERSATION_DELETE_OK" else 2


def run_conversations_organize(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = update_conversation(args.conversation_id, {args.organization_field: args.organization_value}, cfg)
    except KeyError:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_conversations_ask(args: argparse.Namespace, cfg: Settings) -> int:
    conversation = get_conversation(args.conversation_id, cfg)
    if conversation is None:
        emit_payload({"status": "ERROR_CONVERSATION_NOT_FOUND", "conversation_id": args.conversation_id}, args.format)
        return 2
    answer = answer_question(
        " ".join(args.query),
        top_k=args.top_k,
        retrieval_strategy=args.strategy,
        model_provider=args.provider,
        model_name=args.model,
        allow_mock_fallback=args.allow_mock_fallback,
        allow_live=args.allow_live,
        require_offline=args.require_offline,
        conversation_context=build_conversation_context(conversation),
        cfg=cfg,
    )
    updated = append_answer_turn(args.conversation_id, " ".join(args.query), answer, cfg)
    emit_payload({"status": "CONVERSATION_ASK_OK", "answer": answer, "conversation": updated}, args.format)
    if answer.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if answer.get("citation_status") == "CITATION_OK" else 3


def run_runs_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "RUNS_OK", "runs": list_payloads("runs", cfg.sqlite_path)}
    emit_payload(result, args.format)
    return 0


def run_runs_summary(args: argparse.Namespace, cfg: Settings) -> int:
    rows = list_payloads("runs", cfg.sqlite_path)
    citation_ok = sum(1 for run in rows if run.get("citation_status") == "CITATION_OK")
    by_strategy: dict[str, int] = {}
    for run in rows:
        strategy = str(run.get("retrieval_strategy") or "unknown")
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
    result = {
        "status": "RUN_HISTORY_OK",
        "run_count": len(rows),
        "citation_ok": citation_ok,
        "citation_error": len(rows) - citation_ok,
        "by_retrieval_strategy": by_strategy,
        "latest_run_id": rows[-1].get("run_id") if rows else None,
    }
    emit_payload(result, args.format)
    return 0


def run_runs_show(args: argparse.Namespace, cfg: Settings) -> int:
    for run in list_payloads("runs", cfg.sqlite_path):
        if run.get("run_id") == args.run_id:
            emit_payload(run, args.format)
            return 0
    emit_payload({"status": "ERROR_RUN_NOT_FOUND", "run_id": args.run_id}, args.format)
    return 2


def run_tools_list(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    emit_payload(list_tools(), args.format)
    return 0


def run_tools_requests(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_tool_calls(cfg), args.format)
    return 0


def run_tools_request(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = request_tool_call(
            args.tool_name,
            args.arguments,
            cfg=cfg,
            requested_by="cli",
            reason=args.reason,
        )
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_approve(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = approve_tool_call(
            args.tool_call_id,
            cfg=cfg,
            approved_by="cli",
            reason=args.reason,
            expected_arguments_sha256=args.arguments_sha256,
        )
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_deny(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = deny_tool_call(
            args.tool_call_id,
            cfg=cfg,
            denied_by="cli",
            reason=args.reason,
            expected_arguments_sha256=args.arguments_sha256,
        )
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_run(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = run_tool_call(args.tool_call_id, cfg=cfg, executed_by="cli")
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_cancel(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = cancel_tool_call(args.tool_call_id, cfg=cfg, cancelled_by="cli", reason=args.reason)
    except ToolRuntimeError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_tools_verify_audit(args: argparse.Namespace, cfg: Settings) -> int:
    result = verify_tool_audit_chain(cfg)
    emit_payload(result, args.format)
    return 0 if result["status"] == "TOOL_AUDIT_CHAIN_OK" else 1


def run_offline_manifest(args: argparse.Namespace, cfg: Settings) -> int:
    payload = build_offline_manifest(system_offline=args.assume_offline)
    if args.format == "text":
        print(payload["status"])
        print(" ".join(f"{key}={value}" for key, value in payload["counts"].items()))
        for row in payload["capabilities"]:
            print(f"{row['state_now']} {row['capability']}")
        return 0
    emit_payload(payload, args.format)
    return 0


def run_contracts_list(args: argparse.Namespace, cfg: Settings) -> int:
    payload = list_contracts(cfg.project_root if (cfg.project_root / "schemas").is_dir() else None)
    if args.format == "text":
        print(payload["status"])
        for family in payload["families"]:
            kinds = ",".join(row["kind"] for row in family["record_kinds"])
            print(f"{family['family']} {kinds}")
        return 0
    emit_payload(payload, args.format)
    return 0


def run_contracts_validate(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        record = json.loads(Path(args.path).read_text(encoding="utf-8"))
        errors = contract_errors(args.kind, record)
    except KeyError as exc:
        raise MucaroCLIError("ERROR_CONTRACT_KIND_UNKNOWN", args.kind) from exc
    except json.JSONDecodeError as exc:
        raise MucaroCLIError("ERROR_CONTRACT_JSON_INVALID", str(exc)) from exc
    payload = {"status": "CONTRACT_VALID" if not errors else "ERROR_CONTRACT_VIOLATION", "kind": args.kind, "errors": errors}
    emit_payload(payload, args.format)
    return 0 if not errors else 1


def run_tools_audit(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_tool_audit_events(cfg), args.format)
    return 0


def run_capability_contracts(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    report = build_capability_contract_report()
    emit_payload(report, args.format)
    return 0 if report.get("status") == "ADVANCED_CAPABILITY_CONTRACTS_READY" else 2


def run_capability_validate_gis_report(args: argparse.Namespace, cfg: Settings) -> int:
    path = args.report_path
    if not path.is_absolute():
        path = cfg.project_root / path
    report = validate_gis_report_file(path)
    emit_payload(report, args.format)
    return 0 if report.get("status") == "CAPABILITY_OUTPUT_CONTRACT_OK" else 2


def run_capability_validate_ocr_report(args: argparse.Namespace, cfg: Settings) -> int:
    path = args.report_path
    if not path.is_absolute():
        path = cfg.project_root / path
    report = validate_ocr_report_file(path)
    emit_payload(report, args.format)
    return 0 if report.get("status") == "CAPABILITY_OUTPUT_CONTRACT_OK" else 2


def run_capability_validate_retrieval_quality(args: argparse.Namespace, cfg: Settings) -> int:
    path = args.report_path
    if not path.is_absolute():
        path = cfg.project_root / path
    report = validate_retrieval_quality_file(path)
    emit_payload(report, args.format)
    return 0 if report.get("status") == "CAPABILITY_OUTPUT_CONTRACT_OK" else 2


def run_capability_validate_web_snapshot(args: argparse.Namespace, cfg: Settings) -> int:
    path = args.snapshot_path
    if not path.is_absolute():
        path = cfg.project_root / path
    report = validate_web_snapshot_file(path)
    emit_payload(report, args.format)
    return 0 if report.get("status") == "CAPABILITY_OUTPUT_CONTRACT_OK" else 2


def run_memory_list(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(list_memory_items(cfg, include_disabled=not args.active_only), args.format)
    return 0


def run_memory_remember(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = remember_memory_item(" ".join(args.content), cfg, kind=args.kind, source="cli")
    except MemoryError as exc:
        emit_error(exc.status, exc.detail, args.format)
        return 2
    emit_payload(result, args.format)
    return 0


def run_memory_enable(args: argparse.Namespace, cfg: Settings) -> int:
    result = set_memory_enabled(args.memory_id, True, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_UPDATED" else 2


def run_memory_disable(args: argparse.Namespace, cfg: Settings) -> int:
    result = set_memory_enabled(args.memory_id, False, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_UPDATED" else 2


def run_memory_delete(args: argparse.Namespace, cfg: Settings) -> int:
    result = delete_memory_item(args.memory_id, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "MEMORY_DELETE_OK" else 2


def run_memory_export(args: argparse.Namespace, cfg: Settings) -> int:
    emit_payload(export_memory(cfg), args.format)
    return 0


def run_security_status(args: argparse.Namespace, cfg: Settings) -> int:
    result = build_security_report(cfg, bind_host=args.host)
    emit_payload(result, args.format)
    return 0 if result.get("status") != "SECURITY_FAIL" else 2


def run_index_status(args: argparse.Namespace, cfg: Settings) -> int:
    result = get_index_status(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "INDEX_OK" else 2


def prompt_for_directory_grant(root: Path, cfg: Settings) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise MucaroCLIError(
            "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
            f"Run `mucaro permissions grant {root} --yes` before ingesting this path.",
        )
    print(f"Allow Mucaro AI to access files under: {root}? [y/N] ", end="", file=sys.stderr)
    answer = sys.stdin.readline().strip().lower()
    if answer not in {"y", "yes"}:
        raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(root))
    try:
        return grant_directory(root, cfg, granted_by="cli_prompt", reason="ingest --grant-directory-access")
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc


def run_plan(args: argparse.Namespace, cfg: Settings) -> int:
    del cfg
    result = analyze_task(
        " ".join(args.request),
        inputs_available=args.input,
        approval_state=args.approval_state,
    )
    emit_payload(result, args.format)
    return 0


def run_validate(args: argparse.Namespace, cfg: Settings) -> int:
    result = validate_project_json(cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "JSON_OK" else 2


def run_permissions_grant(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        path = Path(args.path).expanduser().resolve()
        if not args.yes:
            if not sys.stdin.isatty():
                raise MucaroCLIError(
                    "ERROR_PERMISSION_CONFIRMATION_REQUIRED",
                    f"Run `mucaro permissions grant {path} --yes` after confirming this directory should be accessible.",
                )
            print(f"Allow Mucaro AI to access files under: {path}? [y/N] ", end="", file=sys.stderr)
            if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
                raise MucaroCLIError("ERROR_DIRECTORY_PERMISSION_DENIED", str(path))
        result = grant_directory(path, cfg, granted_by="cli", reason=args.reason)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_permissions_list(args: argparse.Namespace, cfg: Settings) -> int:
    result = {"status": "DIRECTORY_PERMISSIONS_OK", "grants": load_directory_grants(cfg)}
    emit_payload(result, args.format)
    return 0


def run_permissions_check(args: argparse.Namespace, cfg: Settings) -> int:
    result = check_directory_permission(args.path, cfg)
    emit_payload(result, args.format)
    return 0 if result.get("status") == "PERMISSION_OK" else 2


def run_permissions_revoke(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        result = revoke_directory(args.path, cfg)
    except DirectoryPermissionError as exc:
        raise MucaroCLIError(exc.status, exc.detail) from exc
    emit_payload(result, args.format)
    return 0


def run_serve(args: argparse.Namespace, cfg: Settings) -> int:
    import uvicorn

    try:
        enforce_startup_security(cfg, bind_host=args.host)
    except SecurityProfileError as exc:
        emit_error(exc.status, exc.detail)
        return 2
    uvicorn.run("backend.app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: Sequence[str] | None = None, cfg: Settings = settings) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, cfg)
    except MucaroCLIError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return exc.exit_code
    except ModelProviderError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return 2
    except DirectoryPermissionError as exc:
        emit_error(exc.status, exc.detail, getattr(args, "format", "json"))
        return 2
    except (ParseError, IngestLimitError) as exc:
        emit_error(exc.status, str(exc), getattr(args, "format", "json"))
        return 2
    except ValueError as exc:
        emit_error("ERROR_INVALID_INPUT", str(exc), getattr(args, "format", "json"))
        return 2
    except OSError as exc:
        emit_error("ERROR_FILESYSTEM", str(exc), getattr(args, "format", "json"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
