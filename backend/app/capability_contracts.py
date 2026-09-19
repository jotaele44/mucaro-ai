from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CAPABILITY_CONTRACT_SCHEMA_VERSION = "mucaro_advanced_capability_contracts_v1"
CAPABILITY_OUTPUT_CONTRACT_OK = "CAPABILITY_OUTPUT_CONTRACT_OK"
CAPABILITY_OUTPUT_CONTRACT_FAILED = "CAPABILITY_OUTPUT_CONTRACT_FAILED"
GIS_REPORT_SCHEMA_VERSION = "mucaro_gis_report_v1"
OCR_REPORT_SCHEMA_VERSION = "mucaro_ocr_report_v1"


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
            "crs_exactly_matched",
            "nodata_separate_from_zero",
            "no_implicit_reprojection",
            "all_layer_hashes_present",
            "output_hash_present",
            "renderer_identity_present",
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
        checks.extend(
            [
                _contract_check("crs_engine.parameters.adapter", _dotted(report, "parameters.adapter") == "pyproj_transformer_group_always_xy_v1", "CRS engine reports must name the pyproj TransformerGroup always-XY adapter"),
                _contract_check("crs_engine.parameters.source_crs", bool(_dotted(report, "parameters.source_crs")), "resolved source CRS is required"),
                _contract_check("crs_engine.parameters.target_crs", bool(_dotted(report, "parameters.target_crs")), "resolved target CRS is required"),
                _contract_check("crs_engine.parameters.always_xy", _dotted(report, "parameters.always_xy") is True, "CRS axis order must be explicit"),
                _contract_check("crs_engine.parameters.axis_order_policy", _dotted(report, "parameters.axis_order_policy") == "traditional_gis_xy", "traditional GIS XY policy must be recorded"),
                _contract_check("crs_engine.parameters.pyproj_version", bool(_dotted(report, "parameters.pyproj_version")), "pyproj version is required"),
                _contract_check("crs_engine.parameters.proj_version", bool(_dotted(report, "parameters.proj_version")), "PROJ version is required"),
                _contract_check("crs_engine.parameters.candidates", isinstance(_dotted(report, "parameters.available_candidate_count"), int) and _dotted(report, "parameters.available_candidate_count") >= 1, "at least one available transformation candidate is required"),
                _contract_check("crs_engine.parameters.unavailable_candidates", isinstance(_dotted(report, "parameters.unavailable_candidate_count"), int) and _dotted(report, "parameters.unavailable_candidate_count") >= 0, "unavailable transformation candidates must be counted"),
                _contract_check("crs_engine.parameters.selected_candidate", _dotted(report, "parameters.selected_candidate_index") == 0, "the selected transformation candidate index is required"),
                _contract_check("crs_engine.parameters.best_available", isinstance(_dotted(report, "parameters.best_available"), bool), "best-operation availability must be explicit"),
                _contract_check("crs_engine.parameters.engine_warnings", isinstance(_dotted(report, "parameters.engine_warnings"), list), "captured PROJ engine warnings must be an array"),
                _contract_check("crs_engine.parameters.pipeline_definition", bool(_dotted(report, "parameters.transformer_definition")) and not str(_dotted(report, "parameters.transformer_definition")).lower().startswith("unavailable until"), "selected transformer definition must be resolved"),
                _contract_check("crs_engine.parameters.pipeline_hash", bool(_dotted(report, "parameters.transformer_definition_sha256")), "selected transformer definition hash is required"),
                _contract_check("crs_engine.best_available_warning", _dotted(report, "parameters.best_available") is True or "GIS_CRS_BEST_OPERATION_UNAVAILABLE" in (report.get("warnings") or []), "unavailable best operations must produce an explicit warning"),
                _contract_check("crs_engine.warning_status", _dotted(report, "parameters.best_available") is True or report.get("status") == "GIS_CRS_REPROJECT_WARN", "fallback operations must downgrade report status"),
                _contract_check("crs_engine.source_crs_matches", _dotted(report, "source.crs") == _dotted(report, "parameters.source_crs"), "source CRS must match resolved transform source"),
                _contract_check("crs_engine.output_crs_matches", _dotted(report, "output.crs") == _dotted(report, "parameters.target_crs"), "output CRS must match transform target"),
                _contract_check("crs_engine.output_hash", bool(_dotted(report, "output.sha256")), "transformed output hash is required"),
                _contract_check("crs_engine.features_array", isinstance(features, list), "per-feature transform receipts are required"),
                _contract_check("crs_engine.feature_conservation", _dotted(report, "invariants.feature_row_conservation") is True, "feature rows must be conserved"),
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
        source_count = _dotted(report, "source.feature_count")
        checks.extend(
            [
                _contract_check("ellipsoidal.parameters.adapter", _dotted(report, "parameters.adapter") == "pyproj_geod_wgs84_v1", "ellipsoidal reports must name pyproj Geod"),
                _contract_check("ellipsoidal.parameters.ellipsoid", _dotted(report, "parameters.ellipsoid") == "WGS84", "WGS84 ellipsoid must be explicit"),
                _contract_check("ellipsoidal.parameters.area_units", _dotted(report, "parameters.area_units") == "meters^2", "ellipsoidal area units must be meters^2"),
                _contract_check("ellipsoidal.parameters.length_units", _dotted(report, "parameters.length_units") == "meters", "ellipsoidal length units must be meters"),
                _contract_check("ellipsoidal.source_crs", str(_dotted(report, "source.crs") or "").upper() == "EPSG:4326", "ellipsoidal inputs must be EPSG:4326"),
                _contract_check("ellipsoidal.measurements_array", isinstance(measurements, list), "per-feature ellipsoidal measurements are required"),
                _contract_check("ellipsoidal.state_counts", isinstance(state_counts, dict), "ellipsoidal state counts are required"),
                _contract_check("ellipsoidal.total_area", _is_number(_dotted(report, "invariants.total_area_m2")), "ellipsoidal total area must be numeric"),
                _contract_check("ellipsoidal.total_length", _is_number(_dotted(report, "invariants.total_length_m")), "ellipsoidal total length must be numeric"),
            ]
        )
        if isinstance(measurements, list) and isinstance(state_counts, dict):
            state_total = sum(state_counts.get(state, -1) for state in ("MEASURED", "NULL_EMPTY", "UNRESOLVED"))
            checks.append(_contract_check("ellipsoidal.row_accounting", isinstance(source_count, int) and source_count == len(measurements) == state_total, "ellipsoidal state and receipt rows must close to source count"))
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
    elif operation == "analyze_vector_raster_intersection":
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
                "explicit topology repair records before/after geometry hashes, repair state, output hash, and row/output accounting",
                "ellipsoidal WGS84 measurement records pyproj Geod identity, meter units, per-feature state, and row accounting",
                "vector and raster manifests preserve source format, CRS manifestations, bands, dimensions, NODATA, and source hashes",
                "vector-raster analysis requires exact CRS agreement, separates NODATA from zero, and closes feature states",
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
                "explicit topology repair fixture records valid repaired output without changing inspection semantics",
                "pyproj Geod fixture records WGS84 ellipsoidal distance and area receipts",
                "GeoJSON vector-format and GeoTIFF raster fixtures preserve source manifestations and NODATA accounting",
                "vector-raster fixture preserves valid zero, NODATA-only, and NULL/EMPTY as distinct states",
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
                "topology repair retains unsupported dimensions as explicit unresolved residue",
                "ellipsoidal measurement fails closed for non-EPSG:4326 sources",
                "raster clipping fails closed on invalid or nonintersecting extents",
                "vector-raster analysis fails closed on absent or mismatched CRS and invalid bands",
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
                "datum-grid availability, vertical CRS, time-dependent operations, and broader axis-order cases are independently certified",
                "ellipsoidal antimeridian, polar, hole-orientation, and large-geometry edge cases close against a broader independent corpus",
                "broader vector/raster formats, resampling, zonal statistics, remote sensing, terrain, hydrology, bathymetry, and temporal GIS are receipt-backed",
                "rendered-map visual correctness and a larger real spatial corpus are independently certified",
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
