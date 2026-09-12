from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CAPABILITY_CONTRACT_SCHEMA_VERSION = "mucaro_advanced_capability_contracts_v1"
CAPABILITY_OUTPUT_CONTRACT_OK = "CAPABILITY_OUTPUT_CONTRACT_OK"
CAPABILITY_OUTPUT_CONTRACT_FAILED = "CAPABILITY_OUTPUT_CONTRACT_FAILED"
GIS_REPORT_SCHEMA_VERSION = "mucaro_gis_report_v1"


@dataclass(frozen=True)
class CapabilityContract:
    capability_id: str
    title: str
    implementation_status: str
    contract_status: str
    purpose: str
    permission_gates: tuple[str, ...]
    provenance_fields: tuple[str, ...]
    invariants: tuple[str, ...]
    positive_regression_gates: tuple[str, ...]
    negative_regression_gates: tuple[str, ...]
    output_requirements: tuple[str, ...]
    unavailable_until: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "implementation_status": self.implementation_status,
            "contract_status": self.contract_status,
            "purpose": self.purpose,
            "permission_gates": list(self.permission_gates),
            "provenance_fields": list(self.provenance_fields),
            "invariants": list(self.invariants),
            "positive_regression_gates": list(self.positive_regression_gates),
            "negative_regression_gates": list(self.negative_regression_gates),
            "output_requirements": list(self.output_requirements),
            "unavailable_until": list(self.unavailable_until),
        }


def _contract_row_complete(contract: dict[str, Any]) -> bool:
    required_sequence_fields = (
        "permission_gates",
        "provenance_fields",
        "invariants",
        "positive_regression_gates",
        "negative_regression_gates",
        "output_requirements",
        "unavailable_until",
    )
    return all(contract.get(field) for field in required_sequence_fields)


