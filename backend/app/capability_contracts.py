from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlparse

CAPABILITY_CONTRACT_SCHEMA_VERSION = "mucaro_advanced_capability_contracts_v1"
CAPABILITY_OUTPUT_CONTRACT_OK = "CAPABILITY_OUTPUT_CONTRACT_OK"
CAPABILITY_OUTPUT_CONTRACT_FAILED = "CAPABILITY_OUTPUT_CONTRACT_FAILED"
GIS_REPORT_SCHEMA_VERSION = "mucaro_gis_report_v1"
OCR_REPORT_SCHEMA_VERSION = "mucaro_ocr_report_v1"
WEB_SNAPSHOT_SCHEMA_VERSION = "mucaro_web_snapshot_v1"
WEB_BROWSE_SESSION_SCHEMA_VERSION = "mucaro_web_browse_session_v1"
CODING_REVIEW_SCHEMA_VERSION = "mucaro-coding-review/1.0"
EVIDENCE_CONTRACT_SCHEMA_VERSION = "mucaro_evidence_contract_v1"
CRS_ENGINE_ADAPTERS = {
    "pyproj_transformer_group_always_xy_v1",
    "pyproj_governed_local_grid_always_xy_v1",
}
GOVERNED_CRS_GRID_MANIFEST_SHA256 = "3d49d0bb364be9f41879ef3e74aa6766131ccb13ea6aef8bc11631f35d0fa1bc"
GOVERNED_CRS_GRID_SHA256 = "a6db010f956526a011ba719c45fe32e5aff987eeb1cb03f2b8338208a77c619d"
GOVERNED_CRS_GRID_BYTES = 3644
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_CLAIM_TYPES = {
    "FACT",
    "COMPUTED",
    "BINDING",
    "INFERENCE",
    "ASSUMPTION",
    "HYPOTHESIS",
    "UNKNOWN",
}


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


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item for item in value)


