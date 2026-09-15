from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry import mapping as shapely_mapping
from shapely.geometry import shape as shapely_shape

from .config import Settings, settings
from .gis_processing import (
    GISProcessingError,
    GIS_REPORT_SCHEMA_VERSION,
    _classify_geometry,
    _coordinate_dimension_summary,
    _feature_id,
    _load_geojson,
    _write_geojson_output,
    _write_report,
)
from .utils import stable_id, utc_now

TOPOLOGY_REPAIR_ADAPTER = "shapely_make_valid_v1"
SUPPORTED_REPAIR_GEOMETRY_TYPES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}


def _geometry_sha256(geometry: Any) -> str:
    import json
    return hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def repair_geojson_topology(path: Path, *, cfg: Settings = settings) -> dict[str, Any]:
    """Explicitly repair invalid planar XY topology; inspection never calls this function implicitly."""
    cfg.ensure_dirs()
    source = _load_geojson(path)
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list) or not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_REPAIR_FEATURES", "All GeoJSON features must be objects.")

    output_features: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    states = {"UNCHANGED_VALID": 0, "REPAIRED": 0, "NULL_EMPTY": 0, "UNRESOLVED": 0, "UNSUPPORTED": 0}

    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
        row = {
            "feature_index": index,
            "feature_id": _feature_id(feature, index),
            "source_geometry_type": classification["geometry_type"],
            "source_geometry_status": classification["geometry_status"],
            "source_geometry_sha256": _geometry_sha256(geometry),
            "output_geometry_type": None,
            "output_geometry_sha256": None,
            "repair_state": "UNRESOLVED",
            "adapter": TOPOLOGY_REPAIR_ADAPTER,
            "warnings": list(classification["warnings"]),
        }
        if classification["geometry_status"] == "NULL_EMPTY":
            row["repair_state"] = "NULL_EMPTY"
            states["NULL_EMPTY"] += 1
            output_features.append(deepcopy(feature))
            receipts.append(row)
            continue
        if not isinstance(geometry, dict) or classification["geometry_type"] not in SUPPORTED_REPAIR_GEOMETRY_TYPES:
            row["repair_state"] = "UNSUPPORTED"
            states["UNSUPPORTED"] += 1
            receipts.append(row)
            continue
        if dimensions["has_z"] or dimensions["has_m"]:
            row["warnings"] = ["GIS_REPAIR_COORDINATE_DIMENSION_UNSUPPORTED"]
            states["UNRESOLVED"] += 1
            receipts.append(row)
            continue
        try:
            shaped = shapely_shape(geometry)
            repaired = shaped if shaped.is_valid else make_valid(shaped)
        except (AttributeError, ShapelyError, TypeError, ValueError) as exc:
            row["warnings"] = ["ERROR_GIS_REPAIR_RUNTIME"]
            row["error_detail"] = str(exc)
            states["UNRESOLVED"] += 1
            receipts.append(row)
            continue
        if repaired.is_empty or not repaired.is_valid:
            row["warnings"] = ["GIS_REPAIR_RESULT_INVALID_OR_EMPTY"]
            states["UNRESOLVED"] += 1
            receipts.append(row)
            continue
        if repaired.geom_type not in SUPPORTED_REPAIR_GEOMETRY_TYPES:
            row["warnings"] = [f"GIS_REPAIR_OUTPUT_UNSUPPORTED:{repaired.geom_type}"]
            states["UNRESOLVED"] += 1
            receipts.append(row)
            continue
        output_geometry = shapely_mapping(repaired)
        # mapping() may return tuples; JSON serialization converts them losslessly to arrays.
        import json
        output_geometry = json.loads(json.dumps(output_geometry))
        retained = deepcopy(feature)
        retained["geometry"] = output_geometry
        output_features.append(retained)
        state = "UNCHANGED_VALID" if shaped.is_valid and shaped.equals(repaired) else "REPAIRED"
        states[state] += 1
        row.update({
            "repair_state": state,
            "output_geometry_type": repaired.geom_type,
            "output_geometry_sha256": _geometry_sha256(output_geometry),
            "warnings": [],
        })
        receipts.append(row)

    created_at = utc_now()
    report_id = stable_id("gisreport", "topology_repair", source.sha256, created_at)
    output_payload = deepcopy(source.payload)
    output_payload["features"] = output_features
    output = _write_geojson_output(cfg, report_id, output_payload)
    retained_count = states["UNCHANGED_VALID"] + states["REPAIRED"] + states["NULL_EMPTY"]
    excluded_count = states["UNRESOLVED"] + states["UNSUPPORTED"]
    warnings = [name for name, count in (("GIS_REPAIR_UNRESOLVED", states["UNRESOLVED"]), ("GIS_REPAIR_UNSUPPORTED", states["UNSUPPORTED"])) if count]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_TOPOLOGY_REPAIR_OK" if not warnings else "GIS_TOPOLOGY_REPAIR_WARN",
        "operation": "repair_geojson_topology",
        "created_at": created_at,
        "parameters": {"adapter": TOPOLOGY_REPAIR_ADAPTER, "mode": "explicit_planar_xy"},
        "source": {"path": str(path), "sha256": source.sha256, "crs": source.crs, "feature_count": len(raw_features)},
        "output": {"path": output["path"], "sha256": output["sha256"], "crs": source.crs, "feature_count": len(output_features)},
        "features": receipts,
        "state_counts": states,
        "invariants": {
            "input_feature_count": len(raw_features),
            "output_feature_count": len(output_features),
            "retained_feature_count": retained_count,
            "excluded_feature_count": excluded_count,
            "row_accounting_closed": len(raw_features) == retained_count + excluded_count,
            "output_accounting_closed": len(output_features) == retained_count,
            "crs_preserved": True,
            "repair_is_explicit": True,
        },
        "warnings": warnings,
        "limitations": ["repair is planar XY", "Z/M geometries remain unresolved", "geometry type may legitimately change after make_valid"],
        "falsification_criteria": [
            "reject if row or output accounting does not close",
            "reject if source/output hashes do not match saved artifacts",
            "reject any claim that inspect_geojson silently repairs topology",
        ],
    }
    return _write_report(cfg, report)