def _contract_check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _dotted(payload: Any, dotted_path: str) -> Any:
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
            continue
        return None
    return current


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_source_identity(payload: dict[str, Any]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    source = payload.get("source")
    input_source = payload.get("input")
    inputs = payload.get("inputs")
    checks.append(
        _contract_check(
            "source_or_inputs_present",
            isinstance(source, dict) or isinstance(input_source, dict) or isinstance(inputs, dict),
            "report must identify source or input provenance",
        )
    )
    source_objects: list[tuple[str, dict[str, Any]]] = []
    if isinstance(source, dict):
        source_objects.append(("source", source))
    if isinstance(input_source, dict):
        source_objects.append(("input", input_source))
    if isinstance(inputs, dict):
        for name, value in inputs.items():
            if isinstance(value, dict):
                source_objects.append((f"inputs.{name}", value))
    for name, value in source_objects:
        checks.append(_contract_check(f"{name}.sha256_present", bool(value.get("sha256")), "source/input must record a SHA-256"))
        if "crs" in value:
            checks.append(_contract_check(f"{name}.crs_present", bool(value.get("crs")), "spatial source/input CRS must be recorded"))
        if "feature_count" in value:
            checks.append(_contract_check(f"{name}.feature_count_non_negative", isinstance(value.get("feature_count"), int) and value["feature_count"] >= 0, "feature count must be a non-negative integer"))
    return checks


def validate_gis_report_contract(report: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(report, dict), "report payload must be a JSON object"),
        _contract_check("schema_version", report.get("schema_version") == GIS_REPORT_SCHEMA_VERSION, f"schema_version must be {GIS_REPORT_SCHEMA_VERSION}"),
        _contract_check("report_id_present", bool(report.get("report_id")), "report_id is required"),
        _contract_check("status_present", isinstance(report.get("status"), str) and str(report.get("status")).startswith("GIS_"), "status must be a GIS status string"),
        _contract_check("operation_present", bool(report.get("operation")), "operation is required"),
        _contract_check("created_at_present", bool(report.get("created_at")), "created_at is required"),
        _contract_check("warnings_array", isinstance(report.get("warnings"), list), "warnings must be an array"),
        _contract_check("invariants_object", isinstance(report.get("invariants"), dict), "invariants must be an object"),
        _contract_check("limitations_non_empty", isinstance(report.get("limitations"), list) and all(isinstance(item, str) and item for item in report.get("limitations", [])) and bool(report.get("limitations")), "limitations must be non-empty strings"),
        _contract_check("falsification_criteria_non_empty", isinstance(report.get("falsification_criteria"), list) and all(isinstance(item, str) and item for item in report.get("falsification_criteria", [])) and bool(report.get("falsification_criteria")), "falsification criteria must be non-empty strings"),
    ]
    checks.extend(_validate_source_identity(report))

    status = str(report.get("status") or "")
    warnings = report.get("warnings")
    if status.endswith("_WARN"):
        checks.append(_contract_check("warn_status_has_warnings", isinstance(warnings, list) and bool(warnings), "WARN status must include warning labels"))
    if isinstance(warnings, list):
        checks.append(_contract_check("warning_labels_non_empty", all(isinstance(warning, str) and warning for warning in warnings), "warning labels must be non-empty strings"))

    invariants = report.get("invariants", {})
    if isinstance(invariants, dict):
        for key in (
            "row_accounting_closed",
            "point_row_conservation",
            "feature_key_conservation",
            "feature_row_conservation",
            "coordinate_count_conservation",
            "crs_preserved",
        ):
            if key in invariants:
                checks.append(_contract_check(f"invariants.{key}", invariants.get(key) is True, f"{key} must be true when asserted"))

    operation = str(report.get("operation") or "")
    if operation == "measure_geojson":
        checks.extend(
            [
                _contract_check("measure.parameters.adapter", _dotted(report, "parameters.adapter") == "local_planar_shoelace_v1", "measure reports must name the local planar adapter"),
                _contract_check("measure.parameters.coordinate_units", bool(_dotted(report, "parameters.coordinate_units")), "measure reports must record coordinate units"),
                _contract_check("measure.measurements_array", isinstance(report.get("measurements"), list), "measure reports must include per-feature measurements"),
                _contract_check("measure.total_area_numeric", _is_number(_dotted(report, "invariants.total_area")), "total_area must be numeric"),
                _contract_check("measure.total_length_numeric", _is_number(_dotted(report, "invariants.total_length")), "total_length must be numeric"),
                _contract_check("measure.measured_count_present", isinstance(_dotted(report, "invariants.measured_feature_count"), int), "measured_feature_count must be recorded"),
            ]
        )
    elif operation == "filter_geojson_by_bbox":
        checks.extend(
            [
                _contract_check("bbox.parameters.bbox", isinstance(_dotted(report, "parameters.bbox"), list) and len(_dotted(report, "parameters.bbox")) == 4, "bbox reports must record four query bbox values"),
                _contract_check("bbox.output_hash", bool(_dotted(report, "output.sha256")), "bbox reports must record derived output hash"),
                _contract_check("bbox.retained_count_present", isinstance(_dotted(report, "invariants.retained_feature_count"), int), "retained_feature_count must be recorded"),
            ]
        )

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": CAPABILITY_OUTPUT_CONTRACT_OK if not failed else CAPABILITY_OUTPUT_CONTRACT_FAILED,
        "schema_version": "mucaro_capability_output_contract_v1",
        "capability_id": "advanced_gis_processing",
        "report_path": report_path,
        "report_status": report.get("status"),
        "operation": report.get("operation"),
        "checks": checks,
        "counts": {
            "pass": sum(1 for check in checks if check["status"] == "PASS"),
            "fail": len(failed),
            "total": len(checks),
        },
        "failed_checks": failed,
    }


def validate_gis_report_file(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "status": CAPABILITY_OUTPUT_CONTRACT_FAILED,
            "schema_version": "mucaro_capability_output_contract_v1",
            "capability_id": "advanced_gis_processing",
            "report_path": str(path),
            "checks": [_contract_check("report_file_exists", False, f"report not found: {path}")],
            "counts": {"pass": 0, "fail": 1, "total": 1},
            "failed_checks": [_contract_check("report_file_exists", False, f"report not found: {path}")],
        }
    except json.JSONDecodeError as exc:
        return {
            "status": CAPABILITY_OUTPUT_CONTRACT_FAILED,
            "schema_version": "mucaro_capability_output_contract_v1",
            "capability_id": "advanced_gis_processing",
            "report_path": str(path),
            "checks": [_contract_check("report_json_valid", False, str(exc))],
            "counts": {"pass": 0, "fail": 1, "total": 1},
            "failed_checks": [_contract_check("report_json_valid", False, str(exc))],
        }
    if not isinstance(report, dict):
        report = {}
    return validate_gis_report_contract(report, report_path=str(path))