def _validate_source_identity(payload: dict[str, Any]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    source = payload.get("source")
    input_source = payload.get("input")
    inputs = payload.get("inputs")
    vector_source = payload.get("vector_source")
    raster_source = payload.get("raster_source")
    layers = payload.get("layers")
    checks.append(
        _contract_check(
            "source_or_inputs_present",
            isinstance(source, dict)
            or isinstance(input_source, dict)
            or isinstance(inputs, dict)
            or (isinstance(vector_source, dict) and isinstance(raster_source, dict))
            or isinstance(layers, list),
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
    if isinstance(vector_source, dict):
        source_objects.append(("vector_source", vector_source))
    if isinstance(raster_source, dict):
        source_objects.append(("raster_source", raster_source))
    for name, value in source_objects:
        checks.append(_contract_check(f"{name}.sha256_present", bool(value.get("sha256")), "source/input must record a SHA-256"))
        if "crs" in value:
            checks.append(_contract_check(f"{name}.crs_present", bool(value.get("crs")), "spatial source/input CRS must be recorded"))
        if "feature_count" in value:
            checks.append(_contract_check(f"{name}.feature_count_non_negative", isinstance(value.get("feature_count"), int) and value["feature_count"] >= 0, "feature count must be a non-negative integer"))
    if isinstance(layers, list):
        for index, layer in enumerate(layers):
            checks.extend(
                [
                    _contract_check(f"layers.{index}.object", isinstance(layer, dict), "bound layers must be objects"),
                    _contract_check(f"layers.{index}.sha256_present", isinstance(layer, dict) and bool(layer.get("sha256")), "bound layers must record a SHA-256"),
                    _contract_check(f"layers.{index}.declared_crs_present", isinstance(layer, dict) and bool(layer.get("declared_crs")), "bound layers must record declared CRS"),
                ]
            )
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
        _contract_check("limitations_non_empty", _non_empty_string_list(report.get("limitations")), "limitations must be non-empty strings"),
        _contract_check("falsification_criteria_non_empty", _non_empty_string_list(report.get("falsification_criteria")), "falsification criteria must be non-empty strings"),
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
            "source_crs_parseable",
            "target_crs_parseable",
            "axis_order_explicit",
            "transform_pipeline_frozen",
            "output_accounting_closed",
            "repair_is_explicit",
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
            "all_layer_hashes_present",
            "output_hash_present",
            "renderer_identity_present",
            "dimensions_exact",
            "bounds_preserved",
            "band_count_preserved",
            "nodata_definition_preserved",
            "nodata_accounting_recorded",
            "transactional_output_published",
            "asset_accounting_closed",
            "asset_type_accounting_closed",
            "all_asset_hashes_matched",
            "all_feature_counts_matched",
            "all_stable_ids_present_and_unique",
            "all_geometries_valid",
            "all_coordinate_dimensions_accounted",
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
            "slope_cell_accounting_closed",
            "aspect_cell_accounting_closed",
            "neighborhood_accounting_closed",
            "flat_aspect_accounting_closed",
            "state_accounting_closed",
            "flow_edge_accounting_closed",
            "terminal_state_accounting_closed",
            "direction_state_accounting_closed",
            "terminal_contribution_accounting_closed",
            "terminal_area_accounting_closed",
            "strict_descent_edges",
            "accumulation_range_bounded",
            "contributing_area_matches_cell_area",
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
            "provenance_complete",
            "governed_grid_binding_satisfied",
        ):
            if key in invariants:
                checks.append(_contract_check(f"invariants.{key}", invariants.get(key) is True, f"{key} must be true when asserted"))

    operation = str(report.get("operation") or "")
    if operation == "inspect_geojson":
        features = report.get("features")
        feature_count = _dotted(report, "invariants.feature_count")
        topology_valid_count = _dotted(report, "invariants.topology_valid_count")
        topology_invalid_count = _dotted(report, "invariants.topology_invalid_count")
        topology_unresolved_count = _dotted(report, "invariants.topology_unresolved_count")
        topology_not_applicable_count = _dotted(report, "invariants.topology_not_applicable_count")
        topology_counts_present = all(
            isinstance(value, int)
            for value in (
                topology_valid_count,
                topology_invalid_count,
                topology_unresolved_count,
                _dotted(report, "invariants.topology_planar_xy_only_count"),
                topology_not_applicable_count,
            )
        )
        checks.extend(
            [
                _contract_check("inspect.geometry_counts_object", isinstance(report.get("geometry_counts"), dict), "inspect reports must record geometry counts"),
                _contract_check("inspect.features_array", isinstance(features, list), "inspect reports must include per-feature inspection receipts"),
                _contract_check("inspect.feature_count_present", isinstance(feature_count, int), "feature_count must be recorded"),
                _contract_check("inspect.topology_counts_present", topology_counts_present, "topology state counts must be recorded"),
                _contract_check(
                    "inspect.source_feature_count_matches",
                    isinstance(feature_count, int) and _dotted(report, "source.feature_count") == feature_count,
                    "source feature_count must match invariant feature_count",
                ),
            ]
        )
        if isinstance(features, list):
            checks.append(
                _contract_check(
                    "inspect.feature_count_matches_receipts",
                    isinstance(feature_count, int) and feature_count == len(features),
                    "feature_count must equal per-feature inspection receipts",
                )
            )
            if topology_counts_present and isinstance(feature_count, int):
                checks.append(
                    _contract_check(
                        "inspect.topology_accounting_closed",
                        topology_valid_count + topology_invalid_count + topology_unresolved_count + topology_not_applicable_count == feature_count,
                        "topology valid/invalid/unresolved/not-applicable counts must close to feature_count",
                    )
                )
            for index, feature in enumerate(features):
                if not isinstance(feature, dict):
                    checks.append(_contract_check(f"inspect.features.{index}.object", False, "inspect feature receipts must be objects"))
                    continue
                topology_state = feature.get("topology_state")
                topology_warning = feature.get("topology_warning")
                warning_required = topology_state in {"INVALID", "UNRESOLVED", "UNSUPPORTED", "VALID_PLANAR_XY"}
                checks.extend(
                    [
                        _contract_check(f"inspect.features.{index}.topology_state", isinstance(topology_state, str) and bool(topology_state), "feature topology_state must be recorded"),
                        _contract_check(f"inspect.features.{index}.topology_adapter_key", "topology_adapter" in feature, "feature topology_adapter key must be present"),
                        _contract_check(f"inspect.features.{index}.topology_warning_key", "topology_warning" in feature, "feature topology_warning key must be present"),
                        _contract_check(
                            f"inspect.features.{index}.topology_warning_consistent",
                            (isinstance(topology_warning, str) and bool(topology_warning)) if warning_required else topology_warning is None,
                            "topology warnings must be explicit only when topology residue is present",
                        ),
                    ]
                )
    elif operation == "overlay_geojson_collections":
        features = report.get("features")
        state_counts = report.get("state_counts")
        output_feature_count = _dotted(report, "invariants.output_feature_count")
        emitted_feature_count = _dotted(report, "invariants.emitted_feature_count")
        checks.extend(
            [
                _contract_check("overlay.match_on", report.get("match_on") in {"id", "index"}, "overlay reports must record id or index matching"),
                _contract_check(
                    "overlay.parameters.overlay_operation",
                    _dotted(report, "parameters.overlay_operation") in {"difference", "intersection", "symmetric_difference", "union"},
                    "overlay reports must record a supported operation",
                ),
                _contract_check("overlay.parameters.adapter", _dotted(report, "parameters.adapter") == "shapely_planar_overlay_v1", "overlay reports must name the Shapely planar overlay adapter"),
                _contract_check("overlay.parameters.overlay_adapter", _dotted(report, "parameters.overlay_adapter") == "shapely_planar_overlay_v1", "overlay reports must record the explicit overlay adapter"),
                _contract_check("overlay.output_hash", bool(_dotted(report, "output.sha256")), "overlay reports must record derived output hash"),
                _contract_check("overlay.output_feature_count_present", isinstance(output_feature_count, int), "output_feature_count must be recorded"),
                _contract_check("overlay.emitted_feature_count_present", isinstance(emitted_feature_count, int), "emitted_feature_count must be recorded"),
                _contract_check("overlay.features_array", isinstance(features, list), "overlay reports must include per-pair receipts"),
                _contract_check("overlay.state_counts_object", isinstance(state_counts, dict), "overlay reports must include state counts"),
                _contract_check("overlay.crs_match", _dotted(report, "invariants.crs_match") is True, "overlay reports must prove matching CRS"),
                _contract_check("overlay.feature_key_accounting_closed", _dotted(report, "invariants.feature_key_accounting_closed") is True, "feature-key accounting must close"),
                _contract_check("overlay.pair_accounting_closed", _dotted(report, "invariants.overlay_pair_accounting_closed") is True, "overlay pair accounting must close"),
                _contract_check("overlay.result_area_total_numeric", _is_number(_dotted(report, "invariants.result_area_total")), "result_area_total must be numeric"),
                _contract_check("overlay.result_length_total_numeric", _is_number(_dotted(report, "invariants.result_length_total")), "result_length_total must be numeric"),
            ]
        )
        if isinstance(features, list) and isinstance(state_counts, dict):
            shared_feature_key_count = _dotted(report, "invariants.shared_feature_key_count")
            pair_state_count = sum(
                int(state_counts.get(key, 0))
                for key in ("EMITTED", "EMPTY_INPUT", "EMPTY_RESULT", "UNRESOLVED", "UNSUPPORTED", "NON_POLYGON_RESULT")
                if isinstance(state_counts.get(key, 0), int)
            )
            checks.append(
                _contract_check(
                    "overlay.shared_count_matches_receipts",
                    isinstance(shared_feature_key_count, int) and shared_feature_key_count == len(features) == pair_state_count,
                    "shared feature count must match per-pair receipts and overlay state counts",
                )
            )
            checks.append(
                _contract_check(
                    "overlay.output_count_matches_emitted",
                    isinstance(output_feature_count, int) and isinstance(emitted_feature_count, int) and output_feature_count == emitted_feature_count == int(state_counts.get("EMITTED", -1)),
                    "output feature count must match emitted overlay count",
                )
            )
    elif operation == "compare_geojson_geometries":
        checks.extend(
            [
                _contract_check("compare.match_on", report.get("match_on") in {"id", "index"}, "compare reports must record id or index matching"),
                _contract_check("compare.parameters.planar_overlay_adapter", _dotted(report, "parameters.planar_overlay_adapter") == "shapely_planar_overlay_v1", "compare reports must name the Shapely planar overlay adapter"),
                _contract_check("compare.comparisons_array", isinstance(report.get("comparisons"), list), "compare reports must include per-feature comparisons"),
                _contract_check("compare.compared_count_present", isinstance(_dotted(report, "invariants.compared_count"), int), "compared_count must be recorded"),
                _contract_check("compare.planar_overlay_measured_count_present", isinstance(_dotted(report, "invariants.planar_overlay_measured_count"), int), "planar overlay measured count must be recorded"),
                _contract_check("compare.planar_overlay_unresolved_count_present", isinstance(_dotted(report, "invariants.planar_overlay_unresolved_count"), int), "planar overlay unresolved count must be recorded"),
                _contract_check("compare.planar_overlay_difference_count_present", isinstance(_dotted(report, "invariants.planar_overlay_difference_count"), int), "planar overlay difference count must be recorded"),
                _contract_check("compare.planar_overlay_symmetric_difference_area_total_numeric", _is_number(_dotted(report, "invariants.planar_overlay_symmetric_difference_area_total")), "planar overlay symmetric-difference area total must be numeric"),
            ]
        )
    elif operation == "measure_geojson":
        measurement_mode = _dotted(report, "parameters.measurement_mode") or "planar"
        adapter = _dotted(report, "parameters.adapter")
        checks.extend(
            [
                _contract_check("measure.parameters.measurement_mode", measurement_mode in {"planar", "geodesic"}, "measure reports must record planar or geodesic mode"),
                _contract_check("measure.parameters.adapter", adapter in {"local_planar_shoelace_v1", "local_spherical_geodesic_v1"}, "measure reports must name a supported measurement adapter"),
                _contract_check("measure.parameters.coordinate_units", bool(_dotted(report, "parameters.coordinate_units")), "measure reports must record coordinate units"),
                _contract_check("measure.measurements_array", isinstance(report.get("measurements"), list), "measure reports must include per-feature measurements"),
                _contract_check("measure.total_area_numeric", _is_number(_dotted(report, "invariants.total_area")), "total_area must be numeric"),
                _contract_check("measure.total_length_numeric", _is_number(_dotted(report, "invariants.total_length")), "total_length must be numeric"),
                _contract_check("measure.measured_count_present", isinstance(_dotted(report, "invariants.measured_feature_count"), int), "measured_feature_count must be recorded"),
            ]
        )
        if measurement_mode == "geodesic":
            source_crs = str(_dotted(report, "source.crs") or "").strip().upper()
            checks.extend(
                [
                    _contract_check("measure.geodesic.adapter", adapter == "local_spherical_geodesic_v1", "geodesic reports must name the spherical geodesic adapter"),
                    _contract_check("measure.geodesic.source_crs", source_crs == "EPSG:4326", "geodesic reports must use EPSG:4326 source coordinates"),
                    _contract_check("measure.geodesic.area_units", _dotted(report, "parameters.area_units") == "meters^2", "geodesic area units must be meters^2"),
                    _contract_check("measure.geodesic.length_units", _dotted(report, "parameters.length_units") == "meters", "geodesic length units must be meters"),
                ]
            )
    elif operation == "reproject_geojson_crs":
        features = report.get("features")
        source_count = _dotted(report, "source.feature_count")
        output_count = _dotted(report, "output.feature_count")
        candidate_inventory = _dotted(report, "parameters.candidate_inventory")
        unavailable_inventory = _dotted(report, "parameters.unavailable_operation_inventory")
        selected_index = _dotted(report, "parameters.selected_candidate_index")
        selected_candidate = (
            candidate_inventory[selected_index]
            if isinstance(candidate_inventory, list)
            and isinstance(selected_index, int)
            and 0 <= selected_index < len(candidate_inventory)
            else None
        )
        skipped_candidates = candidate_inventory[:selected_index] if isinstance(selected_candidate, dict) else []
        skipped_network_candidate = any(
            isinstance(candidate, dict) and candidate.get("requires_network_download") is True
            for candidate in skipped_candidates
        )
        checks.extend(
            [
                _contract_check("crs_engine.parameters.adapter", _dotted(report, "parameters.adapter") in CRS_ENGINE_ADAPTERS, "CRS engine reports must name a governed pyproj always-XY adapter"),
                _contract_check("crs_engine.parameters.source_crs", bool(_dotted(report, "parameters.source_crs")), "resolved source CRS is required"),
                _contract_check("crs_engine.parameters.target_crs", bool(_dotted(report, "parameters.target_crs")), "resolved target CRS is required"),
                _contract_check("crs_engine.parameters.always_xy", _dotted(report, "parameters.always_xy") is True, "CRS axis order must be explicit"),
                _contract_check("crs_engine.parameters.axis_order_policy", _dotted(report, "parameters.axis_order_policy") == "traditional_gis_xy", "traditional GIS XY policy must be recorded"),
                _contract_check("crs_engine.parameters.network_policy", _dotted(report, "parameters.network_policy") == "offline_only", "CRS network policy must fail closed to offline-only"),
                _contract_check("crs_engine.parameters.proj_network_enabled", isinstance(_dotted(report, "parameters.proj_network_enabled"), bool), "ambient PROJ network state must be recorded"),
                _contract_check("crs_engine.parameters.pyproj_version", bool(_dotted(report, "parameters.pyproj_version")), "pyproj version is required"),
                _contract_check("crs_engine.parameters.proj_version", bool(_dotted(report, "parameters.proj_version")), "PROJ version is required"),
                _contract_check("crs_engine.parameters.candidates", isinstance(_dotted(report, "parameters.available_candidate_count"), int) and _dotted(report, "parameters.available_candidate_count") >= 1, "at least one available transformation candidate is required"),
                _contract_check("crs_engine.parameters.unavailable_candidates", isinstance(_dotted(report, "parameters.unavailable_candidate_count"), int) and _dotted(report, "parameters.unavailable_candidate_count") >= 0, "unavailable transformation candidates must be counted"),
                _contract_check("crs_engine.parameters.candidate_inventory", isinstance(candidate_inventory, list) and len(candidate_inventory) == _dotted(report, "parameters.available_candidate_count"), "all available transformation candidates must be inventoried"),
                _contract_check("crs_engine.parameters.unavailable_inventory", isinstance(unavailable_inventory, list) and len(unavailable_inventory) == _dotted(report, "parameters.unavailable_candidate_count"), "all unavailable transformation operations must be inventoried"),
                _contract_check("crs_engine.parameters.selected_candidate", isinstance(selected_candidate, dict), "selected transformation candidate must resolve into the candidate inventory"),
                _contract_check("crs_engine.parameters.selected_candidate_index", isinstance(selected_candidate, dict) and selected_candidate.get("index") == selected_index, "selected candidate index must match its inventory row"),
                _contract_check("crs_engine.parameters.selected_candidate_offline", isinstance(selected_candidate, dict) and selected_candidate.get("offline_capable") is True and selected_candidate.get("requires_network_download") is False and selected_candidate.get("grid_provenance_frozen") is True, "selected transformation candidate must be offline-capable with frozen grid provenance"),
                _contract_check("crs_engine.parameters.selected_grid_count", isinstance(selected_candidate, dict) and _dotted(report, "parameters.selected_candidate_grid_count") == len(selected_candidate.get("grids", [])), "selected grid count must close to the candidate inventory"),
                _contract_check("crs_engine.parameters.best_available", isinstance(_dotted(report, "parameters.best_available"), bool), "best-operation availability must be explicit"),
                _contract_check("crs_engine.parameters.engine_warnings", isinstance(_dotted(report, "parameters.engine_warnings"), list), "captured PROJ engine warnings must be an array"),
                _contract_check("crs_engine.parameters.pipeline_definition", bool(_dotted(report, "parameters.transformer_definition")) and not str(_dotted(report, "parameters.transformer_definition")).lower().startswith("unavailable until"), "selected transformer definition must be resolved"),
                _contract_check("crs_engine.parameters.pipeline_hash", bool(_dotted(report, "parameters.transformer_definition_sha256")), "selected transformer definition hash is required"),
                _contract_check("crs_engine.best_available_warning", _dotted(report, "parameters.best_available") is True or "GIS_CRS_BEST_OPERATION_UNAVAILABLE" in (report.get("warnings") or []), "unavailable best operations must produce an explicit warning"),
                _contract_check("crs_engine.offline_fallback_warning", not isinstance(selected_index, int) or selected_index == 0 or "GIS_CRS_OFFLINE_CANDIDATE_FALLBACK" in (report.get("warnings") or []), "offline candidate fallback must produce an explicit warning"),
                _contract_check("crs_engine.network_grid_warning", not skipped_network_candidate or "GIS_CRS_NETWORK_GRID_SKIPPED" in (report.get("warnings") or []), "skipped network-grid candidates must produce an explicit warning"),
                _contract_check("crs_engine.warning_status", (_dotted(report, "parameters.best_available") is True and selected_index == 0) or report.get("status") == "GIS_CRS_REPROJECT_WARN", "fallback operations must downgrade report status"),
                _contract_check("crs_engine.source_crs_matches", _dotted(report, "source.crs") == _dotted(report, "parameters.source_crs"), "source CRS must match resolved transform source"),
                _contract_check("crs_engine.output_crs_matches", _dotted(report, "output.crs") == _dotted(report, "parameters.target_crs"), "output CRS must match transform target"),
                _contract_check("crs_engine.output_hash", bool(_dotted(report, "output.sha256")), "transformed output hash is required"),
                _contract_check("crs_engine.features_array", isinstance(features, list), "per-feature transform receipts are required"),
                _contract_check("crs_engine.feature_conservation", _dotted(report, "invariants.feature_row_conservation") is True, "feature rows must be conserved"),
                _contract_check("crs_engine.offline_policy_satisfied", _dotted(report, "invariants.offline_policy_satisfied") is True, "offline-only selection invariant must pass"),
                _contract_check("crs_engine.grid_inventory_complete", _dotted(report, "invariants.grid_inventory_complete") is True, "candidate grid metadata must be complete"),
            ]
        )
        if isinstance(candidate_inventory, list):
            candidate_grids = [
                grid
                for candidate in candidate_inventory
                if isinstance(candidate, dict) and isinstance(candidate.get("grids"), list)
                for grid in candidate["grids"]
            ]
            unavailable_grids = [
                grid
                for operation in unavailable_inventory
                if isinstance(operation, dict) and isinstance(operation.get("grids"), list)
                for grid in operation["grids"]
            ] if isinstance(unavailable_inventory, list) else []
            all_grid_rows = [*candidate_grids, *unavailable_grids]
            checks.extend(
                [
                    _contract_check("crs_engine.grid_rows", all(isinstance(grid, dict) and isinstance(grid.get("requires_network_download"), bool) and isinstance(grid.get("available_without_network"), bool) and isinstance(grid.get("provenance_frozen"), bool) for grid in all_grid_rows), "all available and unavailable grid rows must expose offline availability and provenance state"),
                    _contract_check("crs_engine.local_grid_hashes", all(not grid.get("local_file_available") or (isinstance(grid.get("local_bytes"), int) and grid.get("local_bytes") > 0 and bool(grid.get("local_sha256"))) for grid in all_grid_rows), "available local grids must be frozen by byte count and SHA-256"),
                ]
            )
        governed_grid = _dotted(report, "parameters.governed_grid")
        if _dotted(report, "parameters.adapter") == "pyproj_governed_local_grid_always_xy_v1":
            selected_grids = selected_candidate.get("grids") if isinstance(selected_candidate, dict) else None
            selected_grid = selected_grids[0] if isinstance(selected_grids, list) and selected_grids and isinstance(selected_grids[0], dict) else None
            checks.extend(
                [
                    _contract_check("crs_engine.governed_grid.object", isinstance(governed_grid, dict), "governed-grid reports must include a grid binding receipt"),
                    _contract_check("crs_engine.governed_grid.manifest_schema", isinstance(governed_grid, dict) and governed_grid.get("schema_version") == "mucaro_gis_grid_manifest_v1", "governed-grid reports must bind the supported manifest schema"),
                    _contract_check("crs_engine.governed_grid.manifest_hash", isinstance(governed_grid, dict) and governed_grid.get("sha256") == GOVERNED_CRS_GRID_MANIFEST_SHA256, "governed-grid reports must bind the source-approved manifest SHA-256"),
                    _contract_check("crs_engine.governed_grid.grid_hash", isinstance(governed_grid, dict) and isinstance(selected_grid, dict) and governed_grid.get("grid_sha256") == selected_grid.get("local_sha256"), "governed-grid reports must bind the selected grid SHA-256"),
                    _contract_check("crs_engine.governed_grid.grid_bytes", isinstance(governed_grid, dict) and isinstance(selected_grid, dict) and governed_grid.get("grid_bytes") == selected_grid.get("local_bytes"), "governed-grid reports must bind the selected grid byte count"),
                    _contract_check("crs_engine.governed_grid.crs_pair", isinstance(governed_grid, dict) and governed_grid.get("source_crs") == _dotted(report, "parameters.source_crs") and governed_grid.get("target_crs") == _dotted(report, "parameters.target_crs"), "governed-grid reports must bind the authorized CRS pair"),
                    _contract_check("crs_engine.governed_grid.network_absent", isinstance(governed_grid, dict) and governed_grid.get("network_access_performed") is False, "governed-grid execution must not perform network access"),
                    _contract_check("crs_engine.governed_grid.independent_scope", isinstance(governed_grid, dict) and governed_grid.get("independent_engine_validation") == "OPEN", "governed-grid reports must not overclaim independent-engine validation"),
                    _contract_check("crs_engine.governed_grid.invariant", _dotted(report, "invariants.governed_grid_binding_satisfied") is True, "governed-grid source and byte bindings must pass"),
                    _contract_check("crs_engine.governed_grid.execution_pipeline_hash", bool(_dotted(report, "parameters.execution_pipeline_sha256")), "governed-grid reports must hash the execution pipeline"),
                ]
            )
        if isinstance(features, list):
            checks.append(
                _contract_check(
                    "crs_engine.feature_counts_close",
                    isinstance(source_count, int) and source_count == output_count == len(features),
                    "source, output, and receipt feature counts must match",
                )
            )
    elif operation == "repair_geojson_topology":
        features = report.get("features")
        state_counts = report.get("state_counts")
        retained_states = ("UNCHANGED_VALID", "REPAIRED", "NULL_EMPTY")
        all_states = (*retained_states, "UNRESOLVED", "UNSUPPORTED")
        input_count = _dotted(report, "invariants.input_feature_count")
        retained_count = _dotted(report, "invariants.retained_feature_count")
        checks.extend(
            [
                _contract_check("topology_repair.parameters.adapter", _dotted(report, "parameters.adapter") == "shapely_make_valid_v1", "topology repair reports must name Shapely make_valid"),
                _contract_check("topology_repair.parameters.mode", _dotted(report, "parameters.mode") == "explicit_planar_xy", "topology repair must remain an explicit planar XY operation"),
                _contract_check("topology_repair.features_array", isinstance(features, list), "per-feature repair receipts are required"),
                _contract_check("topology_repair.state_counts", isinstance(state_counts, dict), "repair state counts are required"),
                _contract_check("topology_repair.output_hash", bool(_dotted(report, "output.sha256")), "repaired output hash is required"),
                _contract_check("topology_repair.crs_preserved", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "repair must preserve CRS"),
            ]
        )
        if isinstance(features, list) and isinstance(state_counts, dict):
            state_total = sum(state_counts.get(state, -1) for state in all_states)
            retained_total = sum(state_counts.get(state, -1) for state in retained_states)
            checks.extend(
                [
                    _contract_check("topology_repair.row_accounting", isinstance(input_count, int) and input_count == len(features) == state_total, "repair state and receipt rows must close to input count"),
                    _contract_check("topology_repair.output_accounting", isinstance(retained_count, int) and retained_count == retained_total == _dotted(report, "output.feature_count"), "retained repair states must close to output count"),
                    _contract_check("topology_repair.source_hashes", all(isinstance(row, dict) and bool(row.get("source_geometry_sha256")) for row in features), "every repair row must bind the source geometry"),
                    _contract_check("topology_repair.output_hashes", all(not isinstance(row, dict) or row.get("repair_state") not in retained_states or bool(row.get("output_geometry_sha256")) or row.get("repair_state") == "NULL_EMPTY" for row in features), "retained non-null geometries must bind output geometry"),
                ]
            )
    elif operation == "measure_geojson_ellipsoidal":
        measurements = report.get("measurements")
        state_counts = report.get("state_counts")
        edge_condition_counts = report.get("edge_condition_counts")
        source_count = _dotted(report, "source.feature_count")
        edge_conditions = {"ANTIMERIDIAN_CROSSING", "NEAR_NORTH_POLE", "NEAR_SOUTH_POLE", "TOUCHES_POLE"}
        checks.extend(
            [
                _contract_check("ellipsoidal.parameters.adapter", _dotted(report, "parameters.adapter") == "pyproj_geod_wgs84_v1", "ellipsoidal reports must name pyproj Geod"),
                _contract_check("ellipsoidal.parameters.ellipsoid", _dotted(report, "parameters.ellipsoid") == "WGS84", "WGS84 ellipsoid must be explicit"),
                _contract_check("ellipsoidal.parameters.area_units", _dotted(report, "parameters.area_units") == "meters^2", "ellipsoidal area units must be meters^2"),
                _contract_check("ellipsoidal.parameters.length_units", _dotted(report, "parameters.length_units") == "meters", "ellipsoidal length units must be meters"),
                _contract_check("ellipsoidal.parameters.coordinate_validation", _dotted(report, "parameters.coordinate_validation_policy") == "finite_wgs84_lonlat_bounds", "ellipsoidal coordinate validation policy must be explicit"),
                _contract_check("ellipsoidal.parameters.antimeridian_threshold", _dotted(report, "parameters.antimeridian_delta_degrees") == 180.0, "antimeridian classification threshold must be explicit"),
                _contract_check("ellipsoidal.parameters.polar_threshold", _dotted(report, "parameters.polar_latitude_degrees") == 85.0, "polar classification threshold must be explicit"),
                _contract_check("ellipsoidal.parameters.ring_area_policy", _dotted(report, "parameters.ring_area_policy") == "absolute_outer_minus_absolute_holes", "polygon ring area policy must be explicit"),
                _contract_check("ellipsoidal.source_crs", str(_dotted(report, "source.crs") or "").upper() == "EPSG:4326", "ellipsoidal inputs must be EPSG:4326"),
                _contract_check("ellipsoidal.measurements_array", isinstance(measurements, list), "per-feature ellipsoidal measurements are required"),
                _contract_check("ellipsoidal.state_counts", isinstance(state_counts, dict), "ellipsoidal state counts are required"),
                _contract_check("ellipsoidal.edge_condition_counts", isinstance(edge_condition_counts, dict) and set(edge_condition_counts) == edge_conditions and all(isinstance(count, int) and count >= 0 for count in edge_condition_counts.values()), "all supported edge-condition counts are required"),
                _contract_check("ellipsoidal.total_area", _is_number(_dotted(report, "invariants.total_area_m2")), "ellipsoidal total area must be numeric"),
                _contract_check("ellipsoidal.total_length", _is_number(_dotted(report, "invariants.total_length_m")), "ellipsoidal total length must be numeric"),
                _contract_check("ellipsoidal.edge_accounting_invariant", _dotted(report, "invariants.edge_condition_accounting_closed") is True, "edge-condition accounting invariant must pass"),
            ]
        )
        if isinstance(measurements, list) and isinstance(state_counts, dict):
            state_total = sum(state_counts.get(state, -1) for state in ("MEASURED", "NULL_EMPTY", "UNRESOLVED"))
            rows_with_edges = [row for row in measurements if isinstance(row, dict) and row.get("edge_conditions")]
            checks.extend(
                [
                    _contract_check("ellipsoidal.row_accounting", isinstance(source_count, int) and source_count == len(measurements) == state_total, "ellipsoidal state and receipt rows must close to source count"),
                    _contract_check("ellipsoidal.edge_rows", all(isinstance(row, dict) and isinstance(row.get("edge_conditions"), list) and set(row["edge_conditions"]) <= edge_conditions for row in measurements), "every ellipsoidal row must expose only supported edge conditions"),
                    _contract_check("ellipsoidal.features_with_edges", _dotted(report, "invariants.features_with_edge_conditions") == len(rows_with_edges), "features-with-edge-conditions count must close to measurement rows"),
                    _contract_check("ellipsoidal.edge_warning", not rows_with_edges or "GIS_ELLIPSOIDAL_EDGE_CONDITIONS_PRESENT" in (report.get("warnings") or []), "classified edge conditions must produce an explicit warning"),
                    _contract_check("ellipsoidal.unresolved_warning", state_counts.get("UNRESOLVED") == 0 or "GIS_ELLIPSOIDAL_UNRESOLVED" in (report.get("warnings") or []), "unresolved measurements must produce an explicit warning"),
                    _contract_check("ellipsoidal.warning_status", not rows_with_edges and state_counts.get("UNRESOLVED") == 0 or report.get("status") == "GIS_ELLIPSOIDAL_WARN", "edge conditions or unresolved rows must downgrade report status"),
                ]
            )
            if isinstance(edge_condition_counts, dict):
                for condition in sorted(edge_conditions):
                    observed_count = sum(condition in row.get("edge_conditions", []) for row in measurements if isinstance(row, dict))
                    checks.append(_contract_check(f"ellipsoidal.edge_count.{condition}", edge_condition_counts.get(condition) == observed_count, "edge-condition count must match per-feature rows"))
    elif operation == "inspect_vector_format":
        source_format = _dotted(report, "source.format")
        manifest = report.get("manifest")
        checks.extend(
            [
                _contract_check("vector_format.source_format", source_format in {"GEOJSON", "GEOPACKAGE", "SHAPEFILE"}, "vector format must be explicitly supported"),
                _contract_check("vector_format.source_bytes", isinstance(_dotted(report, "source.bytes"), int) and _dotted(report, "source.bytes") > 0, "vector source bytes must be positive"),
                _contract_check("vector_format.manifest", isinstance(manifest, dict), "vector format manifest is required"),
            ]
        )
        if source_format == "GEOJSON":
            checks.append(_contract_check("vector_format.geojson_feature_count", isinstance(_dotted(report, "manifest.feature_count"), int), "GeoJSON manifest feature count is required"))
        elif source_format == "GEOPACKAGE":
            checks.append(_contract_check("vector_format.gpkg_tables", isinstance(_dotted(report, "manifest.contents"), list) and isinstance(_dotted(report, "manifest.geometry_columns"), list), "GeoPackage contents and geometry metadata are required"))
        elif source_format == "SHAPEFILE":
            checks.append(_contract_check("vector_format.shapefile_sidecars", isinstance(_dotted(report, "manifest.sidecars"), dict), "Shapefile sidecar manifestations are required"))
    elif operation == "inspect_raster":
        bands = _dotted(report, "manifest.bands")
        band_count = _dotted(report, "manifest.band_count")
        checks.extend(
            [
                _contract_check("raster_inspect.source_bytes", isinstance(_dotted(report, "source.bytes"), int) and _dotted(report, "source.bytes") > 0, "raster source bytes must be positive"),
                _contract_check("raster_inspect.dimensions", isinstance(_dotted(report, "manifest.width"), int) and isinstance(_dotted(report, "manifest.height"), int), "raster dimensions are required"),
                _contract_check("raster_inspect.bands", isinstance(bands, list), "per-band receipts are required"),
                _contract_check("raster_inspect.band_count", isinstance(band_count, int) and isinstance(bands, list) and band_count == len(bands), "band count must match per-band receipts"),
            ]
        )
    elif operation == "sample_raster":
        samples = report.get("samples")
        state_counts = report.get("state_counts")
        checks.extend(
            [
                _contract_check("raster_sample.source_crs", bool(_dotted(report, "source.crs")), "raster sample source CRS is required"),
                _contract_check("raster_sample.samples", isinstance(samples, list) and bool(samples), "raster sample rows are required"),
                _contract_check("raster_sample.state_counts", isinstance(state_counts, dict), "raster sample state counts are required"),
            ]
        )
        if isinstance(samples, list) and isinstance(state_counts, dict):
            state_total = sum(state_counts.get(state, -1) for state in ("SAMPLED", "NODATA", "OUTSIDE"))
            checks.append(_contract_check("raster_sample.accounting", len(samples) == state_total, "raster sample state counts must close to sample rows"))
    elif operation == "clip_raster_bbox":
        checks.extend(
            [
                _contract_check("raster_clip.requested_bbox", isinstance(_dotted(report, "parameters.requested_bbox"), list) and len(_dotted(report, "parameters.requested_bbox")) == 4, "requested raster bbox is required"),
                _contract_check("raster_clip.effective_bbox", isinstance(_dotted(report, "parameters.effective_bbox"), list) and len(_dotted(report, "parameters.effective_bbox")) == 4, "effective raster bbox is required"),
                _contract_check("raster_clip.output_hash", bool(_dotted(report, "output.sha256")), "clipped raster output hash is required"),
                _contract_check("raster_clip.output_bytes", isinstance(_dotted(report, "output.bytes"), int) and _dotted(report, "output.bytes") > 0, "clipped raster output bytes must be positive"),
            ]
        )
    elif operation in {"analyze_vector_raster_intersection", "compute_zonal_statistics"}:
        features = report.get("features")
        state_counts = report.get("state_counts")
        source_count = _dotted(report, "vector_source.feature_count")
        checks.extend(
            [
                _contract_check("vector_raster.parameters.band", isinstance(_dotted(report, "parameters.band"), int) and _dotted(report, "parameters.band") >= 1, "vector-raster band must be one-based"),
                _contract_check("vector_raster.parameters.rule", _dotted(report, "parameters.cell_inclusion_rule") == "cell_center", "cell inclusion rule must be explicit"),
                _contract_check("vector_raster.crs_match", _dotted(report, "vector_source.crs") == _dotted(report, "raster_source.crs"), "vector and raster CRS must match exactly"),
                _contract_check("vector_raster.features", isinstance(features, list), "per-feature vector-raster receipts are required"),
                _contract_check("vector_raster.state_counts", isinstance(state_counts, dict), "vector-raster state counts are required"),
            ]
        )
        if isinstance(features, list) and isinstance(state_counts, dict):
            state_total = sum(state_counts.get(state, -1) for state in ("INTERSECTS_VALID", "NODATA_ONLY", "OUTSIDE_OR_NO_CELL_CENTER", "NULL_EMPTY", "UNRESOLVED"))
            checks.append(_contract_check("vector_raster.accounting", isinstance(source_count, int) and source_count == len(features) == state_total, "vector-raster state counts must close to source features"))
            checks.append(
                _contract_check(
                    "vector_raster.cell_accounting",
                    all(
                        isinstance(feature, dict)
                        and feature.get("cell_accounting_closed") is True
                        and feature.get("covered_cell_count")
                        == feature.get("valid_cell_count", -1) + feature.get("nodata_cell_count", -1)
                        and isinstance(feature.get("zero_cell_count"), int)
                        and feature.get("zero_cell_count", -1) <= feature.get("valid_cell_count", -1)
                        for feature in features
                    ),
                    "every vector-raster row must close covered = valid + NODATA and bound zero counts",
                )
            )
            if operation == "compute_zonal_statistics":
                for index, feature in enumerate(features):
                    if isinstance(feature, dict) and feature.get("state") == "INTERSECTS_VALID":
                        checks.extend(
                            [
                                _contract_check(f"zonal_stats.features.{index}.sum", _is_number(feature.get("sum")), "valid zonal rows must record a numeric sum"),
                                _contract_check(f"zonal_stats.features.{index}.median", _is_number(feature.get("median")), "valid zonal rows must record a numeric median"),
                                _contract_check(f"zonal_stats.features.{index}.population_standard_deviation", _is_number(feature.get("population_standard_deviation")), "valid zonal rows must record a numeric population standard deviation"),
                            ]
                        )
    elif operation == "resample_raster":
        checks.extend(
            [
                _contract_check("raster_resample.method", _dotted(report, "parameters.method") in {"nearest", "bilinear"}, "resampling method must be bounded"),
                _contract_check("raster_resample.requested_width", isinstance(_dotted(report, "parameters.width"), int) and _dotted(report, "parameters.width") > 0, "requested width must be positive"),
                _contract_check("raster_resample.requested_height", isinstance(_dotted(report, "parameters.height"), int) and _dotted(report, "parameters.height") > 0, "requested height must be positive"),
                _contract_check("raster_resample.output_hash", bool(_dotted(report, "output.sha256")), "resampled raster output hash is required"),
                _contract_check("raster_resample.output_width", _dotted(report, "output.width") == _dotted(report, "parameters.width"), "output width must match the explicit request"),
                _contract_check("raster_resample.output_height", _dotted(report, "output.height") == _dotted(report, "parameters.height"), "output height must match the explicit request"),
                _contract_check("raster_resample.crs", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "source and output CRS must match"),
                _contract_check("raster_resample.bounds", _dotted(report, "source.bounds") == _dotted(report, "output.bounds"), "source and output bounds must match"),
                _contract_check("raster_resample.band_count", _dotted(report, "source.band_count") == _dotted(report, "output.band_count"), "source and output band counts must match"),
                _contract_check("raster_resample.nodata", _dotted(report, "source.nodata_values") == _dotted(report, "output.nodata_values"), "source and output NODATA declarations must match"),
                _contract_check("raster_resample.source_bands", isinstance(_dotted(report, "source.bands"), list) and len(_dotted(report, "source.bands")) == _dotted(report, "source.band_count"), "source band value accounting is required"),
                _contract_check("raster_resample.output_bands", isinstance(_dotted(report, "output.bands"), list) and len(_dotted(report, "output.bands")) == _dotted(report, "output.band_count"), "output band value accounting is required"),
            ]
        )
    elif operation == "derive_raster_terrain":
        accounting = report.get("terrain_accounting")
        output_bands = _dotted(report, "output.bands")
        output_band_receipts_valid = (
            isinstance(output_bands, list)
            and len(output_bands) == 2
            and all(isinstance(row, dict) for row in output_bands)
        )
        checks.extend(
            [
                _contract_check("terrain.adapter", _dotted(report, "parameters.adapter") == "numpy_gradient_3x3_complete_neighborhood_v1", "terrain adapter identity must be frozen"),
                _contract_check("terrain.band", isinstance(_dotted(report, "parameters.band"), int) and _dotted(report, "parameters.band") >= 1, "terrain band must be one-based"),
                _contract_check("terrain.elevation_units", _dotted(report, "parameters.elevation_units") == "meters", "terrain elevation units must be explicitly meters"),
                _contract_check("terrain.horizontal_units", _dotted(report, "parameters.horizontal_units") == "meters" and _dotted(report, "parameters.horizontal_unit_factor") == 1.0, "terrain horizontal units must resolve to metres"),
                _contract_check("terrain.neighborhood_policy", _dotted(report, "parameters.neighborhood_policy") == "complete_3x3", "terrain neighborhood policy must be complete 3x3"),
                _contract_check("terrain.flat_aspect_policy", _dotted(report, "parameters.flat_aspect_policy") == "NODATA", "flat-cell aspect policy must be NODATA"),
                _contract_check("terrain.output_hash", bool(_dotted(report, "output.sha256")), "terrain output hash is required"),
                _contract_check("terrain.crs", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "terrain source and output CRS must match"),
                _contract_check("terrain.transform", _dotted(report, "source.transform") == _dotted(report, "output.transform"), "terrain source and output transforms must match"),
                _contract_check("terrain.bounds", _dotted(report, "source.bounds") == _dotted(report, "output.bounds"), "terrain source and output bounds must match"),
                _contract_check("terrain.width", _dotted(report, "source.width") == _dotted(report, "output.width"), "terrain source and output widths must match"),
                _contract_check("terrain.height", _dotted(report, "source.height") == _dotted(report, "output.height"), "terrain source and output heights must match"),
                _contract_check("terrain.output_band_count", _dotted(report, "output.band_count") == 2, "terrain output must contain slope and aspect bands"),
                _contract_check("terrain.output_dtypes", _dotted(report, "output.dtypes") == ["float32", "float32"], "terrain output bands must be float32"),
                _contract_check("terrain.output_nodata", _dotted(report, "output.nodata_values") == [-9999.0, -9999.0], "terrain output NODATA declaration must be frozen"),
                _contract_check("terrain.output_descriptions", _dotted(report, "output.descriptions") == ["slope_degrees", "aspect_degrees_clockwise_from_north"], "terrain output band descriptions must be frozen"),
                _contract_check("terrain.output_bands", output_band_receipts_valid, "terrain output band receipts are required"),
                _contract_check("terrain.accounting", isinstance(accounting, dict), "terrain accounting is required"),
            ]
        )
        if isinstance(accounting, dict) and output_band_receipts_valid:
            total = accounting.get("total_cell_count")
            neighborhood_valid = accounting.get("neighborhood_valid_cell_count")
            neighborhood_excluded = accounting.get("neighborhood_excluded_cell_count")
            flat = accounting.get("flat_cell_count")
            checks.extend(
                [
                    _contract_check("terrain.source_accounting", _is_int(total) and _is_int(accounting.get("source_valid_cell_count")) and _is_int(accounting.get("source_nodata_cell_count")) and accounting["source_valid_cell_count"] + accounting["source_nodata_cell_count"] == total, "terrain source valid and NODATA counts must close"),
                    _contract_check("terrain.neighborhood_accounting", _is_int(total) and _is_int(neighborhood_valid) and _is_int(neighborhood_excluded) and neighborhood_valid + neighborhood_excluded == total, "terrain neighborhood valid and excluded counts must close"),
                    _contract_check("terrain.slope_accounting", _is_int(total) and _is_int(neighborhood_valid) and _is_int(output_bands[0].get("valid_cell_count")) and _is_int(output_bands[0].get("nodata_cell_count")) and output_bands[0]["valid_cell_count"] == neighborhood_valid and output_bands[0]["valid_cell_count"] + output_bands[0]["nodata_cell_count"] == total, "slope accounting must close to complete neighborhoods"),
                    _contract_check("terrain.aspect_accounting", _is_int(total) and _is_int(neighborhood_valid) and _is_int(flat) and _is_int(output_bands[1].get("valid_cell_count")) and _is_int(output_bands[1].get("nodata_cell_count")) and output_bands[1]["valid_cell_count"] + flat == neighborhood_valid and output_bands[1]["valid_cell_count"] + output_bands[1]["nodata_cell_count"] == total, "aspect accounting must separate flat cells from complete neighborhoods"),
                ]
            )
    elif operation == "derive_raster_hillshade":
        accounting = report.get("hillshade_accounting")
        output_bands = _dotted(report, "output.bands")
        output_band = output_bands[0] if isinstance(output_bands, list) and len(output_bands) == 1 else None
        azimuth = _dotted(report, "parameters.azimuth_degrees_clockwise_from_north")
        altitude = _dotted(report, "parameters.altitude_degrees_above_horizon")
        azimuth_valid = (
            isinstance(azimuth, (int, float))
            and not isinstance(azimuth, bool)
            and 0.0 <= azimuth < 360.0
        )
        altitude_valid = (
            isinstance(altitude, (int, float))
            and not isinstance(altitude, bool)
            and 0.0 < altitude <= 90.0
        )
        checks.extend(
            [
                _contract_check("hillshade.adapter", _dotted(report, "parameters.adapter") == "terrain_slope_aspect_cosine_v1", "hillshade adapter identity must be frozen"),
                _contract_check("hillshade.band", isinstance(_dotted(report, "parameters.band"), int) and _dotted(report, "parameters.band") >= 1, "hillshade band must be one-based"),
                _contract_check("hillshade.elevation_units", _dotted(report, "parameters.elevation_units") == "meters", "hillshade elevation units must be explicitly meters"),
                _contract_check("hillshade.azimuth", azimuth_valid, "hillshade azimuth must be within [0,360)"),
                _contract_check("hillshade.altitude", altitude_valid, "hillshade altitude must be within (0,90]"),
                _contract_check("hillshade.model", _dotted(report, "parameters.illumination_model") == "lambertian_direct_light_clamped", "hillshade illumination model must be frozen"),
                _contract_check("hillshade.scale", _dotted(report, "parameters.output_scale") == [0.0, 255.0], "hillshade output scale must be 0..255"),
                _contract_check("hillshade.neighborhood_policy", _dotted(report, "parameters.neighborhood_policy") == "complete_3x3", "hillshade must preserve complete 3x3 terrain neighborhoods"),
                _contract_check("hillshade.terrain_report_hash", bool(_dotted(report, "terrain_lineage.report_sha256")), "hillshade terrain report hash is required"),
                _contract_check("hillshade.terrain_output_hash", bool(_dotted(report, "terrain_lineage.output_sha256")), "hillshade terrain output hash is required"),
                _contract_check("hillshade.terrain_source_identity", _dotted(report, "terrain_lineage.source_sha256") == _dotted(report, "source.sha256"), "hillshade terrain lineage must bind the original source hash"),
                _contract_check("hillshade.terrain_adapter", _dotted(report, "terrain_lineage.adapter") == "numpy_gradient_3x3_complete_neighborhood_v1", "hillshade must bind the certified terrain adapter"),
                _contract_check("hillshade.flat_policy", _dotted(report, "terrain_lineage.flat_aspect_policy") == "NODATA", "hillshade must bind the terrain flat-aspect policy"),
                _contract_check("hillshade.output_hash", bool(_dotted(report, "output.sha256")), "hillshade output hash is required"),
                _contract_check("hillshade.crs", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "hillshade source and output CRS must match"),
                _contract_check("hillshade.transform", _dotted(report, "source.transform") == _dotted(report, "output.transform"), "hillshade source and output transforms must match"),
                _contract_check("hillshade.bounds", _dotted(report, "source.bounds") == _dotted(report, "output.bounds"), "hillshade source and output bounds must match"),
                _contract_check("hillshade.width", _dotted(report, "source.width") == _dotted(report, "output.width"), "hillshade source and output widths must match"),
                _contract_check("hillshade.height", _dotted(report, "source.height") == _dotted(report, "output.height"), "hillshade source and output heights must match"),
                _contract_check("hillshade.output_band_count", _dotted(report, "output.band_count") == 1, "hillshade output must contain one band"),
                _contract_check("hillshade.output_dtypes", _dotted(report, "output.dtypes") == ["float32"], "hillshade output band must be float32"),
                _contract_check("hillshade.output_nodata", _dotted(report, "output.nodata_values") == [-9999.0], "hillshade output NODATA declaration must be frozen"),
                _contract_check("hillshade.output_descriptions", _dotted(report, "output.descriptions") == ["hillshade_0_255"], "hillshade output band description must be frozen"),
                _contract_check("hillshade.output_bands", isinstance(output_band, dict), "hillshade output band receipt is required"),
                _contract_check("hillshade.accounting", isinstance(accounting, dict), "hillshade accounting is required"),
            ]
        )
        if isinstance(accounting, dict) and isinstance(output_band, dict):
            total = accounting.get("total_cell_count")
            valid = accounting.get("valid_cell_count")
            nodata = accounting.get("nodata_cell_count")
            shadow = accounting.get("shadow_cell_count")
            illuminated = accounting.get("illuminated_cell_count")
            fully_lit = accounting.get("fully_lit_cell_count")
            minimum = output_band.get("minimum")
            maximum = output_band.get("maximum")
            checks.extend(
                [
                    _contract_check("hillshade.cell_accounting", _is_int(total) and _is_int(valid) and _is_int(nodata) and valid + nodata == total, "hillshade valid and NODATA counts must close"),
                    _contract_check("hillshade.illumination_accounting", _is_int(valid) and _is_int(shadow) and _is_int(illuminated) and shadow + illuminated == valid, "hillshade shadow and illuminated counts must close"),
                    _contract_check("hillshade.fully_lit_accounting", _is_int(valid) and _is_int(fully_lit) and 0 <= fully_lit <= valid, "hillshade fully-lit count must be bounded by valid cells"),
                    _contract_check("hillshade.band_accounting", output_band.get("valid_cell_count") == valid and output_band.get("nodata_cell_count") == nodata, "hillshade band accounting must match report accounting"),
                    _contract_check("hillshade.range", isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)) and 0.0 <= minimum <= maximum <= 255.0, "hillshade output values must remain within 0..255"),
                ]
            )
    elif operation == "condition_raster_depressions":
        accounting = report.get("depression_fill_accounting")
        output_bands = _dotted(report, "output.bands")
        expected_band_names = [
            "conditioned_elevation_meters",
            "depression_fill_depth_meters",
        ]
        output_band_receipts_valid = (
            isinstance(output_bands, list)
            and len(output_bands) == 2
            and all(isinstance(row, dict) for row in output_bands)
        )
        maximum_fill_depth = _dotted(report, "parameters.maximum_fill_depth_m")
        checks.extend(
            [
                _contract_check("depression_fill.adapter", _dotted(report, "parameters.adapter") == "priority_flood_8_connected_open_boundary_v1", "depression-fill adapter identity must be frozen"),
                _contract_check("depression_fill.band", isinstance(_dotted(report, "parameters.band"), int) and _dotted(report, "parameters.band") >= 1, "depression-fill band must be one-based"),
                _contract_check("depression_fill.elevation_units", _dotted(report, "parameters.elevation_units") == "meters", "depression-fill elevation units must be explicitly meters"),
                _contract_check("depression_fill.horizontal_units", _dotted(report, "parameters.horizontal_units") == "meters" and _dotted(report, "parameters.horizontal_unit_factor") == 1.0, "depression-fill horizontal units must resolve to metres"),
                _contract_check("depression_fill.connectivity", _dotted(report, "parameters.connectivity") == 8, "depression-fill connectivity must be exact"),
                _contract_check("depression_fill.boundary_policy", _dotted(report, "parameters.boundary_policy") == "raster_edge_and_nodata_adjacency_are_outlets", "depression-fill boundary policy must preserve edge and NODATA outlets"),
                _contract_check("depression_fill.fill_policy", _dotted(report, "parameters.fill_policy") == "raise_enclosed_cells_to_lowest_reachable_spill_elevation", "depression-fill policy must be frozen"),
                _contract_check("depression_fill.flat_policy", _dotted(report, "parameters.flat_policy") == "preserve_spill_elevation_no_epsilon_gradient", "depression-fill must not synthesize epsilon gradients"),
                _contract_check("depression_fill.breach_policy", _dotted(report, "parameters.breach_policy") == "NONE", "depression-fill must not silently breach barriers"),
                _contract_check("depression_fill.maximum_fill_depth", _is_number(maximum_fill_depth) and float(maximum_fill_depth) >= 0.0, "depression-fill maximum depth must be finite and non-negative"),
                _contract_check("depression_fill.output_hash", bool(_dotted(report, "output.sha256")), "depression-fill output hash is required"),
                _contract_check("depression_fill.crs", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "depression-fill source and output CRS must match"),
                _contract_check("depression_fill.transform", _dotted(report, "source.transform") == _dotted(report, "output.transform"), "depression-fill source and output transforms must match"),
                _contract_check("depression_fill.bounds", _dotted(report, "source.bounds") == _dotted(report, "output.bounds"), "depression-fill source and output bounds must match"),
                _contract_check("depression_fill.width", _dotted(report, "source.width") == _dotted(report, "output.width"), "depression-fill source and output widths must match"),
                _contract_check("depression_fill.height", _dotted(report, "source.height") == _dotted(report, "output.height"), "depression-fill source and output heights must match"),
                _contract_check("depression_fill.output_band_count", _dotted(report, "output.band_count") == 2, "depression-fill output must contain two semantic bands"),
                _contract_check("depression_fill.output_dtypes", _dotted(report, "output.dtypes") == ["float64"] * 2, "depression-fill output bands must be float64"),
                _contract_check("depression_fill.output_nodata", _dotted(report, "output.nodata_values") == [-9999.0] * 2, "depression-fill output NODATA declaration must be frozen"),
                _contract_check("depression_fill.output_descriptions", _dotted(report, "output.descriptions") == expected_band_names, "depression-fill output band descriptions must be exact"),
                _contract_check("depression_fill.output_bands", output_band_receipts_valid, "depression-fill output band receipts are required"),
                _contract_check("depression_fill.accounting", isinstance(accounting, dict), "depression-fill accounting is required"),
            ]
        )
        if isinstance(accounting, dict) and output_band_receipts_valid:
            total = accounting.get("total_cell_count")
            valid = accounting.get("source_valid_cell_count")
            nodata = accounting.get("source_nodata_cell_count")
            seeds = accounting.get("outlet_seed_cell_count")
            interior = accounting.get("interior_nonseed_cell_count")
            processed = accounting.get("processed_valid_cell_count")
            raised = accounting.get("raised_cell_count")
            unchanged = accounting.get("unchanged_cell_count")
            components = accounting.get("depression_component_count")
            observed_maximum = accounting.get("maximum_fill_depth_m")
            total_fill_depth = accounting.get("total_fill_depth_m")
            fill_volume = accounting.get("fill_volume_cubic_metres")
            cell_area = _dotted(report, "parameters.cell_area_square_metres")
            conditioned_band, fill_depth_band = output_bands
            fill_minimum = fill_depth_band.get("minimum")
            fill_maximum = fill_depth_band.get("maximum")
            checks.extend(
                [
                    _contract_check("depression_fill.source_accounting", _is_int(total) and _is_int(valid) and _is_int(nodata) and valid + nodata == total, "depression-fill source valid and NODATA counts must close"),
                    _contract_check("depression_fill.seed_accounting", _is_int(valid) and _is_int(seeds) and _is_int(interior) and seeds > 0 and seeds + interior == valid, "depression-fill outlet and interior counts must close"),
                    _contract_check("depression_fill.processed_accounting", _is_int(valid) and _is_int(processed) and processed == valid, "depression-fill must process every valid cell"),
                    _contract_check("depression_fill.change_accounting", _is_int(valid) and _is_int(raised) and _is_int(unchanged) and raised + unchanged == valid, "depression-fill raised and unchanged counts must close"),
                    _contract_check("depression_fill.component_accounting", _is_int(raised) and _is_int(components) and (components == 0 if raised == 0 else 0 < components <= raised), "depression components must be bounded by raised cells"),
                    _contract_check("depression_fill.maximum_within_limit", _is_number(observed_maximum) and _is_number(maximum_fill_depth) and 0.0 <= float(observed_maximum) <= float(maximum_fill_depth) + 1e-12, "observed fill depth must remain within the explicit maximum"),
                    _contract_check("depression_fill.volume_accounting", _is_number(fill_volume) and _is_number(total_fill_depth) and _is_number(cell_area) and math.isclose(float(fill_volume), float(total_fill_depth) * float(cell_area), rel_tol=0.0, abs_tol=max(1e-9, abs(float(fill_volume)) * 1e-12)), "fill volume must equal total depth times affine cell area"),
                    _contract_check("depression_fill.band_accounting", all(band.get("valid_cell_count") == valid and band.get("nodata_cell_count") == nodata for band in output_bands), "every depression-fill band must preserve source valid and NODATA accounting"),
                    _contract_check("depression_fill.band_names", [band.get("name") for band in output_bands] == expected_band_names, "depression-fill band receipt names must match output descriptions"),
                    _contract_check("depression_fill.depth_range", _is_number(fill_minimum) and _is_number(fill_maximum) and _is_number(observed_maximum) and float(fill_minimum) >= 0.0 and math.isclose(float(fill_maximum), float(observed_maximum), rel_tol=0.0, abs_tol=1e-12), "fill-depth band must be non-negative and match maximum accounting"),
                    _contract_check("depression_fill.conditioned_range", _is_number(conditioned_band.get("minimum")) and _is_number(conditioned_band.get("maximum")) and float(conditioned_band.get("minimum")) <= float(conditioned_band.get("maximum")), "conditioned elevation range must be ordered"),
                ]
            )
    elif operation == "derive_raster_d8_flow":
        accounting = report.get("flow_accounting")
        state_counts = report.get("state_counts")
        direction_counts = report.get("direction_counts")
        output_bands = _dotted(report, "output.bands")
        expected_band_names = [
            "d8_candidate_bitmask_esri",
            "flow_accumulation_cells_including_self",
            "contributing_area_square_metres",
            "flow_state_code",
        ]
        output_band_receipts_valid = (
            isinstance(output_bands, list)
            and len(output_bands) == 4
            and all(isinstance(row, dict) for row in output_bands)
        )
        expected_states = {
            "FLOW_UNIQUE": 1,
            "BOUNDARY_TERMINAL": 2,
            "SINK_UNRESOLVED": 3,
            "FLAT_UNRESOLVED": 4,
            "TIE_UNRESOLVED": 5,
        }
        expected_directions = {
            "E": 1,
            "SE": 2,
            "S": 4,
            "SW": 8,
            "W": 16,
            "NW": 32,
            "N": 64,
            "NE": 128,
        }
        checks.extend(
            [
                _contract_check("d8.adapter", _dotted(report, "parameters.adapter") == "d8_steepest_descent_complete_neighborhood_v1", "D8 adapter identity must be frozen"),
                _contract_check("d8.band", isinstance(_dotted(report, "parameters.band"), int) and _dotted(report, "parameters.band") >= 1, "D8 band must be one-based"),
                _contract_check("d8.elevation_units", _dotted(report, "parameters.elevation_units") == "meters", "D8 elevation units must be explicitly meters"),
                _contract_check("d8.horizontal_units", _dotted(report, "parameters.horizontal_units") == "meters" and _dotted(report, "parameters.horizontal_unit_factor") == 1.0, "D8 horizontal units must resolve to metres"),
                _contract_check("d8.neighborhood_policy", _dotted(report, "parameters.neighborhood_policy") == "complete_3x3", "D8 neighborhood policy must be complete 3x3"),
                _contract_check("d8.boundary_policy", _dotted(report, "parameters.boundary_policy") == "terminal_no_external_elevation_assumption", "D8 boundary policy must fail closed on unknown external elevation"),
                _contract_check("d8.sink_policy", _dotted(report, "parameters.sink_policy") == "UNRESOLVED_NO_FILL", "D8 sinks must remain unresolved without synthetic fill"),
                _contract_check("d8.flat_policy", _dotted(report, "parameters.flat_policy") == "UNRESOLVED_NO_ROUTING", "D8 flats must remain unresolved without synthetic routing"),
                _contract_check("d8.tie_policy", _dotted(report, "parameters.tie_policy") == "PRESERVE_CANDIDATE_BITMASK_NO_ROUTING", "D8 ties must preserve candidates without arbitrary routing"),
                _contract_check("d8.accumulation_policy", _dotted(report, "parameters.accumulation_policy") == "upstream_cell_count_including_self", "D8 accumulation semantics must be frozen"),
                _contract_check("d8.direction_convention", _dotted(report, "parameters.direction_convention") == "esri_powers_of_two_clockwise_from_east", "D8 direction convention must be frozen"),
                _contract_check("d8.state_codebook", report.get("state_codebook") == expected_states, "D8 state codebook must be exact"),
                _contract_check("d8.direction_codebook", report.get("direction_codebook") == expected_directions, "D8 direction codebook must be exact"),
                _contract_check("d8.output_hash", bool(_dotted(report, "output.sha256")), "D8 output hash is required"),
                _contract_check("d8.crs", _dotted(report, "source.crs") == _dotted(report, "output.crs"), "D8 source and output CRS must match"),
                _contract_check("d8.transform", _dotted(report, "source.transform") == _dotted(report, "output.transform"), "D8 source and output transforms must match"),
                _contract_check("d8.bounds", _dotted(report, "source.bounds") == _dotted(report, "output.bounds"), "D8 source and output bounds must match"),
                _contract_check("d8.width", _dotted(report, "source.width") == _dotted(report, "output.width"), "D8 source and output widths must match"),
                _contract_check("d8.height", _dotted(report, "source.height") == _dotted(report, "output.height"), "D8 source and output heights must match"),
                _contract_check("d8.output_band_count", _dotted(report, "output.band_count") == 4, "D8 output must contain four semantic bands"),
                _contract_check("d8.output_dtypes", _dotted(report, "output.dtypes") == ["float64"] * 4, "D8 output bands must be float64"),
                _contract_check("d8.output_nodata", _dotted(report, "output.nodata_values") == [-9999.0] * 4, "D8 output NODATA declaration must be frozen"),
                _contract_check("d8.output_descriptions", _dotted(report, "output.descriptions") == expected_band_names, "D8 output band descriptions must be exact"),
                _contract_check("d8.output_bands", output_band_receipts_valid, "D8 output band receipts are required"),
                _contract_check("d8.state_counts", isinstance(state_counts, dict) and set(state_counts) == set(expected_states) and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in state_counts.values()), "D8 state counts must cover the exact codebook with non-negative integers"),
                _contract_check("d8.direction_counts", isinstance(direction_counts, dict) and set(direction_counts) == set(expected_directions) and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in direction_counts.values()), "D8 direction counts must cover the exact codebook with non-negative integers"),
                _contract_check("d8.accounting", isinstance(accounting, dict), "D8 flow accounting is required"),
            ]
        )
        if (
            isinstance(accounting, dict)
            and isinstance(state_counts, dict)
            and isinstance(direction_counts, dict)
            and output_band_receipts_valid
        ):
            total = accounting.get("total_cell_count")
            valid = accounting.get("source_valid_cell_count")
            nodata = accounting.get("source_nodata_cell_count")
            complete = accounting.get("complete_neighborhood_cell_count")
            unique = accounting.get("unique_flow_cell_count")
            terminal = accounting.get("terminal_cell_count")
            flow_edges = accounting.get("flow_edge_count")
            nonzero = accounting.get("nonzero_direction_cell_count")
            terminal_accumulation = accounting.get("terminal_accumulation_cell_count_total")
            terminal_area = accounting.get("terminal_contributing_area_square_metres_total")
            cell_area = _dotted(report, "parameters.cell_area_square_metres")
            state_counts_valid = all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in state_counts.values()
            )
            direction_counts_valid = all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in direction_counts.values()
            )
            state_total = sum(state_counts.values()) if state_counts_valid else -1
            terminal_state_total = (
                sum(
                    state_counts.get(name, -1)
                    for name in (
                        "BOUNDARY_TERMINAL",
                        "SINK_UNRESOLVED",
                        "FLAT_UNRESOLVED",
                        "TIE_UNRESOLVED",
                    )
                )
                if state_counts_valid
                else -1
            )
            direction_total = sum(direction_counts.values()) if direction_counts_valid else -1
            terminal_accounting_valid = _is_int(terminal) and _is_int(valid) and _is_int(unique) and terminal == terminal_state_total == valid - unique
            checks.extend(
                [
                    _contract_check("d8.source_accounting", _is_int(total) and _is_int(valid) and _is_int(nodata) and valid + nodata == total, "D8 source valid and NODATA counts must close"),
                    _contract_check("d8.neighborhood_accounting", _is_int(valid) and _is_int(complete) and 0 < complete <= valid, "D8 complete neighborhoods must be a non-empty subset of valid cells"),
                    _contract_check("d8.state_accounting", state_counts_valid and isinstance(valid, int) and state_total == valid, "D8 states must classify every valid cell exactly once"),
                    _contract_check("d8.flow_edge_accounting", state_counts_valid and all(isinstance(value, int) for value in (unique, flow_edges)) and unique == flow_edges == state_counts.get("FLOW_UNIQUE"), "D8 flow edges must equal unique-flow states"),
                    _contract_check("d8.terminal_accounting", terminal_accounting_valid, "D8 terminal states must close to valid cells without flow edges"),
                    _contract_check("d8.direction_accounting", state_counts_valid and direction_counts_valid and all(isinstance(value, int) for value in (nonzero, unique)) and direction_total == unique and nonzero == unique + state_counts.get("TIE_UNRESOLVED", -1), "D8 direction counts and nonzero direction cells must close"),
                    _contract_check("d8.band_accounting", all(band.get("valid_cell_count") == valid and band.get("nodata_cell_count") == nodata for band in output_bands), "Every D8 band must preserve source valid and NODATA accounting"),
                    _contract_check("d8.band_names", [band.get("name") for band in output_bands] == expected_band_names, "D8 band receipt names must match output descriptions"),
                    _contract_check("d8.terminal_contribution", _is_number(terminal_accumulation) and isinstance(valid, int) and math.isclose(float(terminal_accumulation), float(valid), rel_tol=0.0, abs_tol=1e-9), "Terminal accumulation must equal the valid source-cell count"),
                    _contract_check("d8.terminal_area", _is_number(terminal_area) and _is_number(cell_area) and isinstance(valid, int) and math.isclose(float(terminal_area), float(valid) * float(cell_area), rel_tol=0.0, abs_tol=max(1e-9, abs(float(valid) * float(cell_area)) * 1e-12)), "Terminal contributing area must equal valid cells times affine cell area"),
                ]
            )
    elif operation == "validate_real_gis_corpus":
        assets = report.get("assets")
        asset_type_counts = report.get("asset_type_counts")
        checks.extend(
            [
                _contract_check("gis_corpus.assets", isinstance(assets, list) and bool(assets), "real GIS corpus report must include assets"),
                _contract_check("gis_corpus.asset_type_counts", isinstance(asset_type_counts, dict), "real GIS corpus report must include asset-type counts"),
            ]
        )
        if isinstance(assets, list):
            observed_asset_type_counts = {
                asset_type: sum(isinstance(asset, dict) and asset.get("asset_type") == asset_type for asset in assets)
                for asset_type in ("geojson", "raster")
            }
            checks.append(
                _contract_check(
                    "gis_corpus.asset_type_accounting",
                    asset_type_counts == observed_asset_type_counts,
                    "corpus asset-type counts must close to receipt rows",
                )
            )
            for index, asset in enumerate(assets):
                asset_type = asset.get("asset_type") if isinstance(asset, dict) else None
                checks.extend(
                    [
                        _contract_check(f"gis_corpus.assets.{index}.sha256", isinstance(asset, dict) and bool(asset.get("sha256")), "corpus asset hash is required"),
                        _contract_check(f"gis_corpus.assets.{index}.bytes", isinstance(asset, dict) and isinstance(asset.get("bytes"), int) and asset["bytes"] > 0, "corpus asset bytes must be positive"),
                        _contract_check(f"gis_corpus.assets.{index}.authority", isinstance(asset, dict) and bool(asset.get("authority")), "corpus authority is required"),
                        _contract_check(f"gis_corpus.assets.{index}.source_url", isinstance(asset, dict) and bool(asset.get("source_url")), "corpus source URL is required"),
                        _contract_check(f"gis_corpus.assets.{index}.query", isinstance(asset, dict) and isinstance(asset.get("query"), dict) and bool(asset.get("query")), "corpus source query is required"),
                        _contract_check(f"gis_corpus.assets.{index}.retrieval_utc", isinstance(asset, dict) and bool(asset.get("retrieval_utc")), "corpus retrieval UTC is required"),
                        _contract_check(f"gis_corpus.assets.{index}.crs", isinstance(asset, dict) and bool(asset.get("declared_crs")), "corpus declared CRS is required"),
                        _contract_check(f"gis_corpus.assets.{index}.asset_type", asset_type in {"geojson", "raster"}, "corpus asset type must be explicit and supported"),
                    ]
                )
                if asset_type == "geojson":
                    dimension_counts = asset.get("coordinate_dimension_counts")
                    checks.extend(
                        [
                            _contract_check(f"gis_corpus.assets.{index}.stable_ids", asset.get("stable_id_unique") is True, "GeoJSON corpus stable IDs must be unique"),
                            _contract_check(f"gis_corpus.assets.{index}.geometries", asset.get("geometry_valid_count") == asset.get("feature_count"), "all GeoJSON corpus geometries must be valid"),
                            _contract_check(
                                f"gis_corpus.assets.{index}.dimensions",
                                isinstance(dimension_counts, dict)
                                and all(isinstance(value, int) and value >= 0 for value in dimension_counts.values())
                                and sum(dimension_counts.values()) == asset.get("feature_count"),
                                "GeoJSON corpus coordinate dimensions must close to feature count",
                            ),
                        ]
                    )
                elif asset_type == "raster":
                    bands = asset.get("bands")
                    cell_count = asset.get("grid_cell_count")
                    checks.extend(
                        [
                            _contract_check(f"gis_corpus.assets.{index}.embedded_crs", asset.get("embedded_crs") == asset.get("declared_crs"), "raster embedded and declared CRS must match"),
                            _contract_check(f"gis_corpus.assets.{index}.grid", isinstance(asset.get("width"), int) and isinstance(asset.get("height"), int) and asset.get("width", 0) * asset.get("height", 0) == cell_count, "raster grid dimensions must close to cell count"),
                            _contract_check(f"gis_corpus.assets.{index}.bands", isinstance(bands, list) and len(bands) == asset.get("band_count"), "raster band receipts must close to band count"),
                            _contract_check(
                                f"gis_corpus.assets.{index}.band_accounting",
                                asset.get("band_accounting_closed") is True
                                and isinstance(bands, list)
                                and all(
                                    isinstance(row, dict)
                                    and isinstance(row.get("valid_cell_count"), int)
                                    and isinstance(row.get("nodata_cell_count"), int)
                                    and row.get("valid_cell_count", -1) + row.get("nodata_cell_count", -1) == cell_count
                                    and isinstance(row.get("zero_cell_count"), int)
                                    and row.get("zero_cell_count", -1) <= row.get("valid_cell_count", -1)
                                    for row in bands
                                ),
                                "raster valid, NODATA, and zero accounting must close",
                            ),
                            _contract_check(f"gis_corpus.assets.{index}.elevation_units", asset.get("elevation_units") == "meters" and bool(asset.get("elevation_units_evidence")), "raster elevation units and evidence must be frozen"),
                            _contract_check(f"gis_corpus.assets.{index}.manifestation", bool(asset.get("source_manifestation")), "raster source manifestation classification is required"),
                            _contract_check(f"gis_corpus.assets.{index}.response_headers", isinstance(asset.get("response_headers"), dict) and bool(asset.get("response_headers")), "raster export response headers are required"),
                        ]
                    )
    elif operation == "create_map_export_manifest":
        layers = report.get("layers")
        checks.extend(
            [
                _contract_check("map_export.output_hash", bool(_dotted(report, "output.sha256")), "map/export output hash is required"),
                _contract_check("map_export.output_bytes", isinstance(_dotted(report, "output.bytes"), int) and _dotted(report, "output.bytes") > 0, "map/export output bytes must be positive"),
                _contract_check("map_export.output_crs", bool(_dotted(report, "output.crs")), "map/export output CRS is required"),
                _contract_check("map_export.extent", isinstance(_dotted(report, "output.extent"), list) and len(_dotted(report, "output.extent")) == 4, "map/export extent is required"),
                _contract_check("map_export.renderer", bool(_dotted(report, "renderer.name")) and bool(_dotted(report, "renderer.version")), "renderer identity and version are required"),
                _contract_check("map_export.layers", isinstance(layers, list) and bool(layers), "at least one bound layer is required"),
                _contract_check("map_export.layer_count", isinstance(layers, list) and _dotted(report, "invariants.layer_count") == len(layers), "bound layer count must close"),
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
    elif operation == "clip_geojson_by_bbox":
        checks.extend(
            [
                _contract_check("clip.parameters.bbox", isinstance(_dotted(report, "parameters.bbox"), list) and len(_dotted(report, "parameters.bbox")) == 4, "clip reports must record four query bbox values"),
                _contract_check("clip.parameters.adapter", _dotted(report, "parameters.adapter") == "shapely_bbox_intersection_v1", "clip reports must name the Shapely bbox intersection adapter"),
                _contract_check("clip.output_hash", bool(_dotted(report, "output.sha256")), "clip reports must record derived output hash"),
                _contract_check("clip.retained_count_present", isinstance(_dotted(report, "invariants.retained_feature_count"), int), "retained_feature_count must be recorded"),
                _contract_check("clip.clipped_count_present", isinstance(_dotted(report, "invariants.clipped_feature_count"), int), "clipped_feature_count must be recorded"),
                _contract_check("clip.unchanged_count_present", isinstance(_dotted(report, "invariants.unchanged_feature_count"), int), "unchanged_feature_count must be recorded"),
                _contract_check("clip.excluded_count_present", isinstance(_dotted(report, "invariants.excluded_feature_count"), int), "excluded_feature_count must be recorded"),
                _contract_check("clip.source_coordinate_count_present", isinstance(_dotted(report, "invariants.source_coordinate_count"), int), "source coordinate count must be recorded"),
                _contract_check("clip.output_coordinate_count_present", isinstance(_dotted(report, "invariants.output_coordinate_count"), int), "output coordinate count must be recorded"),
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


def validate_ocr_report_contract(report: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(report, dict), "report payload must be a JSON object"),
        _contract_check("schema_version", report.get("schema_version") == OCR_REPORT_SCHEMA_VERSION, f"schema_version must be {OCR_REPORT_SCHEMA_VERSION}"),
        _contract_check("report_id_present", bool(report.get("report_id")), "report_id is required"),
        _contract_check("status_valid", report.get("status") in {"OCR_ANALYSIS_OK", "OCR_ANALYSIS_NO_TEXT"}, "status must be OCR_ANALYSIS_OK or OCR_ANALYSIS_NO_TEXT"),
        _contract_check("operation", report.get("operation") == "image_ocr", "OCR reports must use image_ocr operation"),
        _contract_check("created_at_present", bool(report.get("created_at")), "created_at is required"),
        _contract_check("warnings_array", isinstance(report.get("warnings"), list), "warnings must be an array"),
        _contract_check("invariants_object", isinstance(report.get("invariants"), dict), "invariants must be an object"),
        _contract_check("limitations_non_empty", _non_empty_string_list(report.get("limitations")), "limitations must be non-empty strings"),
        _contract_check("falsification_criteria_non_empty", _non_empty_string_list(report.get("falsification_criteria")), "falsification criteria must be non-empty strings"),
    ]

    source = report.get("source")
    checks.append(_contract_check("source_object", isinstance(source, dict), "source image metadata must be recorded"))
    if isinstance(source, dict):
        checks.extend(
            [
                _contract_check("source.sha256_present", bool(source.get("sha256")), "source image must record a SHA-256"),
                _contract_check("source.mime_type_present", bool(source.get("mime_type")), "source image MIME type must be recorded"),
                _contract_check("source.media_kind_image", source.get("media_kind") == "image", "OCR source media_kind must be image"),
                _contract_check("source.bytes_positive", isinstance(source.get("bytes"), int) and source["bytes"] > 0, "source image byte count must be positive"),
                _contract_check("source.width_positive", isinstance(source.get("width"), int) and source["width"] > 0, "source image width must be positive"),
                _contract_check("source.height_positive", isinstance(source.get("height"), int) and source["height"] > 0, "source image height must be positive"),
                _contract_check("source.parser_warnings_array", isinstance(source.get("parser_warnings"), list), "source parser warnings must be an array"),
            ]
        )

    engine = report.get("engine")
    checks.append(_contract_check("engine_object", isinstance(engine, dict), "OCR engine metadata must be recorded"))
    if isinstance(engine, dict):
        checks.extend(
            [
                _contract_check("engine.name", engine.get("name") == "tesseract", "current OCR reports must identify the local Tesseract engine"),
                _contract_check("engine.version_present", bool(engine.get("version")), "OCR engine version must be recorded"),
                _contract_check("engine.language_present", bool(engine.get("language")), "OCR language must be recorded"),
                _contract_check("engine.layout_mode_present", bool(engine.get("layout_mode")), "OCR layout mode must be recorded"),
                _contract_check("engine.confidence_scale", engine.get("confidence_scale") == "0-100", "OCR confidence scale must be 0-100"),
            ]
        )

    text = report.get("text")
    checks.append(_contract_check("text_object", isinstance(text, dict), "OCR text tiers must be recorded"))
    if isinstance(text, dict):
        raw = text.get("raw")
        normalized = text.get("normalized")
        canonical = text.get("canonical")
        checks.extend(
            [
                _contract_check("text.raw_string", isinstance(raw, str), "raw OCR text must be a string"),
                _contract_check("text.normalized_string", isinstance(normalized, str), "normalized OCR text must be a string"),
                _contract_check("text.canonical_string", isinstance(canonical, str), "canonical OCR text must be a string"),
                _contract_check("text.canonical_matches_normalized", isinstance(normalized, str) and canonical == normalized.casefold(), "canonical OCR text must derive from normalized text"),
            ]
        )

    spans = report.get("spans")
    checks.append(_contract_check("spans_array", isinstance(spans, list), "OCR spans must be an array"))
    valid_span_count = 0
    low_confidence_count = 0
    unknown_confidence_count = 0
    if isinstance(spans, list):
        for index, span in enumerate(spans):
            if not isinstance(span, dict):
                checks.append(_contract_check(f"spans.{index}.object", False, "each OCR span must be an object"))
                continue
            valid_span_count += 1
            state = span.get("confidence_state")
            bbox = span.get("bbox")
            if state == "LOW":
                low_confidence_count += 1
            if state == "UNKNOWN":
                unknown_confidence_count += 1
            checks.extend(
                [
                    _contract_check(f"spans.{index}.raw_text_string", isinstance(span.get("raw_text"), str), "span raw_text must be a string"),
                    _contract_check(f"spans.{index}.normalized_text_string", isinstance(span.get("normalized_text"), str), "span normalized_text must be a string"),
                    _contract_check(f"spans.{index}.canonical_text_string", isinstance(span.get("canonical_text"), str), "span canonical_text must be a string"),
                    _contract_check(f"spans.{index}.confidence_state", state in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}, "span confidence_state must be a known state"),
                    _contract_check(f"spans.{index}.page_index", isinstance(span.get("page_index"), int) and span["page_index"] >= 0, "span page_index must be a non-negative integer"),
                    _contract_check(f"spans.{index}.bbox", isinstance(bbox, dict) and all(isinstance(bbox.get(key), int) and bbox[key] >= 0 for key in ("x", "y", "width", "height")), "span bbox must include non-negative source coordinates"),
                ]
            )

    invariants = report.get("invariants", {})
    if isinstance(invariants, dict):
        checks.extend(
            [
                _contract_check("invariants.raw_normalized_canonical_separated", invariants.get("raw_normalized_canonical_separated") is True, "OCR text tiers must remain separated"),
                _contract_check("invariants.span_count_matches", invariants.get("span_count") == valid_span_count, "span_count must equal saved spans"),
                _contract_check("invariants.low_confidence_count_matches", invariants.get("low_confidence_count") == low_confidence_count, "low-confidence count must equal saved spans"),
                _contract_check("invariants.unknown_confidence_count_matches", invariants.get("unknown_confidence_count") == unknown_confidence_count, "unknown-confidence count must equal saved spans"),
                _contract_check("invariants.empty_text_state_matches", invariants.get("empty_text_state") == (valid_span_count == 0), "empty_text_state must match saved spans"),
                _contract_check("invariants.bounding_boxes_have_source_coordinates", invariants.get("bounding_boxes_have_source_coordinates") is True, "span bounding boxes must retain source coordinates"),
            ]
        )

    layout = report.get("layout")
    if layout is not None:
        layout_invariants = layout.get("invariants") if isinstance(layout, dict) else None
        layout_order = layout.get("reading_order_span_indices") if isinstance(layout, dict) else None
        checks.extend(
            [
                _contract_check("layout.object", isinstance(layout, dict), "layout receipt must be an object when present"),
                _contract_check("layout.lines_array", isinstance(layout.get("lines"), list) if isinstance(layout, dict) else False, "layout lines must be an array"),
                _contract_check("layout.reading_order_array", isinstance(layout_order, list), "layout reading order must be an array"),
                _contract_check("layout.span_accounting_closed", isinstance(layout_invariants, dict) and layout_invariants.get("span_accounting_closed") is True, "layout span accounting must close"),
                _contract_check("layout.no_semantic_table_inference", isinstance(layout_invariants, dict) and layout_invariants.get("semantic_tables_inferred") is False, "layout receipt must not claim semantic table inference"),
                _contract_check("layout.no_semantic_form_inference", isinstance(layout_invariants, dict) and layout_invariants.get("semantic_forms_inferred") is False, "layout receipt must not claim semantic form inference"),
                _contract_check("layout.reading_order_matches_spans", isinstance(layout_order, list) and sorted(layout_order) == list(range(valid_span_count)), "layout reading order must account for every saved span exactly once"),
            ]
        )

    confidence = report.get("confidence")
    if confidence is not None:
        confidence_counts = confidence.get("counts") if isinstance(confidence, dict) else None
        confidence_invariants = confidence.get("invariants") if isinstance(confidence, dict) else None
        expected_counts = {
            "LOW": low_confidence_count,
            "MEDIUM": sum(1 for span in spans if isinstance(span, dict) and span.get("confidence_state") == "MEDIUM") if isinstance(spans, list) else 0,
            "HIGH": sum(1 for span in spans if isinstance(span, dict) and span.get("confidence_state") == "HIGH") if isinstance(spans, list) else 0,
            "UNKNOWN": unknown_confidence_count,
        }
        checks.extend(
            [
                _contract_check("confidence.object", isinstance(confidence, dict), "confidence receipt must be an object when present"),
                _contract_check("confidence.scale", isinstance(confidence, dict) and confidence.get("scale") == "0-100", "confidence receipt scale must be 0-100"),
                _contract_check("confidence.counts_object", isinstance(confidence_counts, dict), "confidence counts must be an object"),
                _contract_check("confidence.counts_match_spans", confidence_counts == expected_counts, "confidence counts must match saved span states"),
                _contract_check("confidence.span_accounting_closed", isinstance(confidence_invariants, dict) and confidence_invariants.get("span_accounting_closed") is True, "confidence span accounting must close"),
            ]
        )

    warnings = report.get("warnings")
    if isinstance(warnings, list):
        checks.append(_contract_check("warning_labels_non_empty", all(isinstance(warning, str) and warning for warning in warnings), "warning labels must be non-empty strings"))
        if report.get("status") == "OCR_ANALYSIS_NO_TEXT":
            checks.append(_contract_check("no_text_status_has_warning", "OCR_NO_TEXT_DETECTED" in warnings, "no-text OCR reports must carry OCR_NO_TEXT_DETECTED"))
        if low_confidence_count:
            checks.append(_contract_check("low_confidence_has_warning", "OCR_LOW_CONFIDENCE_PRESENT" in warnings, "low-confidence spans must be reflected in warnings"))
        if unknown_confidence_count:
            checks.append(_contract_check("unknown_confidence_has_warning", "OCR_UNKNOWN_CONFIDENCE_PRESENT" in warnings, "unknown-confidence spans must be reflected in warnings"))
    if report.get("status") == "OCR_ANALYSIS_OK":
        checks.append(_contract_check("ok_status_has_text", valid_span_count > 0, "OCR_ANALYSIS_OK requires at least one saved span"))

    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": CAPABILITY_OUTPUT_CONTRACT_OK if not failed else CAPABILITY_OUTPUT_CONTRACT_FAILED,
        "schema_version": "mucaro_capability_output_contract_v1",
        "capability_id": "state_of_art_ocr_analysis",
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


def validate_ocr_report_file(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        failed_check = _contract_check("report_file_exists", False, f"report not found: {path}")
        return {
            "status": CAPABILITY_OUTPUT_CONTRACT_FAILED,
            "schema_version": "mucaro_capability_output_contract_v1",
            "capability_id": "state_of_art_ocr_analysis",
            "report_path": str(path),
            "checks": [failed_check],
            "counts": {"pass": 0, "fail": 1, "total": 1},
            "failed_checks": [failed_check],
        }
    except json.JSONDecodeError as exc:
        failed_check = _contract_check("report_json_valid", False, str(exc))
        return {
            "status": CAPABILITY_OUTPUT_CONTRACT_FAILED,
            "schema_version": "mucaro_capability_output_contract_v1",
            "capability_id": "state_of_art_ocr_analysis",
            "report_path": str(path),
            "checks": [failed_check],
            "counts": {"pass": 0, "fail": 1, "total": 1},
            "failed_checks": [failed_check],
        }
    if not isinstance(report, dict):
        report = {}
    return validate_ocr_report_contract(report, report_path=str(path))


def build_evidence_contract(
    *,
    claim_type: str,
    evidence_type: str,
    source_identity_type: str,
    confidence: str,
    uncertainty_state: str,
    contradiction_state: str,
    verification_method: str,
    limitations: list[str],
    falsification_criteria: list[str],
    unresolved_residue: list[str] | None = None,
) -> dict[str, Any]:
    """Build the shared evidence envelope used by advanced capability outputs."""
    return {
        "schema_version": EVIDENCE_CONTRACT_SCHEMA_VERSION,
        "claim_type": claim_type,
        "evidence_type": evidence_type,
        "source_identity_type": source_identity_type,
        "confidence": confidence,
        "uncertainty_state": uncertainty_state,
        "contradiction_state": contradiction_state,
        "verification_method": verification_method,
        "limitations": list(limitations),
        "falsification_criteria": list(falsification_criteria),
        "unresolved_residue": list(unresolved_residue or []),
    }


def validate_evidence_contract(contract: Any, *, prefix: str = "evidence_contract") -> list[dict[str, str]]:
    checks: list[dict[str, str]] = [
        _contract_check(f"{prefix}.object", isinstance(contract, dict), "shared evidence contract must be an object"),
    ]
    if not isinstance(contract, dict):
        return checks
    checks.extend(
        [
            _contract_check(f"{prefix}.schema_version", contract.get("schema_version") == EVIDENCE_CONTRACT_SCHEMA_VERSION, f"evidence contract schema must be {EVIDENCE_CONTRACT_SCHEMA_VERSION}"),
            _contract_check(f"{prefix}.claim_type", contract.get("claim_type") in EVIDENCE_CLAIM_TYPES, "claim_type must use the governed evidence taxonomy"),
            _contract_check(f"{prefix}.evidence_type", isinstance(contract.get("evidence_type"), str) and bool(contract.get("evidence_type")), "evidence_type is required"),
            _contract_check(f"{prefix}.source_identity_type", isinstance(contract.get("source_identity_type"), str) and bool(contract.get("source_identity_type")), "source_identity_type is required"),
            _contract_check(f"{prefix}.confidence", isinstance(contract.get("confidence"), str) and bool(contract.get("confidence")), "confidence state is required"),
            _contract_check(f"{prefix}.uncertainty_state", isinstance(contract.get("uncertainty_state"), str) and bool(contract.get("uncertainty_state")), "uncertainty_state is required"),
            _contract_check(f"{prefix}.contradiction_state", isinstance(contract.get("contradiction_state"), str) and bool(contract.get("contradiction_state")), "contradiction_state is required"),
            _contract_check(f"{prefix}.verification_method", isinstance(contract.get("verification_method"), str) and bool(contract.get("verification_method")), "verification_method is required"),
            _contract_check(f"{prefix}.limitations", _non_empty_string_list(contract.get("limitations")), "limitations must be non-empty strings"),
            _contract_check(f"{prefix}.falsification_criteria", _non_empty_string_list(contract.get("falsification_criteria")), "falsification criteria must be non-empty strings"),
            _contract_check(f"{prefix}.unresolved_residue", isinstance(contract.get("unresolved_residue"), list) and all(isinstance(item, str) and item for item in contract.get("unresolved_residue", [])), "unresolved_residue must be a string array"),
        ]
    )
    return checks


def _contract_result(
    *,
    capability_id: str,
    report: Any,
    report_path: str | None,
    checks: list[dict[str, str]],
    operation: str | None = None,
) -> dict[str, Any]:
    failed = [check for check in checks if check["status"] == "FAIL"]
    return {
        "status": CAPABILITY_OUTPUT_CONTRACT_OK if not failed else CAPABILITY_OUTPUT_CONTRACT_FAILED,
        "schema_version": "mucaro_capability_output_contract_v1",
        "capability_id": capability_id,
        "report_path": report_path,
        "report_status": report.get("status") if isinstance(report, dict) else None,
        "operation": operation if operation is not None else (report.get("operation") if isinstance(report, dict) else None),
        "checks": checks,
        "counts": {
            "pass": sum(1 for check in checks if check["status"] == "PASS"),
            "fail": len(failed),
            "total": len(checks),
        },
        "failed_checks": failed,
    }


def _read_contract_file(path: Path, *, capability_id: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        check = _contract_check("report_file_exists", False, f"report not found: {path}")
        return {}, [check]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        check = _contract_check("report_json_valid", False, str(exc))
        return {}, [check]
    if not isinstance(payload, dict):
        return {}, [_contract_check("payload_object", False, f"{capability_id} report must be a JSON object")]
    return payload, []


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def _valid_commit_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40,64}", value))


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username and not parsed.password


def validate_web_snapshot_contract(report: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(report, dict), "web snapshot must be an object"),
        _contract_check("schema_version", report.get("schema_version") == WEB_SNAPSHOT_SCHEMA_VERSION, f"schema_version must be {WEB_SNAPSHOT_SCHEMA_VERSION}"),
        _contract_check("snapshot_id", isinstance(report.get("snapshot_id"), str) and bool(report.get("snapshot_id")), "snapshot_id is required"),
        _contract_check("snapshot_kind", report.get("snapshot_kind") in {"web_page", "http_error_page"}, "snapshot_kind must be web_page or http_error_page"),
        _contract_check("requested_url", _valid_http_url(report.get("requested_url")), "requested_url must be an http(s) URL without credentials"),
        _contract_check("fetch_url", _valid_http_url(report.get("fetch_url")), "fetch_url must be an http(s) URL without credentials"),
        _contract_check("final_url", _valid_http_url(report.get("final_url")), "final_url must be an http(s) URL without credentials"),
        _contract_check("retrieval_started_utc", isinstance(report.get("retrieval_started_utc"), str) and bool(report.get("retrieval_started_utc")), "retrieval_started_utc is required"),
        _contract_check("retrieval_utc", isinstance(report.get("retrieval_utc"), str) and bool(report.get("retrieval_utc")), "retrieval_utc is required"),
        _contract_check("http_status", _is_int(report.get("http_status")) and 100 <= report["http_status"] <= 599, "http_status must be 100..599"),
        _contract_check("content_type", isinstance(report.get("content_type"), str) and bool(report.get("content_type")), "content_type is required"),
        _contract_check("content_sha256", _valid_sha256(report.get("content_sha256")), "content_sha256 must be a lowercase SHA-256"),
        _contract_check("content_size_bytes", _is_int(report.get("content_size_bytes")) and report["content_size_bytes"] >= 0, "content_size_bytes must be non-negative"),
        _contract_check("content_base64", isinstance(report.get("content_base64"), str) and (bool(report.get("content_base64")) or report.get("content_size_bytes") == 0), "content_base64 is required for byte identity"),
        _contract_check("redirect_chain", isinstance(report.get("redirect_chain"), list), "redirect_chain must be an array"),
        _contract_check("fragment_removed", isinstance(report.get("fragment_removed"), bool), "fragment_removed must be boolean"),
        _contract_check("parser_name", isinstance(report.get("parser_name"), str) and bool(report.get("parser_name")), "parser_name is required"),
        _contract_check("parser_trace", isinstance(report.get("parser_trace"), dict), "parser_trace must be an object"),
        _contract_check("parser_warnings", isinstance(report.get("parser_warnings"), list) and all(isinstance(item, str) and item for item in report.get("parser_warnings", [])), "parser_warnings must be a string array"),
        _contract_check("charset", isinstance(report.get("charset"), str), "charset must be recorded, including an empty value"),
        _contract_check("text_preview", isinstance(report.get("text_preview"), str), "text_preview must be a string, including for binary content"),
        _contract_check("cache_policy", isinstance(report.get("cache_policy"), dict), "cache_policy must be an object"),
        _contract_check("robots_policy", isinstance(report.get("robots_policy"), dict), "robots_policy must be an object"),
        _contract_check("http_error_policy", isinstance(report.get("http_error_policy"), dict), "http_error_policy must be an object"),
    ]
    if isinstance(report.get("content_base64"), str):
        try:
            content = base64.b64decode(report["content_base64"], validate=True)
            checks.extend(
                [
                    _contract_check("content_bytes_match_size", len(content) == report.get("content_size_bytes"), "decoded content size must match content_size_bytes"),
                    _contract_check("content_bytes_match_hash", _valid_sha256(report.get("content_sha256")) and hashlib.sha256(content).hexdigest() == report.get("content_sha256"), "decoded content SHA-256 must match content_sha256"),
                ]
            )
        except (ValueError, TypeError):
            checks.append(_contract_check("content_base64_decodes", False, "content_base64 must be valid base64"))
    if isinstance(report.get("cache_policy"), dict):
        cache = report["cache_policy"]
        checks.extend(
            [
                _contract_check("cache_policy.mode", cache.get("cache_mode") == "immutable_snapshot_only", "cache mode must be immutable_snapshot_only"),
                _contract_check("cache_policy.reuse_disabled", cache.get("reuse_allowed") is False, "snapshot reuse must remain disabled"),
                _contract_check("cache_policy.revalidation_required", cache.get("revalidation_required_before_reuse") is True, "revalidation must be required before reuse"),
                _contract_check("cache_policy.warnings", isinstance(cache.get("warnings"), list) and all(isinstance(item, str) for item in cache.get("warnings", [])), "cache warnings must be a string array"),
            ]
        )
    if isinstance(report.get("http_error_policy"), dict) and isinstance(report.get("http_status"), int):
        error_policy = report["http_error_policy"]
        is_error = bool(report["http_status"] >= 400)
        checks.extend(
            [
                _contract_check("http_error_policy.status_consistent", error_policy.get("is_http_error") is is_error, "HTTP error policy must match the final HTTP status"),
                _contract_check("http_error_policy.snapshot_allowed", error_policy.get("snapshot_allowed") is is_error, "HTTP error snapshots are only allowed for explicit error-page capture"),
                _contract_check("http_error_policy.citation_allowed_boolean", isinstance(error_policy.get("citation_allowed"), bool), "citation_allowed must be boolean"),
                _contract_check("http_error_policy.citation_not_implicit", not is_error or error_policy.get("citation_allowed") is False or error_policy.get("snapshot_allowed") is True, "error-page citation must be explicit"),
            ]
        )
    checks.extend(validate_evidence_contract(report.get("evidence_contract")))
    return _contract_result(capability_id="governed_web_browsing", report=report, report_path=report_path, checks=checks, operation="fetch_web_snapshot")


def validate_web_snapshot_file(path: Path) -> dict[str, Any]:
    report, read_checks = _read_contract_file(path, capability_id="governed_web_browsing")
    if read_checks:
        return _contract_result(capability_id="governed_web_browsing", report=report, report_path=str(path), checks=read_checks, operation="fetch_web_snapshot")
    return validate_web_snapshot_contract(report, report_path=str(path))


def validate_web_browse_session_contract(report: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    candidates = report.get("candidate_count")
    selected = report.get("selected_count")
    successful = report.get("successful_fetch_count")
    decisions = report.get("selection_decisions")
    fetches = report.get("fetches")
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(report, dict), "browse session must be an object"),
        _contract_check("schema_version", report.get("schema_version") == WEB_BROWSE_SESSION_SCHEMA_VERSION, f"schema_version must be {WEB_BROWSE_SESSION_SCHEMA_VERSION}"),
        _contract_check("session_id", isinstance(report.get("session_id"), str) and bool(report.get("session_id")), "session_id is required"),
        _contract_check("status", report.get("status") in {"WEB_BROWSE_SESSION_OK", "WEB_BROWSE_SESSION_PARTIAL"}, "session status must be OK or PARTIAL"),
        _contract_check("timestamps", bool(report.get("started_at")) and bool(report.get("completed_at")), "session timestamps are required"),
        _contract_check("browser_discovery_policy", isinstance(report.get("browser_discovery_policy"), dict), "browser discovery policy is required"),
        _contract_check("session_policy", isinstance(report.get("session_policy"), dict), "session policy is required"),
        _contract_check("fetch_policy", isinstance(report.get("fetch_policy"), dict), "fetch policy is required"),
        _contract_check("candidate_count", isinstance(candidates, int) and candidates >= 1, "candidate_count must be positive"),
        _contract_check("selected_count", isinstance(selected, int) and 1 <= selected <= max(1, candidates if isinstance(candidates, int) else 1), "selected_count must be within candidate_count"),
        _contract_check("successful_fetch_count", isinstance(successful, int) and 0 <= successful <= max(0, selected if isinstance(selected, int) else 0), "successful_fetch_count must be within selected_count"),
        _contract_check("selection_decisions", isinstance(decisions, list) and isinstance(candidates, int) and len(decisions) == candidates, "one selection decision is required per candidate"),
        _contract_check("fetches", isinstance(fetches, list) and isinstance(selected, int) and len(fetches) == selected, "one fetch receipt is required per selected URL"),
        _contract_check("warnings", isinstance(report.get("warnings"), list) and all(isinstance(item, str) and item for item in report.get("warnings", [])), "warnings must be a string array"),
        _contract_check("replay", isinstance(report.get("replay"), dict) and report.get("replay", {}).get("tool_name") == "web.fetch_session", "replay must bind to web.fetch_session"),
    ]
    if isinstance(report.get("browser_discovery_policy"), dict):
        policy = report["browser_discovery_policy"]
        checks.extend(
            [
                _contract_check("browser_discovery_policy.search_disabled", policy.get("search_enabled") is False, "search must remain explicitly disabled for operator-supplied sessions"),
                _contract_check("browser_discovery_policy.follow_disabled", policy.get("automated_link_following") is False, "automated link following must remain disabled"),
                _contract_check("browser_discovery_policy.candidate_source", policy.get("candidate_source") == "operator_supplied_urls", "candidate source must be operator-supplied URLs"),
            ]
        )
    if isinstance(decisions, list):
        selected_decisions = [row for row in decisions if isinstance(row, dict) and row.get("decision") == "SELECTED"]
        checks.append(_contract_check("selection_count_matches", len(selected_decisions) == selected, "selected decisions must close to selected_count"))
    if isinstance(fetches, list):
        successful_fetches = [row for row in fetches if isinstance(row, dict) and row.get("status") == "WEB_FETCH_SNAPSHOT_OK"]
        checks.append(_contract_check("successful_count_matches", len(successful_fetches) == successful, "successful fetch rows must close to successful_fetch_count"))
    if isinstance(report.get("status"), str):
        expected_status = "WEB_BROWSE_SESSION_OK" if successful == selected else "WEB_BROWSE_SESSION_PARTIAL"
        checks.append(_contract_check("status_matches_accounting", report.get("status") == expected_status, "session status must match fetch accounting"))
    checks.extend(validate_evidence_contract(report.get("evidence_contract")))
    return _contract_result(capability_id="governed_web_browsing", report=report, report_path=report_path, checks=checks, operation="fetch_web_browse_session")


def validate_web_browse_session_file(path: Path) -> dict[str, Any]:
    report, read_checks = _read_contract_file(path, capability_id="governed_web_browsing")
    if read_checks:
        return _contract_result(capability_id="governed_web_browsing", report=report, report_path=str(path), checks=read_checks, operation="fetch_web_browse_session")
    return validate_web_browse_session_contract(report, report_path=str(path))


def validate_answer_trace_contract(trace: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    retrieved = trace.get("retrieved_chunk_ids")
    cited = trace.get("cited_chunk_ids")
    records = trace.get("cited_chunks")
    flags = trace.get("validation_flags")
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(trace, dict), "answer trace must be an object"),
        _contract_check("query", isinstance(trace.get("query"), str) and bool(trace.get("query", "").strip()), "answer trace query is required"),
        _contract_check("retrieved_chunk_ids", isinstance(retrieved, list) and all(isinstance(item, str) and item for item in retrieved), "retrieved_chunk_ids must be a string array"),
        _contract_check("cited_chunk_ids", isinstance(cited, list) and all(isinstance(item, str) and item for item in cited), "cited_chunk_ids must be a string array"),
        _contract_check("validation_flags", isinstance(flags, list) and all(isinstance(item, str) and item for item in flags), "validation_flags must be a string array"),
        _contract_check("cited_chunks", isinstance(records, list), "cited_chunks must be an array"),
        _contract_check("citation_status", trace.get("citation_status") in {"CITATION_OK", "CITATION_ERROR"}, "citation_status must be explicit"),
    ]
    if isinstance(retrieved, list) and isinstance(cited, list):
        checks.extend(
            [
                _contract_check("retrieved_ids_unique", len(retrieved) == len(set(retrieved)), "retrieved chunk IDs must be unique"),
                _contract_check("cited_ids_unique", len(cited) == len(set(cited)), "cited chunk IDs must be unique"),
                _contract_check("cited_subset_of_retrieved", set(cited).issubset(set(retrieved)), "cited chunks must come from retrieved chunks"),
                _contract_check("uncited_answer_not_ok", trace.get("citation_status") != "CITATION_OK" or bool(cited), "a citation-ok answer must cite at least one chunk"),
            ]
        )
    if isinstance(records, list) and isinstance(cited, list):
        record_ids = [str(row.get("chunk_id")) for row in records if isinstance(row, dict)]
        checks.extend(
            [
                _contract_check("citation_record_count", len(records) == len(cited), "one citation record is required per cited chunk"),
                _contract_check("citation_records_bind_ids", set(record_ids) == set(cited), "citation records must bind exactly to cited chunk IDs"),
                _contract_check("citation_order_closed", [row.get("citation_order") for row in records if isinstance(row, dict)] == list(range(1, len(records) + 1)), "citation order must be contiguous"),
            ]
        )
    if isinstance(flags, list) and trace.get("citation_status") == "CITATION_OK":
        checks.append(_contract_check("citation_ok_has_no_errors", not any(str(flag).startswith("ERROR_") for flag in flags), "citation-ok traces cannot carry error flags"))
    checks.extend(validate_evidence_contract(trace.get("evidence_contract")))
    return _contract_result(capability_id="transparency_objectivity_governance", report=trace, report_path=report_path, checks=checks, operation="answer_trace")


