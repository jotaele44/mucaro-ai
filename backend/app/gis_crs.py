from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError

from .config import Settings, settings
from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary, _feature_id, _load_geojson, _write_geojson_output, _write_report
from .utils import stable_id, utc_now

CRS_ADAPTER = "pyproj_transformer_always_xy_v1"


def _transform_coordinates(value: Any, transformer: Transformer) -> Any:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value[:2]):
        x, y = transformer.transform(float(value[0]), float(value[1]), errcheck=True)
        return [x, y, *value[2:]]
    if isinstance(value, list):
        return [_transform_coordinates(item, transformer) for item in value]
    raise GISProcessingError("ERROR_GIS_CRS_INVALID_COORDINATES", "Geometry contains a non-position coordinate value.")


def reproject_geojson_crs(path: Path, *, target_crs: str, cfg: Settings = settings, source_crs: str | None = None) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    source_name = source_crs or source.crs
    try:
        source_obj = CRS.from_user_input(source_name)
        target_obj = CRS.from_user_input(target_crs)
        transformer = Transformer.from_crs(source_obj, target_obj, always_xy=True)
    except (ProjError, ValueError) as exc:
        raise GISProcessingError("ERROR_GIS_CRS_UNSUPPORTED", str(exc)) from exc
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list) or not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_CRS_FEATURES", "All GeoJSON features must be objects.")
    output_features: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        if geometry is None:
            output_features.append(deepcopy(feature))
            receipts.append({"feature_index": index, "feature_id": _feature_id(feature, index), "transform_state": "NULL_EMPTY"})
            continue
        if not isinstance(geometry, dict) or "coordinates" not in geometry:
            raise GISProcessingError("ERROR_GIS_CRS_GEOMETRY", f"Feature {index} has unsupported geometry.")
        dims = _coordinate_dimension_summary(geometry)
        if dims["has_m"]:
            raise GISProcessingError("ERROR_GIS_CRS_M_UNSUPPORTED", "M coordinates require an explicit semantic transform policy.")
        transformed = deepcopy(feature)
        try:
            transformed["geometry"]["coordinates"] = _transform_coordinates(geometry["coordinates"], transformer)
        except ProjError as exc:
            raise GISProcessingError("ERROR_GIS_CRS_TRANSFORM", str(exc)) from exc
        output_features.append(transformed)
        receipts.append({
            "feature_index": index,
            "feature_id": _feature_id(feature, index),
            "transform_state": "TRANSFORMED" if source_obj != target_obj else "NOOP",
            "coordinate_dimension": dims["coordinate_dimension"],
            "has_z": dims["has_z"],
        })
    created_at = utc_now()
    report_id = stable_id("gisreport", "crs_engine", source.sha256, source_obj.to_string(), target_obj.to_string(), created_at)
    output_payload = deepcopy(source.payload)
    output_payload["features"] = output_features
    output_payload["crs"] = {"type": "name", "properties": {"name": target_obj.to_string()}}
    output = _write_geojson_output(cfg, report_id, output_payload)
    pipeline = transformer.definition or transformer.description
    pipeline_sha256 = hashlib.sha256(pipeline.encode("utf-8")).hexdigest()
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_CRS_REPROJECT_OK",
        "operation": "reproject_geojson_crs",
        "created_at": created_at,
        "parameters": {
            "adapter": CRS_ADAPTER,
            "source_crs": source_obj.to_string(),
            "target_crs": target_obj.to_string(),
            "always_xy": True,
            "axis_order_policy": "traditional_gis_xy",
            "transformer_description": transformer.description,
            "transformer_definition": pipeline,
            "transformer_definition_sha256": pipeline_sha256,
            "accuracy": transformer.accuracy,
        },
        "source": {"path": str(path), "sha256": source.sha256, "declared_crs": source.crs, "feature_count": len(raw_features)},
        "output": {"path": output["path"], "sha256": output["sha256"], "crs": target_obj.to_string(), "feature_count": len(output_features)},
        "features": receipts,
        "invariants": {
            "feature_row_conservation": len(raw_features) == len(output_features) == len(receipts),
            "source_crs_parseable": True,
            "target_crs_parseable": True,
            "axis_order_explicit": True,
            "transform_pipeline_frozen": bool(pipeline),
        },
        "warnings": ["GIS_CRS_Z_PASSTHROUGH_UNTRANSFORMED"] if any(row.get("has_z") for row in receipts) else [],
        "limitations": [
            "XY coordinates are transformed with pyproj/PROJ using always_xy=True",
            "Z values are preserved but not vertically transformed",
            "M coordinates fail closed",
            "grid availability and transformer accuracy remain environment-dependent and are exposed in the receipt",
        ],
        "falsification_criteria": [
            "reject if feature_row_conservation is false",
            "reject broad CRS claims if transformer definition or axis-order policy is absent",
            "independently verify material transformations when grid availability or accuracy differs between runtimes",
        ],
    }
    return _write_report(cfg, report)
