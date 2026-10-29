"""Governed vector workflows over byte-frozen GeoJSON inputs and shared GIS engines."""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .artifact_identity import create_artifact_record
from .config import Settings
from .gis_crs import reproject_geojson_crs
from .gis_geodesy import measure_geojson_ellipsoidal
from .gis_overlay_policy import OVERLAY_POLICY_SHA256
from .gis_processing import (
    GISProcessingError,
    compare_geojson_geometries,
    inspect_geojson,
    overlay_geojson_collections,
    spatial_join_points_to_polygons,
)
from .gis_topology_repair import repair_geojson_topology
from .local_inputs import snapshot_local_file
from .spatial_reports import record_spatial_artifacts

OPERATIONS = {
    "inspect": "Inspect geometry and topology",
    "measure_wgs84": "Measure distance and area on WGS84",
    "reproject": "Transform coordinate reference system",
    "repair": "Repair planar topology explicitly",
    "compare": "Compare geometry by declared feature ID",
    "intersection": "Intersect matched polygons",
    "union": "Union matched polygons",
    "difference": "Subtract matched reference polygons",
    "symmetric_difference": "Find differences between matched polygons",
    "spatial_join": "Locate points within reference polygons",
}
PAIR_OPERATIONS = ["compare", "intersection", "union", "difference", "symmetric_difference", "spatial_join"]
GIS_EXECUTION_POLICY = {
    "source_policy": "directory_grant_and_frozen_bytes_v1",
    "parser_policy": "strict_json_unique_keys_finite_numbers_feature_rows_v1",
    "pair_policy": "unique_typed_feature_ids_or_explicit_property_binding_v1",
    "report_policy": "private_invocation_directory_hash_verified_v1",
    "operations": list(OPERATIONS),
    "max_source_bytes": 5 * 1024 * 1024,
    "max_features": 1000,
    "max_json_nodes": 100_000,
    "max_json_depth": 64,
    "max_pair_coordinate_work": 5_000_000,
    "max_report_bytes": 2 * 1024 * 1024,
    "crs_network": "DISABLED",
    "overlay_policy_sha256": OVERLAY_POLICY_SHA256,
}
GIS_ARGUMENTS_SCHEMA = {
    "type": "object", "title": "Analyze spatial data",
    "properties": {
        "operation": {"type": "string", "enum": list(OPERATIONS), "default": "inspect", "title": "Spatial operation", "x-enum-labels": OPERATIONS},
        "path": {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S", "title": "Input GeoJSON path"},
        "reference_path": {"type": "string", "minLength": 1, "maxLength": 4096, "pattern": r"\S", "title": "Reference GeoJSON path", "x-operations": PAIR_OPERATIONS},
        "id_property": {"type": "string", "minLength": 1, "maxLength": 100, "title": "Input ID property (optional; blank uses feature ID)", "x-operations": PAIR_OPERATIONS},
        "reference_id_property": {"type": "string", "minLength": 1, "maxLength": 100, "title": "Reference ID property (optional; for example GEOID)", "x-operations": PAIR_OPERATIONS},
        "target_crs": {"type": "string", "pattern": r"^EPSG:[0-9]{3,6}$", "maxLength": 12, "title": "Target CRS (for example EPSG:3857)", "x-operations": ["reproject"]},
        "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "title": "Expected input SHA-256 (optional)"},
        "expected_reference_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "title": "Expected reference SHA-256 (optional)", "x-operations": PAIR_OPERATIONS},
    },
    "required": ["operation", "path"], "additionalProperties": False,
    "allOf": [
        {"if": {"properties": {"operation": {"enum": PAIR_OPERATIONS}}}, "then": {"required": ["reference_path"]}, "else": {"not": {"anyOf": [{"required": ["reference_path"]}, {"required": ["expected_reference_sha256"]}, {"required": ["id_property"]}, {"required": ["reference_id_property"]}]}}},
        {"if": {"properties": {"operation": {"const": "reproject"}}}, "then": {"required": ["target_crs"]}, "else": {"not": {"required": ["target_crs"]}}},
    ],
}


def _fail(code: str, detail: str) -> GISProcessingError:
    return GISProcessingError(f"ERROR_GIS_{code}", detail)


def _preflight(path: Path, *, require_ids: bool, id_property: str | None = None) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _fail("DUPLICATE_JSON_KEY", f"Duplicate JSON object key: {key!r}.")
            result[key] = value
        return result

    def invalid_constant(value: str) -> Any:
        raise _fail("NONFINITE_NUMBER", f"JSON contains a non-finite number: {value}.")

    try:
        payload = json.loads(path.read_bytes().decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise _fail("INVALID_JSON", "Choose a UTF-8 GeoJSON file with valid, bounded JSON.") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise _fail("UNSUPPORTED_GEOJSON", "Choose a GeoJSON FeatureCollection with a features array.")
    features = payload["features"]
    if len(features) > GIS_EXECUTION_POLICY["max_features"]:
        raise _fail("FEATURE_LIMIT", "Split this layer into explicit smaller inputs; the feature limit was exceeded.")
    if any(not isinstance(row, dict) or row.get("type") != "Feature" for row in features):
        raise _fail("FEATURE_ROWS_INVALID", "Every features entry must be a Feature object; malformed rows cannot be silently dropped.")
    if "crs" in payload:
        crs = payload["crs"]
        if not isinstance(crs, dict) or not isinstance(crs.get("properties"), dict) or not isinstance(crs["properties"].get("name"), str) or not crs["properties"]["name"].strip():
            raise _fail("CRS_INVALID", "An explicit CRS must declare its name; malformed CRS metadata cannot default silently.")
    stack, node_count = [(payload, 0)], 0
    while stack:
        item, depth = stack.pop()
        node_count += 1
        if node_count > GIS_EXECUTION_POLICY["max_json_nodes"] or depth > GIS_EXECUTION_POLICY["max_json_depth"]:
            raise _fail("COMPLEXITY_LIMIT", "Layer nesting or node count exceeds the declared spatial-work budget.")
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            try:
                finite = math.isfinite(item)
            except OverflowError:
                finite = False
            if not finite:
                raise _fail("NONFINITE_NUMBER", "Every numeric input must have a finite representation.")
        if isinstance(item, (dict, list)):
            stack.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    seen, duplicates, missing, raw_ids, coordinate_counts = set(), [], [], [], []
    for index, feature in enumerate(features):
        properties = feature.get("properties")
        raw_id = properties.get(id_property) if id_property and isinstance(properties, dict) else feature.get("id") if not id_property else None
        raw_ids.append(raw_id)
        geometry = feature.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        coordinate_stack, coordinate_count = [coordinates], 0
        while coordinate_stack:
            coordinate = coordinate_stack.pop()
            if not isinstance(coordinate, list):
                continue
            if len(coordinate) >= 2 and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in coordinate[:2]):
                coordinate_count += 1
            else:
                coordinate_stack.extend(coordinate)
        coordinate_counts.append(coordinate_count)
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or raw_id == "":
            missing.append(index)
            continue
        key = str(raw_id)  # Matches the existing engine's key semantics; never identity proof.
        if key in seen:
            duplicates.append({"feature_index": index, "id_raw": raw_id})
        seen.add(key)
    if require_ids and (duplicates or missing):
        raise _fail("FEATURE_IDS_UNRESOLVED", "Pair operations require unique explicit string/integer feature IDs in both layers. Resolve missing, duplicate, or colliding IDs first.")
    return {"feature_count": len(features), "json_node_count": node_count, "coordinate_counts": coordinate_counts, "feature_ids_raw": raw_ids, "id_property_raw": id_property, "missing_id_indices": missing, "duplicate_ids": duplicates}


def analyze_spatial_data(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    operation = arguments["operation"]
    sources = []
    for key, hash_key in [("path", "expected_sha256"), *([("reference_path", "expected_reference_sha256")] if operation in PAIR_OPERATIONS else [])]:
        frozen, manifest, artifact = snapshot_local_file(
            arguments[key], arguments.get(hash_key), cfg, extensions={".geojson", ".json"},
            max_bytes=GIS_EXECUTION_POLICY["max_source_bytes"], namespace="GIS", producer="gis_skill", error_type=GISProcessingError,
        )
        manifest["preflight"] = _preflight(frozen, require_ids=operation in PAIR_OPERATIONS, id_property=arguments.get("id_property" if key == "path" else "reference_id_property"))
        sources.append((frozen, manifest, artifact))
    alignment = None
    coordinate_work = 0
    if operation == "spatial_join":
        coordinate_work = sources[0][1]["preflight"]["feature_count"] * (sources[1][1]["preflight"]["feature_count"] + sum(sources[1][1]["preflight"]["coordinate_counts"]))
    if len(sources) == 2 and operation != "spatial_join":
        left, right = [{str(raw_id): raw_id for raw_id in item[1]["preflight"]["feature_ids_raw"]} for item in sources]
        intersection = sorted(left.keys() & right.keys())
        if any(type(left[key]) is not type(right[key]) for key in intersection):
            raise _fail("FEATURE_ID_TYPE_MISMATCH", "A numeric ID and a text ID would collide under the engine's key semantics; resolve the ID binding explicitly.")
        counts = [{str(raw_id): count for raw_id, count in zip(item[1]["preflight"]["feature_ids_raw"], item[1]["preflight"]["coordinate_counts"], strict=True)} for item in sources]
        coordinate_work = sum(counts[0][key] * counts[1][key] for key in intersection)
        alignment = {
            "classification": "FEATURE_ID_KEY_ALIGNMENT_CANDIDATE",
            "INTERSECTION": intersection, "A_ONLY": sorted(left.keys() - right.keys()),
            "B_ONLY": sorted(right.keys() - left.keys()), "UNION": sorted(left.keys() | right.keys()),
            "SYMMETRIC_DIFFERENCE": sorted(left.keys() ^ right.keys()),
        }
    if coordinate_work > GIS_EXECUTION_POLICY["max_pair_coordinate_work"]:
        raise _fail("PAIR_COMPLEXITY_LIMIT", "Split these layers before pair analysis; the combined coordinate-work budget was exceeded.")
    # Some legacy engine report IDs use second-resolution timestamps. A private
    # output directory keeps simultaneous identical requests from overwriting reports.
    run_dir = Path(tempfile.mkdtemp(prefix="gis-run-", dir=cfg.export_dir))
    run_cfg = replace(cfg, export_dir=run_dir)
    engine_sources, binding_ids = [], []
    for index, (frozen, manifest, artifact) in enumerate(sources):
        preflight = manifest["preflight"]
        if preflight["id_property_raw"]:
            payload = json.loads(frozen.read_bytes().decode("utf-8-sig"))
            for feature, raw_id in zip(payload["features"], preflight["feature_ids_raw"], strict=True):
                feature["id"] = raw_id
            raw = (json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            path = run_dir / f"id-bound-input-{index}.geojson"
            path.write_bytes(raw)
            path.chmod(0o400)
            digest = hashlib.sha256(raw).hexdigest()
            binding = create_artifact_record(
                processed_dir=cfg.processed_dir, artifact_type="DERIVED", payload=raw,
                logical_identity=f"gis_id_binding:{path}", producer="gis_skill", producer_version="1.0.0",
                parent_artifact_ids=[artifact.artifact_id], source_path_or_uri=str(path),
                provenance_parameters={"policy": "explicit_property_to_feature_id_v1", "id_property_raw": preflight["id_property_raw"]},
            )
            binding_ids.append(binding.artifact_id)
        else:
            path, digest = frozen, manifest["sha256"]
        manifest["engine_input"] = {"path": str(path), "sha256": digest, "id_property_raw": preflight["id_property_raw"]}
        engine_sources.append((path, digest))
    source = engine_sources[0][0]
    if operation == "inspect":
        report = inspect_geojson(source, cfg=run_cfg)
    elif operation == "measure_wgs84":
        report = measure_geojson_ellipsoidal(source, cfg=run_cfg)
    elif operation == "reproject":
        report = reproject_geojson_crs(source, target_crs=arguments["target_crs"], cfg=run_cfg)
    elif operation == "repair":
        report = repair_geojson_topology(source, cfg=run_cfg)
    elif operation == "compare":
        report = compare_geojson_geometries(source, engine_sources[1][0], cfg=run_cfg, match_on="id")
    elif operation == "spatial_join":
        report = spatial_join_points_to_polygons(source, engine_sources[1][0], cfg=run_cfg, require_matching_crs=True)
    else:
        report = overlay_geojson_collections(source, engine_sources[1][0], cfg=run_cfg, operation=operation, match_on="id")
    for frozen, manifest, _ in sources:
        if hashlib.sha256(frozen.read_bytes()).hexdigest() != manifest["sha256"]:
            raise _fail("SOURCE_CHANGED", "Frozen input changed during spatial analysis.")
    if any(hashlib.sha256(path.read_bytes()).hexdigest() != digest for path, digest in engine_sources):
        raise _fail("SOURCE_CHANGED", "ID-bound engine input changed during spatial analysis.")
    engine_inputs = report.get("inputs") or {"source": report.get("source") or report.get("input")}
    if sorted(item.get("sha256", "") for item in engine_inputs.values() if isinstance(item, dict)) != sorted(digest for _, digest in engine_sources):
        raise _fail("SOURCE_HASH_MISMATCH", "Engine input hashes do not match the frozen input manifestations.")
    invariants = report.get("invariants", {})
    if any(value is False for name, value in invariants.items() if "accounting_closed" in name or "conservation" in name or name == "crs_match"):
        raise _fail("INVARIANT_FAILED", "Spatial row accounting or CRS compatibility failed. Review the retained reports.")
    source_ids = [artifact.artifact_id for _, _, artifact in sources]
    downloads = []
    artifact_ids = record_spatial_artifacts(
        report, cfg=cfg, run_dir=run_dir, parent_ids=[*source_ids, *binding_ids], producer="gis_skill",
        max_report_bytes=GIS_EXECUTION_POLICY["max_report_bytes"], max_output_bytes=GIS_EXECUTION_POLICY["max_report_bytes"],
        downloads=downloads,
    )
    parser_warnings = ["GIS_FEATURE_IDS_NOT_CANONICAL" for _, manifest, _ in sources if manifest["preflight"]["missing_id_indices"] or manifest["preflight"]["duplicate_ids"]]
    warnings = list(dict.fromkeys([*report.get("warnings", []), *parser_warnings]))
    return {
        "kind": "GIS_ANALYSIS", "status": "GIS_SKILL_REVIEW_REQUIRED" if warnings else "GIS_SKILL_OK",
        "operation": operation, "operation_label": OPERATIONS[operation], "report": report,
        "source_manifests": [manifest for _, manifest, _ in sources], "source_artifact_ids": source_ids,
        "artifact_ids": artifact_ids, "warnings": warnings, "trust": "UNTRUSTED_CONTENT",
        "files": downloads,
        "alignment": alignment,
        "coordinate_work": coordinate_work,
        "identity_state": "CANDIDATE_NOT_IDENTITY", "epistemic_classification": "COMPUTED",
        "limitations": [*report.get("limitations", []), "Feature ID alignment and spatial relations do not establish canonical identity.", "This task supports bounded local GeoJSON vector workflows; raster, hydrology, and other advanced workflows have separate contracts."],
    }