def list_advanced_capability_contracts() -> list[dict[str, Any]]:
    contracts = [
        CapabilityContract(
            capability_id="advanced_gis_processing",
            title="Advanced GIS processing",
            implementation_status="CANDIDATE",
            contract_status="CONTRACT_DEFINED",
            purpose="Run spatial operations without losing source identity, CRS, geometry semantics, or uncertainty.",
            permission_gates=(
                "explicit local source grant for every spatial input",
                "declared operation intent before geometry mutation",
                "deny remote map/service access unless a network policy grants it",
            ),
            provenance_fields=(
                "source_path_or_uri",
                "source_sha256",
                "retrieval_utc",
                "source_header",
                "crs",
                "source_crs",
                "target_crs",
                "crs_transform_adapter",
                "query_bbox",
                "bbox_filter_mode",
                "geometry_type",
                "feature_count",
                "retained_feature_count",
                "excluded_feature_count",
                "measured_feature_count",
                "total_area",
                "total_length",
                "area_units",
                "length_units",
                "operation_name",
                "operation_parameters",
                "feature_match_key",
                "coordinate_dimension",
                "skipped_row_count",
                "topology_state",
                "hausdorff_vertex_distance",
                "vertex_symmetric_difference_count",
            ),
            invariants=(
                "row conservation or explicit retained/excluded counts",
                "stable ID uniqueness when stable IDs are present",
                "CRS preservation or explicit reprojection record",
                "geometry validity and NULL/EMPTY classification",
                "join cardinality classification for spatial joins",
                "coordinate dimensionality, Z, and M presence are retained",
                "geometry comparisons separate exact coordinate equality from topological equality",
                "CSV-derived point features preserve raw properties and close source/output/skipped row counts",
                "bbox filtering preserves CRS and closes retained/excluded feature counts",
                "planar measurements preserve CRS, units labels, per-feature residue, and total area/length accounting",
            ),
            positive_regression_gates=(
                "point-in-polygon fixture classifies FULLY_WITHIN, TOUCH_ONLY, OUTSIDE, and NULL_EMPTY",
                "geometry comparison fixture records exact equality, topological equality, Hausdorff distance, and vertex symmetric difference",
                "reprojection fixture records source CRS, target CRS, adapter identity, output hash, and row/coordinate conservation",
                "CSV point fixture records header, source rows, output features, skipped rows, CRS, and output hash",
                "bbox filter fixture records query bbox, filter mode, retained features, excluded features, NULL/EMPTY residue, CRS, and output hash",
                "planar measurement fixture records polygon area with holes, line length, null residue, units, and aggregate totals",
                "GIS report contract validator accepts real saved measurement receipts and rejects weak provenance or false accounting",
                "GIS eval manifest runs positive and negative fixtures through the supported CLI boundary",
                "map export fixture preserves source and derived layer hashes",
            ),
            negative_regression_gates=(
                "mismatched CRS fails closed unless reprojection is explicit",
                "unsupported CRS transformation fails closed without output synthesis",
                "CSV point conversion fails closed on missing required headers, duplicate headers, malformed coordinates, and no valid output features",
                "bbox filtering fails closed on malformed query extents and unsupported modes",
                "planar measurement fails closed on missing units and records invalid or unsupported geometries as residue",
                "GIS report contract validation fails when warning status, source hashes, CRS, or asserted accounting are missing or inconsistent",
                "ambiguous nearest-neighbor match remains UNRESOLVED",
                "M:N spatial join requires explicit multiplication counts",
                "feature comparison with unmatched keys records left-only and right-only residue",
            ),
            output_requirements=(
                "FACT/INFERENCE/ASSUMPTION/UNKNOWN separation",
                "spatial state per feature or aggregate",
                "input/output hashes",
                "limitations and falsification criteria",
            ),
            unavailable_until=(
                "broader GIS engine adapter supports robust topology operations",
                "broader CRS, datum transformation, grid shift, axis-order, and vertical CRS support is explicit and receipt-backed",
                "local planar measurement is replaced or supplemented by robust geodesic and overlay checks where required",
                "map/export provenance and broader spatial eval corpus are complete",
            ),
        ),
        CapabilityContract(
            capability_id="state_of_art_ocr_analysis",
            title="State-of-the-art OCR analysis",
            implementation_status="CANDIDATE",
            contract_status="CONTRACT_DEFINED",
            purpose="Extract text and layout from images while preserving raw text, confidence, and source-image identity.",
            permission_gates=(
                "explicit local source grant for every image or PDF input",
                "operator-selected OCR mode before text extraction",
                "deny cloud OCR unless a provider and privacy policy are explicitly enabled",
            ),
            provenance_fields=(
                "source_path_or_uri",
                "source_sha256",
                "image_page_index",
                "ocr_engine",
                "ocr_engine_version",
                "language_hints",
                "layout_mode",
                "confidence_scale",
            ),
            invariants=(
                "raw OCR text is preserved separately from normalized text",
                "bounding boxes retain coordinate system and page/image dimensions",
                "low-confidence spans remain marked, not silently corrected",
                "page and block counts close against source metadata",
            ),
            positive_regression_gates=(
                "clear printed text fixture extracts expected raw strings",
                "multi-column fixture preserves reading-order evidence",
                "accented/mojibake fixture preserves RAW/NORMALIZED/CANONICAL separation",
            ),
            negative_regression_gates=(
                "blank image returns no-text state without synthesis",
                "illegible span stays low confidence",
                "language mismatch emits warning instead of fabricated certainty",
            ),
            output_requirements=(
                "raw extracted text",
                "layout evidence",
                "confidence summary",
                "source image hashes",
                "limitations and falsification criteria",
            ),
            unavailable_until=(
                "PDF/page rendering and page-level evidence are receipt-backed",
                "layout reconstruction is calibrated against representative document fixtures",
                "confidence thresholds pass a frozen OCR regression corpus",
                "optional cloud/private OCR engines have explicit privacy and provider policies",
            ),
        ),
        CapabilityContract(
            capability_id="governed_web_browsing",
            title="Governed web browsing",
            implementation_status="CANDIDATE",
            contract_status="CONTRACT_DEFINED",
            purpose="Fetch and cite web sources with frozen snapshots, URL policy, and parser evidence.",
            permission_gates=(
                "operator approval for network access",
                "URL allow/deny policy evaluation before fetch",
                "bounded redirect and content-size policy",
            ),
            provenance_fields=(
                "requested_url",
                "final_url",
                "retrieval_utc",
                "http_status",
                "content_type",
                "content_sha256",
                "parser_name",
                "parser_trace",
                "parser_warnings",
                "http_error_policy",
            ),
            invariants=(
                "mutable web content is snapshotted before use",
                "redirect chain is retained",
                "citation points to fetched content, not search-result metadata",
                "failed fetches preserve status and do not fabricate source text",
            ),
            positive_regression_gates=(
                "static HTML fixture produces cited text, byte hash, title, headings, links, and meta trace",
                "redirect fixture records requested URL, final URL, and target robots policy",
                "non-text content fixture records preview omission without fabricated text",
                "PDF fixture records parser trace page counts and page warnings without fabricated text",
                "missing cache-header fixture records immutable snapshot-only reuse policy",
                "explicitly citeable HTTP error-page fixture can be snapshotted and ingested",
                "operator-supplied multi-page session writes selection decisions and replay receipt",
            ),
            negative_regression_gates=(
                "blocked URL policy prevents fetch",
                "oversized response fails closed",
                "redirect target disallowed by robots fails before snapshot creation",
                "404/500 pages remain fetch failures unless explicitly cited as error pages",
                "unciteable HTTP error-page snapshots are rejected by source ingestion",
                "session with no selectable URL fails closed before fetching",
            ),
            output_requirements=(
                "FACT/INFERENCE separation",
                "source URLs and snapshot hashes",
                "retrieval UTC",
                "parser limitations",
                "falsification path",
            ),
            unavailable_until=(
                "automated search and discovery policy is defined",
                "rendered-browser capture policy is defined",
                "autonomous link-following remains disabled until source-selection gates exist",
            ),
        ),
        CapabilityContract(
            capability_id="coding_programming_expertise",
            title="Coding and programming expertise",
            implementation_status="ABSENT",
            contract_status="CONTRACT_DEFINED",
            purpose="Read, modify, test, and review code through explicit repository safety and evidence boundaries.",
            permission_gates=(
                "explicit workspace/repository selection",
                "read-only discovery before mutation",
                "patch/test action approval policy",
                "dirty-work preservation check before edits",
            ),
            provenance_fields=(
                "repo_root",
                "head_sha",
                "branch",
                "dirty_status",
                "files_read",
                "files_changed",
                "test_commands",
                "diff_summary",
            ),
            invariants=(
                "unrelated dirty work is preserved",
                "generated patches are reviewable as diffs",
                "tests map to changed behavior or documented risk",
                "failures and skipped checks are reported explicitly",
            ),
            positive_regression_gates=(
                "small patch fixture records changed file and passing test",
                "review fixture emits severity-ordered findings with file/line evidence",
                "dirty-work fixture avoids reverting unrelated changes",
            ),
            negative_regression_gates=(
                "destructive git command is rejected without explicit request",
                "hidden mutation outside selected workspace fails",
                "test failure cannot be summarized as certified completion",
            ),
            output_requirements=(
                "changed files",
                "test evidence",
                "residual risk",
                "assumptions",
                "next verification command",
            ),
            unavailable_until=(
                "coding action runtime exists",
                "sandbox/workspace policy exists",
                "patch and review fixtures pass",
            ),
        ),
        CapabilityContract(
            capability_id="transparency_objectivity_governance",
            title="Transparency and objectivity governance",
            implementation_status="CANDIDATE",
            contract_status="CONTRACT_DEFINED",
            purpose="Make evidence class, uncertainty, contradictions, and falsification visible across every advanced capability.",
            permission_gates=(
                "capability output must identify its evidence class",
                "uncertainty cannot be hidden by normalization or ranking",
                "contradictions require explicit classification before resolution",
            ),
            provenance_fields=(
                "claim_type",
                "evidence_type",
                "source_identity_type",
                "confidence",
                "uncertainty_state",
                "contradiction_state",
                "verification_command_or_method",
            ),
            invariants=(
                "FACT, COMPUTED, BINDING, INFERENCE, ASSUMPTION, HYPOTHESIS, and UNKNOWN remain distinct",
                "normalization is never sole identity proof",
                "search/discovery results are not exhaustive unless independently proven",
                "script success is not certification",
            ),
            positive_regression_gates=(
                "conflicting evidence fixture preserves both observations",
                "unknown evidence fixture remains UNKNOWN",
                "certification fixture requires scope, tests, hashes, and zero unresolved residue",
            ),
            negative_regression_gates=(
                "name-only identity proof is rejected",
                "unexplained arithmetic mismatch fails closed",
                "search absence cannot prove source absence",
            ),
            output_requirements=(
                "evidence taxonomy",
                "confidence and limitations",
                "contradiction handling",
                "unresolved residue",
                "falsification criteria",
            ),
            unavailable_until=(
                "all advanced capability outputs call this contract",
                "cross-capability fixtures pass",
                "completion audit confirms no unresolved residue in scope",
            ),
        ),
    ]
    return [contract.as_dict() for contract in contracts]


def build_capability_contract_report() -> dict[str, Any]:
    contracts = list_advanced_capability_contracts()
    complete = [contract for contract in contracts if _contract_row_complete(contract)]
    implementation_states: dict[str, int] = {}
    for contract in contracts:
        state = str(contract.get("implementation_status") or "UNKNOWN")
        implementation_states[state] = implementation_states.get(state, 0) + 1
    status = "ADVANCED_CAPABILITY_CONTRACTS_READY" if len(complete) == len(contracts) else "ADVANCED_CAPABILITY_CONTRACTS_FAILED"
    return {
        "status": status,
        "schema_version": CAPABILITY_CONTRACT_SCHEMA_VERSION,
        "contract_count": len(contracts),
        "complete_contract_count": len(complete),
        "implementation_states": dict(sorted(implementation_states.items())),
        "contracts": contracts,
        "next_step": "Implement adapters and eval fixtures only after these contracts are enforced by each capability output.",
    }