def validate_coding_review_contract(report: dict[str, Any], *, report_path: str | None = None) -> dict[str, Any]:
    changed_paths = report.get("changed_paths")
    findings = report.get("findings")
    checks: list[dict[str, str]] = [
        _contract_check("payload_object", isinstance(report, dict), "coding review must be an object"),
        _contract_check("schema_version", report.get("schema_version") == CODING_REVIEW_SCHEMA_VERSION, f"schema_version must be {CODING_REVIEW_SCHEMA_VERSION}"),
        _contract_check("status", report.get("status") in {"CODING_REVIEW_FINDINGS", "CODING_REVIEW_NO_FINDINGS"}, "coding review status must be explicit"),
        _contract_check("repo_root", isinstance(report.get("repo_root"), str) and bool(report.get("repo_root")), "repo_root is required"),
        _contract_check("head_sha", _valid_commit_identity(report.get("head_sha")), "head_sha must be a lowercase hexadecimal commit identity"),
        _contract_check("base_commit_sha", _valid_commit_identity(report.get("base_commit_sha")), "base_commit_sha must be a lowercase hexadecimal commit identity"),
        _contract_check("changed_paths", isinstance(changed_paths, list) and len(changed_paths) == len(set(changed_paths)) and all(isinstance(item, str) and item for item in changed_paths), "changed_paths must be a unique string array"),
        _contract_check("findings", isinstance(findings, list), "findings must be an array"),
        _contract_check("finding_count", isinstance(report.get("finding_count"), int) and isinstance(findings, list) and report.get("finding_count") == len(findings), "finding_count must close to findings"),
        _contract_check("invariants", isinstance(report.get("invariants"), dict), "review invariants are required"),
        _contract_check("limitations", _non_empty_string_list(report.get("limitations")), "review limitations are required"),
        _contract_check("falsification_criteria", _non_empty_string_list(report.get("falsification_criteria")), "review falsification criteria are required"),
    ]
    if isinstance(findings, list) and isinstance(changed_paths, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                checks.append(_contract_check(f"findings.{index}.object", False, "finding must be an object"))
                continue
            path = finding.get("path")
            line_start = finding.get("line_start")
            line_end = finding.get("line_end")
            excerpt = finding.get("excerpt")
            checks.extend(
                [
                    _contract_check(f"findings.{index}.path", path in changed_paths, "finding path must be in changed_paths"),
                    _contract_check(f"findings.{index}.line_range", isinstance(line_start, int) and isinstance(line_end, int) and 0 < line_start <= line_end, "finding line range must be positive and ordered"),
                    _contract_check(f"findings.{index}.source_sha256", _valid_sha256(finding.get("source_sha256")), "finding must bind a source SHA-256"),
                    _contract_check(f"findings.{index}.excerpt_sha256", _valid_sha256(finding.get("excerpt_sha256")) and isinstance(excerpt, str) and hashlib.sha256(excerpt.encode("utf-8")).hexdigest() == finding.get("excerpt_sha256"), "finding excerpt hash must match its excerpt"),
                ]
            )
    if isinstance(report.get("invariants"), dict):
        checks.extend(
            [
                _contract_check("invariants.finding_count_closed", report["invariants"].get("finding_count_closed") is True, "finding count invariant must close"),
                _contract_check("invariants.changed_sources_bound", report["invariants"].get("changed_sources_bound") is True, "changed source binding invariant must hold"),
            ]
        )
    checks.extend(validate_evidence_contract(report.get("evidence_contract")))
    return _contract_result(capability_id="transparency_objectivity_governance", report=report, report_path=report_path, checks=checks, operation="coding_review")


def validate_coding_review_file(path: Path) -> dict[str, Any]:
    report, read_checks = _read_contract_file(path, capability_id="transparency_objectivity_governance")
    if read_checks:
        return _contract_result(capability_id="transparency_objectivity_governance", report=report, report_path=str(path), checks=read_checks, operation="coding_review")
    return validate_coding_review_contract(report, report_path=str(path))


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
                "axis_order_policy",
                "transformer_definition_sha256",
                "grid_manifest_sha256",
                "grid_sha256",
                "grid_bytes",
                "grid_source_commit_sha",
                "grid_license_evidence_url",
                "network_access_performed",
                "query_bbox",
                "bbox_filter_mode",
                "clip_bbox",
                "clip_adapter",
                "overlay_operation",
                "overlay_state",
                "overlay_warning",
                "overlay_adapter",
                "geometry_type",
                "feature_count",
                "retained_feature_count",
                "excluded_feature_count",
                "measured_feature_count",
                "total_area",
                "total_length",
                "measurement_mode",
                "area_units",
                "length_units",
                "operation_name",
                "operation_parameters",
                "feature_match_key",
                "coordinate_dimension",
                "skipped_row_count",
                "topology_state",
                "topology_adapter",
                "topology_warning",
                "repair_state",
                "repair_adapter",
                "source_geometry_sha256",
                "output_geometry_sha256",
                "ellipsoid",
                "ellipsoidal_adapter",
                "raster_driver",
                "raster_band",
                "nodata_value",
                "vector_format",
                "vector_source_sha256",
                "raster_source_sha256",
                "cell_inclusion_rule",
                "zonal_statistic",
                "zero_cell_count",
                "resampling_method",
                "output_width",
                "output_height",
                "corpus_asset_sha256",
                "corpus_authority",
                "corpus_retrieval_utc",
                "corpus_stable_id_path",
                "renderer_name",
                "renderer_version",
                "bound_layer_sha256",
                "planar_overlay_adapter",
                "planar_overlay_state",
                "intersection_area",
                "symmetric_difference_area",
                "planar_overlay_intersection_area_total",
                "planar_overlay_symmetric_difference_area_total",
                "hausdorff_vertex_distance",
                "vertex_symmetric_difference_count",
                "illumination_azimuth_degrees_clockwise_from_north",
                "illumination_altitude_degrees_above_horizon",
                "terrain_lineage_report_sha256",
                "terrain_lineage_output_sha256",
                "terrain_lineage_source_sha256",
                "d8_direction_convention",
                "d8_state_codebook",
                "d8_direction_codebook",
                "elevation_tolerance_m",
                "gradient_tie_tolerance",
                "terminal_accumulation_cell_count_total",
                "terminal_contributing_area_square_metres_total",
                "depression_conditioning_adapter",
                "maximum_fill_depth_m",
                "outlet_seed_cell_count",
                "raised_cell_count",
                "depression_component_count",
                "fill_volume_cubic_metres",
            ),
            invariants=(
                "row conservation or explicit retained/excluded counts",
                "stable ID uniqueness when stable IDs are present",
                "CRS preservation or explicit reprojection record",
                "geometry validity and NULL/EMPTY classification",
                "GeoJSON inspection records Shapely planar topology state and closes topology state counts",
                "join cardinality classification for spatial joins",
                "coordinate dimensionality, Z, and M presence are retained",
                "geometry comparisons separate exact coordinate equality from topological equality",
                "polygon geometry comparisons record planar overlay area metrics without applying them to non-polygon rows",
                "CSV-derived point features preserve raw properties and close source/output/skipped row counts",
                "bbox filtering preserves CRS and closes retained/excluded feature counts",
                "bbox clipping preserves CRS and closes retained/excluded/clipped/unchanged feature counts",
                "standalone polygon overlay preserves CRS, writes output hashes, and closes feature-key and pair-state accounting",
                "planar and geodesic measurements preserve CRS, units labels, per-feature residue, and total area/length accounting",
                "pyproj CRS reprojection records an always-XY policy, frozen transformer definition, output hash, and feature conservation",
                "the governed Puerto Rico datum-grid path binds the exact public-domain source revision, manifest hash, grid hash, byte count, authorized CRS pair, canonical pipeline, and absent runtime network access",
                "explicit topology repair records before/after geometry hashes, repair state, output hash, and row/output accounting",
                "ellipsoidal WGS84 measurement records pyproj Geod identity, meter units, per-feature state, and row accounting",
                "vector and raster manifests preserve source format, CRS manifestations, bands, dimensions, NODATA, and source hashes",
                "vector-raster analysis requires exact CRS agreement, separates NODATA from zero, and closes feature states",
                "zonal statistics close covered, valid, NODATA, and zero-cell accounting before reporting numeric summaries",
                "raster resampling preserves extent, CRS, band count, and NODATA declarations while recording a new output hash",
                "authoritative corpus fixtures preserve immutable bytes, query provenance, explicit asset types, vector schema/identity/geometry, and raster grid/value accounting",
                "terrain derivation requires projected horizontal metres and explicit vertical metres, preserves grid identity, and separates complete neighborhoods, NODATA, and flat aspect",
                "hillshade binds the exact terrain report and raster hashes, explicit sun azimuth/altitude, 0..255 value range, and valid/NODATA/shadow accounting",
                "D8 hydrology requires projected horizontal metres and explicit vertical metres, preserves tied candidate directions, leaves boundaries/sinks/flats/ties terminal, and closes terminal accumulation and contributing-area accounting",
                "priority-flood depression conditioning preserves edge and NODATA outlets, never lowers elevations, requires an explicit maximum fill depth, and closes changed-cell and fill-volume accounting",
                "map/export provenance binds output hash, extent, CRS, renderer identity, and every declared source-layer hash",
            ),
            positive_regression_gates=(
                "point-in-polygon fixture classifies FULLY_WITHIN, TOUCH_ONLY, OUTSIDE, and NULL_EMPTY",
                "GeoJSON inspection fixture records invalid topology without repairing or flattening geometries",
                "geometry comparison fixture records exact equality, topological equality, Hausdorff distance, and vertex symmetric difference",
                "geometry comparison fixture records Polygon/MultiPolygon planar intersection and symmetric-difference area",
                "reprojection fixture records source CRS, target CRS, adapter identity, output hash, and row/coordinate conservation",
                "CSV point fixture records header, source rows, output features, skipped rows, CRS, and output hash",
                "bbox filter fixture records query bbox, filter mode, retained features, excluded features, NULL/EMPTY residue, CRS, and output hash",
                "bbox clip fixture records query bbox, adapter identity, clipped features, unchanged features, unresolved Z/M residue, CRS, and output hash",
                "standalone overlay fixture records matched polygon intersection output, adapter identity, output hash, and result area accounting",
                "planar measurement fixture records polygon area with holes, line length, null residue, units, and aggregate totals",
                "geodesic measurement fixture records EPSG:4326 source CRS, meter units, adapter identity, and aggregate totals",
                "pyproj CRS fixture records always-XY policy and a frozen transformer pipeline hash",
                "Puerto Rico datum-grid fixture selects the exact local NOAA grid, differs materially from the gridless ballpark transform, and preserves an explicit independent-engine OPEN state",
                "explicit topology repair fixture records valid repaired output without changing inspection semantics",
                "pyproj Geod fixture records WGS84 ellipsoidal distance and area receipts",
                "GeoJSON vector-format and GeoTIFF raster fixtures preserve source manifestations and NODATA accounting",
                "vector-raster fixture preserves valid zero, NODATA-only, and NULL/EMPTY as distinct states",
                "zonal-statistics fixture records count, sum, minimum, maximum, mean, median, population standard deviation, zero, and NODATA states",
                "raster-resampling fixture preserves the requested dimensions, extent, CRS, band count, and NODATA declaration",
                "frozen Census TIGERweb, USGS earthquake, and USGS 3DEP raster manifestations validate exact bytes, authority, retrieval UTC, type-specific schema, and accounting",
                "analytic terrain fixture proves slope/aspect orientation and complete-neighborhood accounting on a projected metre grid",
                "analytic hillshade fixture proves light-direction semantics, terrain lineage, 0..255 bounds, and zero-shadow versus NODATA separation",
                "analytic D8 fixtures prove monotonic direction, tie preservation, strict-descent edges, and terminal contribution conservation",
                "analytic priority-flood fixtures prove enclosed-bowl fill, unchanged monotonic terrain, NODATA-adjacent outlet preservation, explicit depth limiting, and volume closure",
                "GIS report contract validator accepts real saved measurement receipts and rejects weak provenance or false accounting",
                "GIS eval manifest runs positive and negative fixtures through the supported CLI boundary",
                "map export fixture preserves renderer identity plus source and derived layer hashes",
            ),
            negative_regression_gates=(
                "mismatched CRS fails closed unless reprojection is explicit",
                "unsupported CRS transformation fails closed without output synthesis",
                "CSV point conversion fails closed on missing required headers, duplicate headers, malformed coordinates, and no valid output features",
                "bbox filtering fails closed on malformed query extents and unsupported modes",
                "bbox clipping fails closed on malformed query extents and records Z/M dimensionality as unresolved residue",
                "GeoJSON inspection records invalid topology as warning-state residue instead of repairing it silently",
                "standalone overlay fails closed on CRS mismatch or invalid operation and records unsupported geometry, dimensional, empty, and topology residue without silent output synthesis",
                "planar measurement fails closed on missing units and records invalid or unsupported geometries as residue",
                "geodesic measurement fails closed for non-EPSG:4326 sources and records invalid longitude/latitude coordinates as residue",
                "pyproj CRS transformation fails closed for invalid CRS and M coordinates",
                "governed datum-grid transformation fails closed before output on manifest drift, missing or altered grid bytes, path escape, unauthorized CRS pairs, and coordinates outside the operation area",
                "topology repair retains unsupported dimensions as explicit unresolved residue",
                "ellipsoidal measurement fails closed for non-EPSG:4326 sources",
                "raster clipping fails closed on invalid or nonintersecting extents",
                "vector-raster analysis fails closed on absent or mismatched CRS and invalid bands",
                "zonal statistics fail closed on invalid bands and retain malformed, dimensional, invalid, and NULL/EMPTY feature states",
                "raster resampling fails closed on unsupported methods, absent CRS, invalid dimensions, or excessive output-cell requests",
                "terrain derivation fails closed on geographic/non-metre/rotated grids, invalid bands, and grids outside the bounded dimensions",
                "hillshade fails closed on invalid azimuth or altitude, absent valid terrain neighborhoods, and terrain lineage that violates slope/aspect mask semantics",
                "D8 hydrology fails closed on geographic/non-metre/rotated grids, invalid bands or tolerances, absent complete neighborhoods, and grids outside the bounded cell limit",
                "depression conditioning fails closed on geographic/non-metre/rotated grids, invalid bands or depth limits, excessive required fill, and grids outside the bounded cell limit",
                "real-corpus validation fails closed on byte drift, path escape, unknown asset type, vector ID/schema/geometry drift, or raster grid/value drift",
                "map/export provenance fails closed on empty artifacts, invalid extents, missing layer CRS, or absent renderer identity",
                "GIS report contract validation fails when warning status, source hashes, CRS, or asserted accounting are missing or inconsistent",
                "ambiguous nearest-neighbor match remains UNRESOLVED",
                "M:N spatial join requires explicit multiplication counts",
                "feature comparison with unmatched keys records left-only and right-only residue",
                "planar overlay comparison records unsupported dimensionality as unresolved residue instead of dropping Z/M",
            ),
            output_requirements=(
                "FACT/INFERENCE/ASSUMPTION/UNKNOWN separation",
                "spatial state per feature or aggregate",
                "input/output hashes",
                "limitations and falsification criteria",
            ),
            unavailable_until=(
                "topology policy and overlay operations extend beyond explicit planar make_valid and the current Polygon/MultiPolygon boundary",
                "additional regional datum-grid transformations, vertical CRS, time-dependent operations, broader axis-order cases, and independent-engine datum validation are certified",
                "ellipsoidal hole-orientation and large-geometry edge cases close beyond the bounded antimeridian and polar control corpus",
                "broader vector/raster formats, fractional zonal coverage, additional resampling kernels, remote sensing, hydrology beyond bounded priority-flood conditioning and D8, bathymetry, and temporal GIS are receipt-backed",
                "rendered-map visual correctness and larger, more diverse real vector/raster corpora are independently certified",
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
                "OCR report contract validator accepts saved image OCR receipts and rejects false span/confidence accounting",
            ),
            negative_regression_gates=(
                "blank image returns no-text state without synthesis",
                "illegible span stays low confidence",
                "language mismatch emits warning instead of fabricated certainty",
                "OCR report contract validation fails when source metadata, engine identity, confidence scale, warnings, or span counts are missing or inconsistent",
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
                "evidence_contract",
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
                "evidence_contract",
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
