from __future__ import annotations

import csv
import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shapely.errors import ShapelyError
from shapely.geometry import box as shapely_box
from shapely.geometry import mapping as shapely_mapping
from shapely.geometry import shape as shapely_shape

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json

GIS_REPORT_SCHEMA_VERSION = "mucaro_gis_report_v1"
GIS_REPORT_DIR = "gis_reports"
DEFAULT_CRS = "EPSG:4326"
EPSILON = 1e-9
WEB_MERCATOR_RADIUS = 6378137.0
WEB_MERCATOR_MAX_LATITUDE = 85.0511287798066
WEB_MERCATOR_MAX_EXTENT = 20037508.342789244
EARTH_MEAN_RADIUS_METERS = 6371008.8
SUPPORTED_REPROJECT_CRS = {"EPSG:4326", "EPSG:3857"}
SUPPORTED_REPROJECT_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
SUPPORTED_BBOX_FILTER_MODES = {"intersects", "within"}
SUPPORTED_CLIP_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
SUPPORTED_OVERLAY_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}
SUPPORTED_OVERLAY_OPERATIONS = {"difference", "intersection", "symmetric_difference", "union"}
SUPPORTED_TOPOLOGY_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
SUPPORTED_MEASURE_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
SUPPORTED_MEASUREMENT_MODES = {"planar", "geodesic"}


class GISProcessingError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class GeoJSONSource:
    path: Path
    sha256: str
    payload: dict[str, Any]
    crs: str
    crs_status: str


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report_path(cfg: Settings, report_id: str) -> Path:
    return cfg.export_dir / GIS_REPORT_DIR / f"{report_id}.json"


def _load_geojson(path: Path) -> GeoJSONSource:
    if not path.exists() or not path.is_file():
        raise GISProcessingError("ERROR_GIS_INPUT_NOT_FOUND", f"GeoJSON input not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GISProcessingError("ERROR_GIS_INVALID_JSON", f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise GISProcessingError("ERROR_GIS_UNSUPPORTED_GEOJSON", "Only GeoJSON FeatureCollection inputs are supported.")
    crs_payload = payload.get("crs")
    crs = DEFAULT_CRS
    crs_status = "DEFAULTED_EPSG4326"
    if isinstance(crs_payload, dict):
        properties = crs_payload.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("name"), str) and properties["name"].strip():
            crs = properties["name"].strip()
            crs_status = "DECLARED"
    return GeoJSONSource(path=path, sha256=_sha256_file(path), payload=payload, crs=crs, crs_status=crs_status)


def _features(source: GeoJSONSource) -> list[dict[str, Any]]:
    features = source.payload.get("features")
    if not isinstance(features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    return [feature for feature in features if isinstance(feature, dict)]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_position(value: Any) -> bool:
    return isinstance(value, list) and len(value) >= 2 and _is_number(value[0]) and _is_number(value[1])


def _coordinates_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return not value or all(_coordinates_empty(item) for item in value)
    return False


def _iter_positions(value: Any) -> list[tuple[float, float]]:
    if _is_position(value):
        return [(float(value[0]), float(value[1]))]
    if isinstance(value, list):
        positions: list[tuple[float, float]] = []
        for item in value:
            positions.extend(_iter_positions(item))
        return positions
    return []


def _iter_position_dimensions(value: Any) -> list[int]:
    if _is_position(value):
        return [len(value)]
    if isinstance(value, list):
        dimensions: list[int] = []
        for item in value:
            dimensions.extend(_iter_position_dimensions(item))
        return dimensions
    return []


def _coordinate_dimension_summary(geometry: dict[str, Any] | None) -> dict[str, Any]:
    if not geometry:
        return {"coordinate_dimension": "EMPTY", "dimension_counts": {}, "has_z": False, "has_m": False}
    dimensions = _iter_position_dimensions(geometry.get("coordinates"))
    counts: dict[str, int] = {}
    for dimension in dimensions:
        label = {2: "XY", 3: "XYZ", 4: "XYZM"}.get(dimension, f"{dimension}D")
        counts[label] = counts.get(label, 0) + 1
    labels = sorted(counts)
    if not labels:
        coordinate_dimension = "EMPTY"
    elif len(labels) == 1:
        coordinate_dimension = labels[0]
    else:
        coordinate_dimension = "MIXED:" + ",".join(labels)
    return {
        "coordinate_dimension": coordinate_dimension,
        "dimension_counts": counts,
        "has_z": any(dimension >= 3 for dimension in dimensions),
        "has_m": any(dimension >= 4 for dimension in dimensions),
    }


def _bbox_for_geometry(geometry: dict[str, Any] | None) -> list[float] | None:
    if not geometry:
        return None
    positions = _iter_positions(geometry.get("coordinates"))
    if not positions:
        return None
    xs = [point[0] for point in positions]
    ys = [point[1] for point in positions]
    return [min(xs), min(ys), max(xs), max(ys)]


def _validate_bbox(values: tuple[Any, Any, Any, Any]) -> list[float]:
    try:
        coordinates = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise GISProcessingError("ERROR_GIS_BBOX_INVALID", "Bounding-box coordinates must be numbers.") from exc
    min_x, min_y, max_x, max_y = coordinates
    if not all(math.isfinite(value) for value in coordinates):
        raise GISProcessingError("ERROR_GIS_BBOX_INVALID", "Bounding-box coordinates must be finite numbers.")
    if min_x > max_x or min_y > max_y:
        raise GISProcessingError("ERROR_GIS_BBOX_INVALID", "Bounding-box minimums must be less than or equal to maximums.")
    return [min_x, min_y, max_x, max_y]


def _bbox_intersects(left: list[float], right: list[float]) -> bool:
    return left[0] <= right[2] and left[2] >= right[0] and left[1] <= right[3] and left[3] >= right[1]


def _bbox_within(inner: list[float], outer: list[float]) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _ring_valid(ring: Any) -> bool:
    return (
        isinstance(ring, list)
        and len(ring) >= 4
        and all(_is_position(position) for position in ring)
        and ring[0][0] == ring[-1][0]
        and ring[0][1] == ring[-1][1]
    )


def _multipoint_valid(coordinates: Any) -> bool:
    return isinstance(coordinates, list) and bool(coordinates) and all(_is_position(position) for position in coordinates)


def _line_valid(coordinates: Any) -> bool:
    return isinstance(coordinates, list) and len(coordinates) >= 2 and all(_is_position(position) for position in coordinates)


def _multiline_valid(coordinates: Any) -> bool:
    return isinstance(coordinates, list) and bool(coordinates) and all(_line_valid(line) for line in coordinates)


def _polygon_valid(coordinates: Any) -> bool:
    return isinstance(coordinates, list) and bool(coordinates) and all(_ring_valid(ring) for ring in coordinates)


def _classify_geometry(geometry: Any) -> dict[str, Any]:
    if geometry is None:
        return {"geometry_type": None, "geometry_status": "NULL_EMPTY", "warnings": ["GEOMETRY_NULL"]}
    if not isinstance(geometry, dict):
        return {"geometry_type": None, "geometry_status": "INVALID", "warnings": ["GEOMETRY_NOT_OBJECT"]}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(geometry_type, str):
        return {"geometry_type": None, "geometry_status": "INVALID", "warnings": ["GEOMETRY_TYPE_MISSING"]}
    if _coordinates_empty(coordinates):
        return {"geometry_type": geometry_type, "geometry_status": "NULL_EMPTY", "warnings": ["GEOMETRY_COORDINATES_EMPTY"]}
    if geometry_type == "Point":
        valid = _is_position(coordinates)
    elif geometry_type == "MultiPoint":
        valid = _multipoint_valid(coordinates)
    elif geometry_type == "LineString":
        valid = _line_valid(coordinates)
    elif geometry_type == "MultiLineString":
        valid = _multiline_valid(coordinates)
    elif geometry_type == "Polygon":
        valid = _polygon_valid(coordinates)
    elif geometry_type == "MultiPolygon":
        valid = isinstance(coordinates, list) and all(_polygon_valid(polygon) for polygon in coordinates)
    else:
        return {"geometry_type": geometry_type, "geometry_status": "UNSUPPORTED", "warnings": [f"GEOMETRY_UNSUPPORTED:{geometry_type}"]}
    return {
        "geometry_type": geometry_type,
        "geometry_status": "VALID" if valid else "INVALID",
        "warnings": [] if valid else [f"GEOMETRY_INVALID:{geometry_type}"],
    }


def _feature_id(feature: dict[str, Any], index: int) -> str:
    raw_id = feature.get("id")
    return str(raw_id) if raw_id is not None and raw_id != "" else f"feature_{index + 1}"


def _inspect_features(source: GeoJSONSource) -> tuple[list[dict[str, Any]], dict[str, int]]:
    inspected: list[dict[str, Any]] = []
    geometry_counts: dict[str, int] = {}
    for index, feature in enumerate(_features(source)):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        topology = _inspect_topology_state(geometry, classification)
        geometry_type = classification["geometry_type"] or "NULL"
        geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        warnings = list(classification["warnings"])
        if topology["topology_warning"]:
            warnings.append(str(topology["topology_warning"]))
        inspected.append(
            {
                "feature_index": index,
                "feature_id": _feature_id(feature, index),
                "geometry_type": classification["geometry_type"],
                "geometry_status": classification["geometry_status"],
                "topology_state": topology["topology_state"],
                "topology_adapter": topology["topology_adapter"],
                "topology_warning": topology["topology_warning"],
                **_coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None),
                "bbox": _bbox_for_geometry(geometry) if isinstance(geometry, dict) else None,
                "property_keys": sorted(str(key) for key in properties),
                "warnings": warnings,
            }
        )
    return inspected, geometry_counts


def _write_report(cfg: Settings, report: dict[str, Any]) -> dict[str, Any]:
    path = _report_path(cfg, str(report["report_id"]))
    write_json(path, report)
    report_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return {**report, "report_path": str(path), "report_sha256": report_sha256}


def _write_geojson_output(cfg: Settings, report_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    path = cfg.export_dir / GIS_REPORT_DIR / f"{report_id}.geojson"
    write_json(path, payload)
    return {"path": str(path), "sha256": _sha256_file(path)}


def _parse_csv_coordinate(value: Any, *, label: str, row_number: int) -> float:
    try:
        coordinate = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise GISProcessingError(
            "ERROR_GIS_CSV_INVALID_COORDINATES",
            f"CSV row {row_number} has invalid {label}: {value!r}",
        ) from exc
    if not math.isfinite(coordinate):
        raise GISProcessingError(
            "ERROR_GIS_CSV_INVALID_COORDINATES",
            f"CSV row {row_number} has non-finite {label}: {value!r}",
        )
    return coordinate


def _validate_csv_point_coordinate(lon: float, lat: float, *, crs: str, row_number: int) -> None:
    if _normalize_crs_name(crs) != "EPSG:4326":
        return
    if lon < -180.0 or lon > 180.0:
        raise GISProcessingError(
            "ERROR_GIS_CSV_INVALID_COORDINATES",
            f"CSV row {row_number} longitude outside EPSG:4326 range: {lon}",
        )
    if lat < -90.0 or lat > 90.0:
        raise GISProcessingError(
            "ERROR_GIS_CSV_INVALID_COORDINATES",
            f"CSV row {row_number} latitude outside EPSG:4326 range: {lat}",
        )


def convert_csv_points_to_geojson(
    path: Path,
    *,
    latitude_column: str,
    longitude_column: str,
    cfg: Settings = settings,
    id_column: str | None = None,
    crs: str = DEFAULT_CRS,
    delimiter: str = ",",
    skip_invalid_rows: bool = False,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    if not path.exists() or not path.is_file():
        raise GISProcessingError("ERROR_GIS_INPUT_NOT_FOUND", f"CSV input not found: {path}")
    if len(delimiter) != 1:
        raise GISProcessingError("ERROR_GIS_CSV_DELIMITER_INVALID", "CSV delimiter must be exactly one character.")
    if not latitude_column.strip() or not longitude_column.strip():
        raise GISProcessingError("ERROR_GIS_CSV_REQUIRED_COLUMN_MISSING", "Latitude and longitude column names are required.")
    if not crs.strip():
        raise GISProcessingError("ERROR_GIS_CSV_CRS_INVALID", "CSV point CRS must be non-empty.")

    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
    except UnicodeDecodeError as exc:
        raise GISProcessingError("ERROR_GIS_CSV_ENCODING", f"CSV input is not valid UTF-8: {path}") from exc
    except csv.Error as exc:
        raise GISProcessingError("ERROR_GIS_CSV_INVALID", f"CSV input could not be parsed: {exc}") from exc

    if not headers:
        raise GISProcessingError("ERROR_GIS_CSV_HEADER_MISSING", "CSV input must include a header row.")
    duplicate_headers = sorted({header for header in headers if headers.count(header) > 1})
    if duplicate_headers:
        raise GISProcessingError("ERROR_GIS_CSV_DUPLICATE_HEADERS", f"CSV input has duplicate headers: {', '.join(duplicate_headers)}")
    required_columns = [latitude_column, longitude_column, *([id_column] if id_column else [])]
    missing_columns = [column for column in required_columns if column not in headers]
    if missing_columns:
        raise GISProcessingError("ERROR_GIS_CSV_REQUIRED_COLUMN_MISSING", f"CSV input is missing columns: {', '.join(missing_columns)}")

    features: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        try:
            lat = _parse_csv_coordinate(row.get(latitude_column), label=latitude_column, row_number=index)
            lon = _parse_csv_coordinate(row.get(longitude_column), label=longitude_column, row_number=index)
            _validate_csv_point_coordinate(lon, lat, crs=crs, row_number=index)
        except GISProcessingError as exc:
            if not skip_invalid_rows:
                raise
            skipped_rows.append({"row_number": index, "status": exc.status, "detail": exc.detail})
            continue
        feature_id = str(row.get(id_column) or "").strip() if id_column else ""
        features.append(
            {
                "type": "Feature",
                "id": feature_id or f"row_{index}",
                "properties": {header: row.get(header) for header in headers},
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            }
        )
    if not features:
        raise GISProcessingError("ERROR_GIS_CSV_NO_VALID_POINTS", "CSV conversion produced no valid point features.")

    output_payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs.strip()}},
        "features": features,
    }
    created_at = utc_now()
    source_sha256 = _sha256_file(path)
    report_id = stable_id(
        "gisreport",
        "csv_points",
        str(path),
        source_sha256,
        latitude_column,
        longitude_column,
        id_column or "",
        crs,
        created_at,
    )
    output = _write_geojson_output(cfg, report_id, output_payload)
    warnings = ["GIS_CSV_INVALID_ROWS_SKIPPED"] if skipped_rows else []
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_CSV_POINTS_OK" if not warnings else "GIS_CSV_POINTS_WARN",
        "operation": "csv_points_to_geojson",
        "created_at": created_at,
        "parameters": {
            "latitude_column": latitude_column,
            "longitude_column": longitude_column,
            "id_column": id_column,
            "crs": crs.strip(),
            "delimiter": delimiter,
            "skip_invalid_rows": skip_invalid_rows,
        },
        "source": {
            "path": str(path),
            "sha256": source_sha256,
            "encoding": "utf-8-sig",
            "header": headers,
            "header_count": len(headers),
            "row_count": len(rows),
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": crs.strip(),
            "feature_count": len(features),
        },
        "skipped_rows": skipped_rows,
        "invariants": {
            "required_columns_present": not missing_columns,
            "source_row_count": len(rows),
            "output_feature_count": len(features),
            "skipped_row_count": len(skipped_rows),
            "row_accounting_closed": len(rows) == len(features) + len(skipped_rows),
            "crs": crs.strip(),
            "coordinate_dimension": "XY",
        },
        "warnings": warnings,
        "limitations": [
            "CSV point conversion preserves raw row properties but only creates XY Point geometries",
            "coordinate validation checks numeric longitude and latitude ranges, not geocoding accuracy",
            "source schema interpretation is limited to the provided longitude and latitude column names",
        ],
        "falsification_criteria": [
            "reject the conversion if source_row_count does not equal output_feature_count plus skipped_row_count",
            "reject EPSG:4326 output if any retained longitude or latitude is outside the valid coordinate range",
            "recompute source and output SHA-256 values before using the derived GeoJSON as current evidence",
        ],
    }
    return _write_report(cfg, report)


def filter_geojson_by_bbox(
    path: Path,
    *,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    cfg: Settings = settings,
    mode: str = "intersects",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    normalized_mode = mode.strip().lower()
    if normalized_mode not in SUPPORTED_BBOX_FILTER_MODES:
        raise GISProcessingError(
            "ERROR_GIS_BBOX_FILTER_MODE",
            f"Unsupported bounding-box filter mode: {mode}",
        )
    query_bbox = _validate_bbox((min_x, min_y, max_x, max_y))
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    if not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_BBOX_FEATURES_NOT_OBJECTS", "All GeoJSON features must be objects.")

    retained_features: list[dict[str, Any]] = []
    feature_receipts: list[dict[str, Any]] = []
    retained_count = 0
    excluded_outside_count = 0
    excluded_null_empty_count = 0
    excluded_unresolved_count = 0

    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        geometry_bbox = _bbox_for_geometry(geometry) if isinstance(geometry, dict) else None
        feature_id = _feature_id(feature, index)
        if classification["geometry_status"] == "NULL_EMPTY":
            filter_state = "EXCLUDED_NULL_EMPTY"
            excluded_null_empty_count += 1
        elif classification["geometry_status"] != "VALID" or geometry_bbox is None:
            filter_state = "EXCLUDED_UNRESOLVED"
            excluded_unresolved_count += 1
        else:
            retained = (
                _bbox_intersects(geometry_bbox, query_bbox)
                if normalized_mode == "intersects"
                else _bbox_within(geometry_bbox, query_bbox)
            )
            if retained:
                filter_state = "RETAINED_INTERSECTS" if normalized_mode == "intersects" else "RETAINED_WITHIN"
                retained_features.append(deepcopy(feature))
                retained_count += 1
            else:
                filter_state = "EXCLUDED_OUTSIDE"
                excluded_outside_count += 1
        feature_receipts.append(
            {
                "feature_index": index,
                "feature_id": feature_id,
                "geometry_type": classification["geometry_type"],
                "geometry_status": classification["geometry_status"],
                "source_bbox": geometry_bbox,
                "filter_state": filter_state,
                "warnings": classification["warnings"],
            }
        )

    output_payload = deepcopy(source.payload)
    output_payload["features"] = retained_features
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "bbox_filter",
        source.sha256,
        normalized_mode,
        *[str(value) for value in query_bbox],
        created_at,
    )
    output = _write_geojson_output(cfg, report_id, output_payload)
    warnings = [
        warning
        for warning in [
            "GIS_BBOX_FILTER_EMPTY" if not retained_features else "",
            "GIS_BBOX_FILTER_NULL_EMPTY_EXCLUDED" if excluded_null_empty_count else "",
            "GIS_BBOX_FILTER_UNRESOLVED_EXCLUDED" if excluded_unresolved_count else "",
        ]
        if warning
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_BBOX_FILTER_OK" if not warnings else "GIS_BBOX_FILTER_WARN",
        "operation": "filter_geojson_by_bbox",
        "created_at": created_at,
        "parameters": {
            "bbox": query_bbox,
            "mode": normalized_mode,
        },
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "crs": source.crs,
            "crs_status": source.crs_status,
            "feature_count": len(raw_features),
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": source.crs,
            "feature_count": len(retained_features),
        },
        "features": feature_receipts,
        "state_counts": {
            "RETAINED": retained_count,
            "EXCLUDED_OUTSIDE": excluded_outside_count,
            "EXCLUDED_NULL_EMPTY": excluded_null_empty_count,
            "EXCLUDED_UNRESOLVED": excluded_unresolved_count,
        },
        "invariants": {
            "source_crs": source.crs,
            "output_crs": source.crs,
            "crs_preserved": True,
            "input_feature_count": len(raw_features),
            "retained_feature_count": retained_count,
            "excluded_feature_count": excluded_outside_count + excluded_null_empty_count + excluded_unresolved_count,
            "excluded_outside_count": excluded_outside_count,
            "excluded_null_empty_count": excluded_null_empty_count,
            "excluded_unresolved_count": excluded_unresolved_count,
            "row_accounting_closed": len(raw_features) == retained_count + excluded_outside_count + excluded_null_empty_count + excluded_unresolved_count,
        },
        "warnings": warnings,
        "limitations": [
            "bounding-box filtering uses feature coordinate extents, not robust polygon clipping",
            "intersects mode is inclusive at bbox edges",
            "within mode requires the feature bbox to be fully inside the query bbox",
        ],
        "falsification_criteria": [
            "reject the filter result if input_feature_count does not equal retained plus excluded feature counts",
            "reject the output if any retained feature lacks a RETAINED_* filter state in the receipt",
            "recompute source and output SHA-256 values before using the filtered GeoJSON as current evidence",
        ],
    }
    return _write_report(cfg, report)


def _json_geometry(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_geometry(item) for item in value]
    if isinstance(value, list):
        return [_json_geometry(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_geometry(item) for key, item in value.items()}
    return value


def _shapely_geojson_geometry(
    geometry: dict[str, Any],
    *,
    invalid_status: str = "ERROR_GIS_CLIP_INVALID_GEOMETRY",
    topology_status: str = "ERROR_GIS_CLIP_INVALID_TOPOLOGY",
) -> Any:
    try:
        shaped = shapely_shape(geometry)
    except (AttributeError, ShapelyError, TypeError, ValueError) as exc:
        raise GISProcessingError(invalid_status, f"Geometry could not be loaded by Shapely: {exc}") from exc
    if shaped.is_empty:
        return shaped
    if not shaped.is_valid:
        raise GISProcessingError(topology_status, "Geometry is not topologically valid for this operation.")
    return shaped


def _inspect_topology_state(geometry: Any, classification: dict[str, Any]) -> dict[str, Any]:
    geometry_type = classification.get("geometry_type")
    geometry_status = classification.get("geometry_status")
    if geometry_status == "NULL_EMPTY":
        return {"topology_state": "NULL_EMPTY", "topology_adapter": None, "topology_warning": None}
    if geometry_status != "VALID" or not isinstance(geometry, dict):
        return {
            "topology_state": "UNRESOLVED",
            "topology_adapter": None,
            "topology_warning": "GIS_TOPOLOGY_SYNTAX_UNRESOLVED",
        }
    if geometry_type not in SUPPORTED_TOPOLOGY_GEOMETRY_TYPES:
        return {
            "topology_state": "UNSUPPORTED",
            "topology_adapter": None,
            "topology_warning": f"GIS_TOPOLOGY_UNSUPPORTED_GEOMETRY:{geometry_type}",
        }
    dimensions = _coordinate_dimension_summary(geometry)
    dimension_warning = "GIS_TOPOLOGY_PLANAR_XY_ONLY" if dimensions["has_z"] or dimensions["has_m"] else None
    if dimensions["has_m"]:
        return {
            "topology_state": "UNRESOLVED",
            "topology_adapter": "shapely_is_valid_v1",
            "topology_warning": "GIS_TOPOLOGY_COORDINATE_DIMENSION_UNSUPPORTED",
        }
    try:
        _shapely_geojson_geometry(
            geometry,
            invalid_status="ERROR_GIS_INSPECT_TOPOLOGY_INVALID_GEOMETRY",
            topology_status="ERROR_GIS_INSPECT_TOPOLOGY_INVALID",
        )
    except GISProcessingError as exc:
        return {
            "topology_state": "INVALID" if exc.status == "ERROR_GIS_INSPECT_TOPOLOGY_INVALID" else "UNRESOLVED",
            "topology_adapter": "shapely_is_valid_v1",
            "topology_warning": exc.status,
        }
    return {
        "topology_state": "VALID_PLANAR_XY" if dimension_warning else "VALID",
        "topology_adapter": "shapely_is_valid_v1",
        "topology_warning": dimension_warning,
    }


def clip_geojson_by_bbox(
    path: Path,
    *,
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    cfg: Settings = settings,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    query_bbox = _validate_bbox((min_x, min_y, max_x, max_y))
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    if not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_CLIP_FEATURES_NOT_OBJECTS", "All GeoJSON features must be objects.")

    clip_bounds = shapely_box(*query_bbox)
    output_features: list[dict[str, Any]] = []
    feature_receipts: list[dict[str, Any]] = []
    state_counts = {
        "RETAINED_UNCHANGED": 0,
        "RETAINED_CLIPPED": 0,
        "EXCLUDED_OUTSIDE": 0,
        "EXCLUDED_NULL_EMPTY": 0,
        "EXCLUDED_UNRESOLVED": 0,
    }
    source_coordinate_count = 0
    output_coordinate_count = 0

    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        feature_id = _feature_id(feature, index)
        source_bbox = _bbox_for_geometry(geometry) if isinstance(geometry, dict) else None
        source_count = len(_iter_positions(geometry.get("coordinates") if isinstance(geometry, dict) else None))
        source_coordinate_count += source_count
        dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
        row = {
            "feature_index": index,
            "feature_id": feature_id,
            "source_geometry_type": classification["geometry_type"],
            "source_geometry_status": classification["geometry_status"],
            "source_bbox": source_bbox,
            "output_geometry_type": None,
            "output_bbox": None,
            "source_coordinate_count": source_count,
            "output_coordinate_count": 0,
            "coordinate_dimension": dimensions["coordinate_dimension"],
            "clip_state": "EXCLUDED_UNRESOLVED",
            "warnings": list(classification["warnings"]),
        }
        if classification["geometry_status"] == "NULL_EMPTY":
            row["clip_state"] = "EXCLUDED_NULL_EMPTY"
            state_counts["EXCLUDED_NULL_EMPTY"] += 1
            feature_receipts.append(row)
            continue
        if not isinstance(geometry, dict) or not isinstance(geometry.get("type"), str):
            row["warnings"] = row["warnings"] or ["GEOMETRY_INVALID"]
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        if geometry["type"] not in SUPPORTED_CLIP_GEOMETRY_TYPES:
            row["warnings"] = [f"GIS_CLIP_UNSUPPORTED_GEOMETRY:{geometry['type']}"]
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        if dimensions["has_z"] or dimensions["has_m"]:
            row["warnings"] = ["GIS_CLIP_COORDINATE_DIMENSION_UNSUPPORTED"]
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        try:
            source_geometry = _shapely_geojson_geometry(geometry)
            clipped = source_geometry.intersection(clip_bounds)
        except GISProcessingError as exc:
            row["warnings"] = [exc.status]
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        except (AttributeError, ShapelyError, TypeError, ValueError) as exc:
            row["warnings"] = ["ERROR_GIS_CLIP_RUNTIME"]
            row["error_detail"] = str(exc)
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue

        if clipped.is_empty:
            row["clip_state"] = "EXCLUDED_OUTSIDE"
            row["warnings"] = []
            state_counts["EXCLUDED_OUTSIDE"] += 1
            feature_receipts.append(row)
            continue
        if clipped.geom_type not in SUPPORTED_CLIP_GEOMETRY_TYPES:
            row["warnings"] = [f"GIS_CLIP_OUTPUT_UNSUPPORTED_GEOMETRY:{clipped.geom_type}"]
            state_counts["EXCLUDED_UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue

        output_geometry = _json_geometry(shapely_mapping(clipped))
        output_count = len(_iter_positions(output_geometry.get("coordinates") if isinstance(output_geometry, dict) else None))
        output_coordinate_count += output_count
        output_bbox = _bbox_for_geometry(output_geometry) if isinstance(output_geometry, dict) else None
        clip_state = "RETAINED_UNCHANGED" if source_geometry.equals(clipped) else "RETAINED_CLIPPED"
        retained_feature = deepcopy(feature)
        retained_feature["geometry"] = output_geometry
        output_features.append(retained_feature)
        row.update(
            {
                "clip_state": clip_state,
                "output_geometry_type": output_geometry.get("type") if isinstance(output_geometry, dict) else None,
                "output_bbox": output_bbox,
                "output_coordinate_count": output_count,
                "warnings": [],
            }
        )
        state_counts[clip_state] += 1
        feature_receipts.append(row)

    output_payload = deepcopy(source.payload)
    output_payload["features"] = output_features
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "clip_bbox",
        source.sha256,
        *[str(value) for value in query_bbox],
        created_at,
    )
    output = _write_geojson_output(cfg, report_id, output_payload)
    unresolved_count = state_counts["EXCLUDED_UNRESOLVED"]
    null_empty_count = state_counts["EXCLUDED_NULL_EMPTY"]
    retained_count = state_counts["RETAINED_UNCHANGED"] + state_counts["RETAINED_CLIPPED"]
    warnings = [
        warning
        for warning in [
            "GIS_CLIP_BBOX_EMPTY" if retained_count == 0 else "",
            "GIS_CLIP_BBOX_NULL_EMPTY_EXCLUDED" if null_empty_count else "",
            "GIS_CLIP_BBOX_UNRESOLVED_EXCLUDED" if unresolved_count else "",
        ]
        if warning
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_CLIP_BBOX_OK" if not warnings else "GIS_CLIP_BBOX_WARN",
        "operation": "clip_geojson_by_bbox",
        "created_at": created_at,
        "parameters": {
            "bbox": query_bbox,
            "adapter": "shapely_bbox_intersection_v1",
            "supported_geometry_types": sorted(SUPPORTED_CLIP_GEOMETRY_TYPES),
        },
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "crs": source.crs,
            "crs_status": source.crs_status,
            "feature_count": len(raw_features),
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": source.crs,
            "feature_count": len(output_features),
        },
        "features": feature_receipts,
        "state_counts": state_counts,
        "invariants": {
            "crs": source.crs,
            "input_feature_count": len(raw_features),
            "output_feature_count": len(output_features),
            "retained_feature_count": retained_count,
            "clipped_feature_count": state_counts["RETAINED_CLIPPED"],
            "unchanged_feature_count": state_counts["RETAINED_UNCHANGED"],
            "excluded_feature_count": state_counts["EXCLUDED_OUTSIDE"] + null_empty_count + unresolved_count,
            "excluded_outside_count": state_counts["EXCLUDED_OUTSIDE"],
            "excluded_null_empty_count": null_empty_count,
            "excluded_unresolved_count": unresolved_count,
            "row_accounting_closed": len(raw_features) == retained_count + state_counts["EXCLUDED_OUTSIDE"] + null_empty_count + unresolved_count,
            "source_coordinate_count": source_coordinate_count,
            "output_coordinate_count": output_coordinate_count,
            "crs_preserved": True,
        },
        "warnings": warnings,
        "limitations": [
            "bbox clipping uses a local Shapely intersection adapter against an axis-aligned bbox",
            "Z and M coordinate dimensions are not clipped in this adapter and are recorded as unresolved residue",
            "this command does not reproject inputs, repair invalid topology, or perform non-bbox overlay analysis",
        ],
        "falsification_criteria": [
            "reject the clipped output if row_accounting_closed or crs_preserved is false",
            "reject clipped geometry claims if unresolved residue exists inside the claimed scope",
            "validate material clipping results against an independent GIS engine before legal-boundary or engineering use",
        ],
    }
    return _write_report(cfg, report)


def _line_length(coordinates: list[Any]) -> float:
    if len(coordinates) < 2 or not all(_is_position(position) for position in coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "LineString coordinates must contain at least two valid positions.")
    return sum(math.dist(_xy(start), _xy(end)) for start, end in zip(coordinates, coordinates[1:]))


def _multiline_length(coordinates: list[Any]) -> float:
    if not coordinates or not all(isinstance(line, list) for line in coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiLineString coordinates must contain at least one line.")
    return sum(_line_length(line) for line in coordinates)


def _ring_signed_area(ring: list[Any]) -> float:
    if not _ring_valid(ring):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon rings must be closed and contain at least four positions.")
    total = 0.0
    for start, end in zip(ring, ring[1:]):
        x1, y1 = _xy(start)
        x2, y2 = _xy(end)
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _polygon_measurements(coordinates: list[Any]) -> dict[str, Any]:
    if not _polygon_valid(coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon coordinates must contain valid closed rings.")
    outer_area = abs(_ring_signed_area(coordinates[0]))
    hole_areas = [abs(_ring_signed_area(ring)) for ring in coordinates[1:]]
    perimeter = sum(_line_length(ring) for ring in coordinates)
    area = outer_area - sum(hole_areas)
    warnings = ["GIS_MEASURE_HOLE_AREA_EXCEEDS_OUTER"] if area < -EPSILON else []
    return {
        "area": round(area, 12),
        "length": round(perimeter, 12),
        "ring_count": len(coordinates),
        "hole_count": max(len(coordinates) - 1, 0),
        "warnings": warnings,
    }


def _normalize_measurement_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode not in SUPPORTED_MEASUREMENT_MODES:
        raise GISProcessingError(
            "ERROR_GIS_MEASURE_MODE_UNSUPPORTED",
            f"Unsupported measurement mode: {value}",
        )
    return mode


def _lonlat_degrees(position: Any) -> tuple[float, float]:
    if not _is_position(position):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Geodesic coordinates must be valid longitude/latitude positions.")
    lon, lat = float(position[0]), float(position[1])
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_COORDINATES", "Geodesic coordinates must be finite.")
    if lon < -180.0 or lon > 180.0:
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_COORDINATES", f"Longitude outside EPSG:4326 range: {lon}")
    if lat < -90.0 or lat > 90.0:
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_COORDINATES", f"Latitude outside EPSG:4326 range: {lat}")
    return lon, lat


def _normalize_longitude_delta_radians(delta: float) -> float:
    while delta > math.pi:
        delta -= 2.0 * math.pi
    while delta < -math.pi:
        delta += 2.0 * math.pi
    return delta


def _geodesic_segment_length(start: Any, end: Any) -> float:
    lon1, lat1 = _lonlat_degrees(start)
    lon2, lat2 = _lonlat_degrees(end)
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = lat2_rad - lat1_rad
    delta_lon = _normalize_longitude_delta_radians(math.radians(lon2 - lon1))
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2.0) ** 2
    )
    angular_distance = 2.0 * math.atan2(math.sqrt(min(1.0, haversine)), math.sqrt(max(0.0, 1.0 - haversine)))
    return EARTH_MEAN_RADIUS_METERS * angular_distance


def _geodesic_line_length(coordinates: list[Any]) -> float:
    if len(coordinates) < 2 or not all(_is_position(position) for position in coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "LineString coordinates must contain at least two valid positions.")
    return sum(_geodesic_segment_length(start, end) for start, end in zip(coordinates, coordinates[1:]))


def _geodesic_multiline_length(coordinates: list[Any]) -> float:
    if not coordinates or not all(isinstance(line, list) for line in coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiLineString coordinates must contain at least one line.")
    return sum(_geodesic_line_length(line) for line in coordinates)


def _geodesic_ring_signed_area(ring: list[Any]) -> float:
    if not _ring_valid(ring):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon rings must be closed and contain at least four positions.")
    total = 0.0
    for start, end in zip(ring, ring[1:]):
        lon1, lat1 = _lonlat_degrees(start)
        lon2, lat2 = _lonlat_degrees(end)
        delta_lon = _normalize_longitude_delta_radians(math.radians(lon2 - lon1))
        total += delta_lon * (2.0 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return (total * EARTH_MEAN_RADIUS_METERS * EARTH_MEAN_RADIUS_METERS) / 2.0


def _geodesic_polygon_measurements(coordinates: list[Any]) -> dict[str, Any]:
    if not _polygon_valid(coordinates):
        raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon coordinates must contain valid closed rings.")
    outer_area = abs(_geodesic_ring_signed_area(coordinates[0]))
    hole_areas = [abs(_geodesic_ring_signed_area(ring)) for ring in coordinates[1:]]
    perimeter = sum(_geodesic_line_length(ring) for ring in coordinates)
    area = outer_area - sum(hole_areas)
    warnings = ["GIS_MEASURE_HOLE_AREA_EXCEEDS_OUTER"] if area < -EPSILON else []
    return {
        "area": round(area, 6),
        "length": round(perimeter, 6),
        "ring_count": len(coordinates),
        "hole_count": max(len(coordinates) - 1, 0),
        "warnings": warnings,
    }


def _geodesic_measure_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        _lonlat_degrees(coordinates)
        return {"area": 0.0, "length": 0.0, "part_count": 1, "ring_count": 0, "hole_count": 0, "warnings": []}
    if geometry_type == "MultiPoint":
        if not isinstance(coordinates, list) or not coordinates or not all(_is_position(position) for position in coordinates):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPoint coordinates must contain valid positions.")
        for position in coordinates:
            _lonlat_degrees(position)
        return {"area": 0.0, "length": 0.0, "part_count": len(coordinates), "ring_count": 0, "hole_count": 0, "warnings": []}
    if geometry_type == "LineString":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "LineString coordinates must be an array.")
        return {
            "area": 0.0,
            "length": round(_geodesic_line_length(coordinates), 6),
            "part_count": 1,
            "ring_count": 0,
            "hole_count": 0,
            "warnings": [],
        }
    if geometry_type == "MultiLineString":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiLineString coordinates must be an array.")
        return {
            "area": 0.0,
            "length": round(_geodesic_multiline_length(coordinates), 6),
            "part_count": len(coordinates),
            "ring_count": 0,
            "hole_count": 0,
            "warnings": [],
        }
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon coordinates must be an array.")
        polygon = _geodesic_polygon_measurements(coordinates)
        return {**polygon, "part_count": 1}
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPolygon coordinates must contain at least one polygon.")
        polygons = [_geodesic_polygon_measurements(polygon) for polygon in coordinates if isinstance(polygon, list)]
        if len(polygons) != len(coordinates):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPolygon members must be polygon coordinate arrays.")
        warnings = sorted({warning for polygon in polygons for warning in polygon["warnings"]})
        return {
            "area": round(sum(float(polygon["area"]) for polygon in polygons), 6),
            "length": round(sum(float(polygon["length"]) for polygon in polygons), 6),
            "part_count": len(polygons),
            "ring_count": sum(int(polygon["ring_count"]) for polygon in polygons),
            "hole_count": sum(int(polygon["hole_count"]) for polygon in polygons),
            "warnings": warnings,
        }
    raise GISProcessingError("ERROR_GIS_MEASURE_UNSUPPORTED_GEOMETRY", f"Unsupported geometry type for geodesic measurement: {geometry_type}")


def _measure_geometry(geometry: dict[str, Any], *, measurement_mode: str = "planar") -> dict[str, Any]:
    if measurement_mode == "geodesic":
        return _geodesic_measure_geometry(geometry)
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        if not _is_position(coordinates):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Point coordinates must be a valid position.")
        return {"area": 0.0, "length": 0.0, "part_count": 1, "ring_count": 0, "hole_count": 0, "warnings": []}
    if geometry_type == "MultiPoint":
        if not isinstance(coordinates, list) or not coordinates or not all(_is_position(position) for position in coordinates):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPoint coordinates must contain valid positions.")
        return {"area": 0.0, "length": 0.0, "part_count": len(coordinates), "ring_count": 0, "hole_count": 0, "warnings": []}
    if geometry_type == "LineString":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "LineString coordinates must be an array.")
        return {
            "area": 0.0,
            "length": round(_line_length(coordinates), 12),
            "part_count": 1,
            "ring_count": 0,
            "hole_count": 0,
            "warnings": [],
        }
    if geometry_type == "MultiLineString":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiLineString coordinates must be an array.")
        return {
            "area": 0.0,
            "length": round(_multiline_length(coordinates), 12),
            "part_count": len(coordinates),
            "ring_count": 0,
            "hole_count": 0,
            "warnings": [],
        }
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "Polygon coordinates must be an array.")
        polygon = _polygon_measurements(coordinates)
        return {**polygon, "part_count": 1}
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPolygon coordinates must contain at least one polygon.")
        polygons = [_polygon_measurements(polygon) for polygon in coordinates if isinstance(polygon, list)]
        if len(polygons) != len(coordinates):
            raise GISProcessingError("ERROR_GIS_MEASURE_INVALID_GEOMETRY", "MultiPolygon members must be polygon coordinate arrays.")
        warnings = sorted({warning for polygon in polygons for warning in polygon["warnings"]})
        return {
            "area": round(sum(float(polygon["area"]) for polygon in polygons), 12),
            "length": round(sum(float(polygon["length"]) for polygon in polygons), 12),
            "part_count": len(polygons),
            "ring_count": sum(int(polygon["ring_count"]) for polygon in polygons),
            "hole_count": sum(int(polygon["hole_count"]) for polygon in polygons),
            "warnings": warnings,
        }
    raise GISProcessingError("ERROR_GIS_MEASURE_UNSUPPORTED_GEOMETRY", f"Unsupported geometry type for measurement: {geometry_type}")


def measure_geojson(
    path: Path,
    *,
    cfg: Settings = settings,
    coordinate_units: str = "source_crs_units",
    measurement_mode: str = "planar",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    if not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_MEASURE_FEATURES_NOT_OBJECTS", "All GeoJSON features must be objects.")
    if not coordinate_units.strip():
        raise GISProcessingError("ERROR_GIS_MEASURE_UNITS_REQUIRED", "Coordinate units label is required.")
    normalized_measurement_mode = _normalize_measurement_mode(measurement_mode)
    normalized_source_crs = _normalize_crs_name(source.crs)
    if normalized_measurement_mode == "geodesic" and normalized_source_crs != "EPSG:4326":
        raise GISProcessingError(
            "ERROR_GIS_MEASURE_GEODESIC_CRS",
            f"Geodesic measurement requires EPSG:4326 coordinates; source CRS is {source.crs}.",
        )
    adapter = (
        "local_spherical_geodesic_v1"
        if normalized_measurement_mode == "geodesic"
        else "local_planar_shoelace_v1"
    )
    area_units = "meters^2" if normalized_measurement_mode == "geodesic" else f"{coordinate_units.strip()}^2"
    length_units = "meters" if normalized_measurement_mode == "geodesic" else coordinate_units.strip()

    feature_measurements: list[dict[str, Any]] = []
    state_counts = {"MEASURED": 0, "NULL_EMPTY": 0, "INVALID": 0, "UNSUPPORTED": 0}
    total_area = 0.0
    total_length = 0.0
    total_coordinate_count = 0
    total_part_count = 0
    total_ring_count = 0
    total_hole_count = 0

    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        feature_id = _feature_id(feature, index)
        coordinate_count = len(_iter_positions(geometry.get("coordinates") if isinstance(geometry, dict) else None))
        total_coordinate_count += coordinate_count
        dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
        row = {
            "feature_index": index,
            "feature_id": feature_id,
            "geometry_type": classification["geometry_type"],
            "geometry_status": classification["geometry_status"],
            "measurement_state": "UNSUPPORTED",
            "bbox": _bbox_for_geometry(geometry) if isinstance(geometry, dict) else None,
            "coordinate_count": coordinate_count,
            "coordinate_dimension": dimensions["coordinate_dimension"],
            "area": None,
            "length": None,
            "part_count": 0,
            "ring_count": 0,
            "hole_count": 0,
            "warnings": list(classification["warnings"]),
        }
        if classification["geometry_status"] == "NULL_EMPTY":
            if isinstance(geometry, dict) and isinstance(geometry.get("type"), str) and geometry["type"] not in SUPPORTED_MEASURE_GEOMETRY_TYPES:
                row["measurement_state"] = "UNSUPPORTED"
                row["geometry_status"] = "UNSUPPORTED"
                row["warnings"] = [f"GEOMETRY_UNSUPPORTED:{geometry['type']}"]
                state_counts["UNSUPPORTED"] += 1
                feature_measurements.append(row)
                continue
            row["measurement_state"] = "NULL_EMPTY"
            state_counts["NULL_EMPTY"] += 1
            feature_measurements.append(row)
            continue
        if not isinstance(geometry, dict) or not isinstance(geometry.get("type"), str):
            row["measurement_state"] = "INVALID"
            state_counts["INVALID"] += 1
            feature_measurements.append(row)
            continue
        if geometry["type"] not in SUPPORTED_MEASURE_GEOMETRY_TYPES:
            row["measurement_state"] = "UNSUPPORTED"
            state_counts["UNSUPPORTED"] += 1
            feature_measurements.append(row)
            continue
        try:
            measurements = _measure_geometry(geometry, measurement_mode=normalized_measurement_mode)
        except GISProcessingError as exc:
            row["measurement_state"] = "INVALID"
            row["warnings"] = [exc.status]
            state_counts["INVALID"] += 1
            feature_measurements.append(row)
            continue
        row.update(
            {
                "measurement_state": "MEASURED",
                "geometry_status": "VALID",
                "area": measurements["area"],
                "length": measurements["length"],
                "part_count": measurements["part_count"],
                "ring_count": measurements["ring_count"],
                "hole_count": measurements["hole_count"],
                "warnings": measurements["warnings"],
            }
        )
        state_counts["MEASURED"] += 1
        total_area += float(measurements["area"])
        total_length += float(measurements["length"])
        total_part_count += int(measurements["part_count"])
        total_ring_count += int(measurements["ring_count"])
        total_hole_count += int(measurements["hole_count"])
        feature_measurements.append(row)

    warnings = [
        warning
        for warning in [
            "GIS_MEASURE_NO_MEASURABLE_FEATURES" if state_counts["MEASURED"] == 0 else "",
            "GIS_MEASURE_NULL_EMPTY_GEOMETRY" if state_counts["NULL_EMPTY"] else "",
            "GIS_MEASURE_INVALID_GEOMETRY" if state_counts["INVALID"] else "",
            "GIS_MEASURE_UNSUPPORTED_GEOMETRY" if state_counts["UNSUPPORTED"] else "",
            "GIS_MEASURE_HOLE_AREA_EXCEEDS_OUTER"
            if any("GIS_MEASURE_HOLE_AREA_EXCEEDS_OUTER" in row["warnings"] for row in feature_measurements)
            else "",
        ]
        if warning
    ]
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "measure",
        str(path),
        source.sha256,
        coordinate_units.strip(),
        normalized_measurement_mode,
        created_at,
    )
    limitations = (
        [
            "geodesic measurements use a local spherical Earth approximation with mean radius, not an ellipsoidal GIS engine",
            "geodesic mode requires EPSG:4326 longitude/latitude degree coordinates and returns area/length in meters",
            "this command does not perform robust overlay, clipping, datum transformation, antimeridian topology repair, or polar-area adjudication",
        ]
        if normalized_measurement_mode == "geodesic"
        else [
            "measurements are planar in the source coordinate units, not geodesic",
            "polygon area uses the shoelace formula and subtracts interior ring area",
            "this command does not perform robust overlay, clipping, datum transformation, or topology repair",
        ]
    )
    falsification_criteria = (
        [
            "reject the measurement if row_accounting_closed is false",
            "reject geodesic meter claims if the source CRS is not EPSG:4326 or retained coordinates are outside longitude/latitude bounds",
            "compare material area or length values against an independent ellipsoidal GIS engine before legal-boundary, engineering, or high-stakes use",
        ]
        if normalized_measurement_mode == "geodesic"
        else [
            "reject the measurement if row_accounting_closed is false",
            "reject area or length claims if the source CRS units label is wrong or unknown for the claim",
            "compare material area or length values against an independent GIS engine before geodesic or legal-boundary use",
        ]
    )
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_MEASURE_OK" if not warnings else "GIS_MEASURE_WARN",
        "operation": "measure_geojson",
        "created_at": created_at,
        "parameters": {
            "coordinate_units": coordinate_units.strip(),
            "measurement_mode": normalized_measurement_mode,
            "area_units": area_units,
            "length_units": length_units,
            "adapter": adapter,
        },
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "crs": source.crs,
            "crs_status": source.crs_status,
            "feature_count": len(raw_features),
        },
        "measurements": feature_measurements,
        "state_counts": state_counts,
        "invariants": {
            "crs": source.crs,
            "feature_count": len(raw_features),
            "measured_feature_count": state_counts["MEASURED"],
            "null_empty_count": state_counts["NULL_EMPTY"],
            "invalid_count": state_counts["INVALID"],
            "unsupported_count": state_counts["UNSUPPORTED"],
            "row_accounting_closed": len(raw_features) == sum(state_counts.values()),
            "total_coordinate_count": total_coordinate_count,
            "total_part_count": total_part_count,
            "total_ring_count": total_ring_count,
            "total_hole_count": total_hole_count,
            "total_area": round(total_area, 12),
            "total_length": round(total_length, 12),
        },
        "warnings": warnings,
        "limitations": limitations,
        "falsification_criteria": falsification_criteria,
    }
    return _write_report(cfg, report)


def inspect_geojson(path: Path, *, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    inspected, geometry_counts = _inspect_features(source)
    invalid_count = sum(1 for feature in inspected if feature["geometry_status"] in {"INVALID", "UNSUPPORTED"})
    null_empty_count = sum(1 for feature in inspected if feature["geometry_status"] == "NULL_EMPTY")
    topology_invalid_count = sum(1 for feature in inspected if feature["topology_state"] == "INVALID")
    topology_unresolved_count = sum(1 for feature in inspected if feature["topology_state"] == "UNRESOLVED")
    topology_planar_xy_only_count = sum(1 for feature in inspected if feature["topology_state"] == "VALID_PLANAR_XY")
    topology_not_applicable_count = sum(1 for feature in inspected if feature["topology_state"] in {"NULL_EMPTY", "UNSUPPORTED"})
    feature_count = len(inspected)
    created_at = utc_now()
    report_id = stable_id("gisreport", "inspect", str(path), source.sha256, created_at)
    warnings = [
        warning
        for warning in [
            "GIS_GEOMETRY_WARNINGS_PRESENT" if invalid_count else "",
            "GIS_TOPOLOGY_WARNINGS_PRESENT" if topology_invalid_count or topology_unresolved_count else "",
            "GIS_TOPOLOGY_PLANAR_XY_ONLY_PRESENT" if topology_planar_xy_only_count else "",
        ]
        if warning
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_INSPECT_OK" if not warnings else "GIS_INSPECT_WARN",
        "operation": "inspect_geojson",
        "created_at": created_at,
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "crs": source.crs,
            "crs_status": source.crs_status,
            "feature_count": feature_count,
        },
        "geometry_counts": geometry_counts,
        "invariants": {
            "feature_count": feature_count,
            "valid_or_null_empty_count": feature_count - invalid_count,
            "invalid_or_unsupported_count": invalid_count,
            "null_empty_count": null_empty_count,
            "topology_valid_count": sum(1 for feature in inspected if feature["topology_state"] in {"VALID", "VALID_PLANAR_XY"}),
            "topology_invalid_count": topology_invalid_count,
            "topology_unresolved_count": topology_unresolved_count,
            "topology_planar_xy_only_count": topology_planar_xy_only_count,
            "topology_not_applicable_count": topology_not_applicable_count,
            "crs_preserved": True,
            "coordinate_dimension_counts": {
                label: sum(
                    int(feature.get("dimension_counts", {}).get(label, 0))
                    for feature in inspected
                    if isinstance(feature.get("dimension_counts"), dict)
                )
                for label in sorted(
                    {
                        str(label)
                        for feature in inspected
                        if isinstance(feature.get("dimension_counts"), dict)
                        for label in feature["dimension_counts"]
                    }
                )
            },
            "features_with_z": sum(1 for feature in inspected if feature.get("has_z")),
            "features_with_m": sum(1 for feature in inspected if feature.get("has_m")),
        },
        "features": inspected,
        "warnings": warnings,
        "limitations": [
            "inspection classifies local GeoJSON structure, coordinate dimensionality, and Shapely planar topology state",
            "topology validation is planar XY and does not prove 3D/M topology semantics",
            "inspection does not repair invalid topology or transform geometries",
        ],
        "falsification_criteria": [
            "reject the inspection if feature_count does not equal the number of inspected feature receipts",
            "reject geometry status conclusions that cannot be reproduced from the saved source SHA-256",
            "reject topology conclusions if topology warnings are omitted or unresolved topology is hidden inside the claimed scope",
        ],
    }
    return _write_report(cfg, report)


def _normalize_crs_name(value: str) -> str:
    return value.strip().upper()


def _require_supported_reprojection(source_crs: str, target_crs: str) -> None:
    if source_crs not in SUPPORTED_REPROJECT_CRS:
        raise GISProcessingError("ERROR_GIS_REPROJECT_UNSUPPORTED_CRS", f"Unsupported source CRS: {source_crs}")
    if target_crs not in SUPPORTED_REPROJECT_CRS:
        raise GISProcessingError("ERROR_GIS_REPROJECT_UNSUPPORTED_CRS", f"Unsupported target CRS: {target_crs}")


def _reproject_xy(x: float, y: float, *, source_crs: str, target_crs: str) -> tuple[float, float]:
    if source_crs == target_crs:
        return x, y
    if not math.isfinite(x) or not math.isfinite(y):
        raise GISProcessingError("ERROR_GIS_REPROJECT_INVALID_COORDINATES", "Coordinate values must be finite numbers.")
    if source_crs == "EPSG:4326" and target_crs == "EPSG:3857":
        if x < -180.0 or x > 180.0:
            raise GISProcessingError("ERROR_GIS_REPROJECT_INVALID_COORDINATES", f"Longitude outside EPSG:4326 range: {x}")
        if y < -WEB_MERCATOR_MAX_LATITUDE or y > WEB_MERCATOR_MAX_LATITUDE:
            raise GISProcessingError(
                "ERROR_GIS_REPROJECT_INVALID_COORDINATES",
                f"Latitude outside Web Mercator supported range: {y}",
            )
        projected_x = WEB_MERCATOR_RADIUS * math.radians(x)
        projected_y = WEB_MERCATOR_RADIUS * math.log(math.tan(math.pi / 4.0 + math.radians(y) / 2.0))
        return (0.0 if abs(projected_x) < EPSILON else projected_x, 0.0 if abs(projected_y) < EPSILON else projected_y)
    if source_crs == "EPSG:3857" and target_crs == "EPSG:4326":
        if abs(x) > WEB_MERCATOR_MAX_EXTENT or abs(y) > WEB_MERCATOR_MAX_EXTENT:
            raise GISProcessingError("ERROR_GIS_REPROJECT_INVALID_COORDINATES", "EPSG:3857 coordinate outside Web Mercator extent.")
        lon = math.degrees(x / WEB_MERCATOR_RADIUS)
        lat = math.degrees(2.0 * math.atan(math.exp(y / WEB_MERCATOR_RADIUS)) - math.pi / 2.0)
        return (0.0 if abs(lon) < EPSILON else lon, 0.0 if abs(lat) < EPSILON else lat)
    raise GISProcessingError(
        "ERROR_GIS_REPROJECT_UNSUPPORTED_CRS",
        f"Unsupported reprojection pair: {source_crs} to {target_crs}",
    )


def _reproject_position(position: Any, *, source_crs: str, target_crs: str) -> list[Any]:
    if not _is_position(position):
        raise GISProcessingError("ERROR_GIS_REPROJECT_INVALID_COORDINATES", "Geometry coordinates contain an invalid position.")
    projected_x, projected_y = _reproject_xy(float(position[0]), float(position[1]), source_crs=source_crs, target_crs=target_crs)
    return [projected_x, projected_y, *position[2:]]


def _reproject_coordinates(value: Any, *, source_crs: str, target_crs: str) -> Any:
    if _is_position(value):
        return _reproject_position(value, source_crs=source_crs, target_crs=target_crs)
    if isinstance(value, list):
        return [_reproject_coordinates(item, source_crs=source_crs, target_crs=target_crs) for item in value]
    raise GISProcessingError("ERROR_GIS_REPROJECT_INVALID_COORDINATES", "Geometry coordinates contain a non-position value.")


def _reproject_geometry(geometry: Any, *, source_crs: str, target_crs: str) -> tuple[Any, str, list[str]]:
    if geometry is None:
        return None, "NULL_EMPTY", ["GEOMETRY_NULL"]
    if not isinstance(geometry, dict):
        raise GISProcessingError("ERROR_GIS_REPROJECT_UNSUPPORTED_GEOMETRY", "Geometry must be an object or null.")
    geometry_type = geometry.get("type")
    if geometry_type not in SUPPORTED_REPROJECT_GEOMETRY_TYPES:
        raise GISProcessingError("ERROR_GIS_REPROJECT_UNSUPPORTED_GEOMETRY", f"Unsupported geometry type: {geometry_type}")
    coordinates = geometry.get("coordinates")
    if _coordinates_empty(coordinates):
        copied = deepcopy(geometry)
        return copied, "NULL_EMPTY", ["GEOMETRY_COORDINATES_EMPTY"]
    copied = deepcopy(geometry)
    copied["coordinates"] = _reproject_coordinates(coordinates, source_crs=source_crs, target_crs=target_crs)
    return copied, "TRANSFORMED" if source_crs != target_crs else "NOOP", []


def reproject_geojson(
    path: Path,
    *,
    target_crs: str,
    cfg: Settings = settings,
    source_crs: str | None = None,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    effective_source_crs = _normalize_crs_name(source_crs) if source_crs is not None else _normalize_crs_name(source.crs)
    normalized_target_crs = _normalize_crs_name(target_crs)
    if not normalized_target_crs:
        raise GISProcessingError("ERROR_GIS_REPROJECT_UNSUPPORTED_CRS", "Target CRS is required.")
    _require_supported_reprojection(effective_source_crs, normalized_target_crs)
    output_payload = deepcopy(source.payload)
    raw_features = output_payload.get("features")
    if not isinstance(raw_features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    if not all(isinstance(feature, dict) for feature in raw_features):
        raise GISProcessingError("ERROR_GIS_REPROJECT_FEATURES_NOT_OBJECTS", "All GeoJSON features must be objects.")

    feature_receipts: list[dict[str, Any]] = []
    source_coordinate_count = 0
    output_coordinate_count = 0
    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        before_dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
        before_coordinate_count = len(_iter_positions(geometry.get("coordinates") if isinstance(geometry, dict) else None))
        reprojected_geometry, transform_state, warnings = _reproject_geometry(
            geometry,
            source_crs=effective_source_crs,
            target_crs=normalized_target_crs,
        )
        feature["geometry"] = reprojected_geometry
        after_dimensions = _coordinate_dimension_summary(reprojected_geometry if isinstance(reprojected_geometry, dict) else None)
        after_coordinate_count = len(_iter_positions(reprojected_geometry.get("coordinates") if isinstance(reprojected_geometry, dict) else None))
        source_coordinate_count += before_coordinate_count
        output_coordinate_count += after_coordinate_count
        feature_receipts.append(
            {
                "feature_index": index,
                "feature_id": _feature_id(feature, index),
                "geometry_type": reprojected_geometry.get("type") if isinstance(reprojected_geometry, dict) else None,
                "transform_state": transform_state,
                "source_coordinate_count": before_coordinate_count,
                "output_coordinate_count": after_coordinate_count,
                "source_coordinate_dimension": before_dimensions["coordinate_dimension"],
                "output_coordinate_dimension": after_dimensions["coordinate_dimension"],
                "coordinate_dimension_preserved": before_dimensions["coordinate_dimension"] == after_dimensions["coordinate_dimension"],
                "has_z": after_dimensions["has_z"],
                "has_m": after_dimensions["has_m"],
                "warnings": warnings,
            }
        )
    output_payload["crs"] = {"type": "name", "properties": {"name": normalized_target_crs}}
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "reproject",
        source.sha256,
        effective_source_crs,
        normalized_target_crs,
        created_at,
    )
    output = _write_geojson_output(cfg, report_id, output_payload)
    null_empty_count = sum(1 for row in feature_receipts if row["transform_state"] == "NULL_EMPTY")
    dimension_mismatch_count = sum(1 for row in feature_receipts if not row["coordinate_dimension_preserved"])
    warnings = [
        warning
        for warning in [
            "GIS_REPROJECT_NOOP" if effective_source_crs == normalized_target_crs else "",
            "GIS_NULL_EMPTY_GEOMETRY_PRESERVED" if null_empty_count else "",
            "GIS_COORDINATE_DIMENSION_MISMATCH" if dimension_mismatch_count else "",
        ]
        if warning
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_REPROJECT_OK" if not warnings else "GIS_REPROJECT_WARN",
        "operation": "reproject_geojson",
        "created_at": created_at,
        "parameters": {
            "source_crs_override": source_crs,
            "effective_source_crs": effective_source_crs,
            "target_crs": normalized_target_crs,
            "adapter": "local_web_mercator_formula_v1",
            "supported_crs": sorted(SUPPORTED_REPROJECT_CRS),
        },
        "input": {
            "path": str(path),
            "sha256": source.sha256,
            "declared_crs": source.crs,
            "declared_crs_status": source.crs_status,
            "feature_count": len(raw_features),
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": normalized_target_crs,
            "feature_count": len(raw_features),
        },
        "features": feature_receipts,
        "invariants": {
            "source_crs": effective_source_crs,
            "target_crs": normalized_target_crs,
            "crs_changed": effective_source_crs != normalized_target_crs,
            "feature_row_conservation": len(raw_features) == len(feature_receipts),
            "source_coordinate_count": source_coordinate_count,
            "output_coordinate_count": output_coordinate_count,
            "coordinate_count_conservation": source_coordinate_count == output_coordinate_count,
            "transformed_feature_count": sum(1 for row in feature_receipts if row["transform_state"] == "TRANSFORMED"),
            "null_empty_count": null_empty_count,
            "coordinate_dimension_mismatch_count": dimension_mismatch_count,
            "features_with_z": sum(1 for row in feature_receipts if row["has_z"]),
            "features_with_m": sum(1 for row in feature_receipts if row["has_m"]),
        },
        "warnings": warnings,
        "limitations": [
            "local reprojection currently supports EPSG:4326 and EPSG:3857 only",
            "datum transformations, grid shifts, axis-order variants, vertical CRS, and time-dependent CRS are not implemented",
            "coordinates outside the Web Mercator supported extent fail closed",
        ],
        "falsification_criteria": [
            "reject the reprojection if feature_row_conservation or coordinate_count_conservation is false",
            "reject unsupported CRS pairs unless a new adapter and fixture update explicitly certify them",
            "compare sampled transformed coordinates against an independent CRS engine before broad GIS claims",
        ],
    }
    return _write_report(cfg, report)


def _xy(position: Any) -> tuple[float, float]:
    return (float(position[0]), float(position[1]))


def _canonical_ring(ring: list[Any]) -> tuple[tuple[float, float], ...] | None:
    if not _ring_valid(ring):
        return None
    coordinates = [_xy(position) for position in ring[:-1]]
    if not coordinates:
        return None
    rotations = [tuple(coordinates[index:] + coordinates[:index]) for index in range(len(coordinates))]
    reversed_coordinates = list(reversed(coordinates))
    rotations.extend(
        tuple(reversed_coordinates[index:] + reversed_coordinates[:index])
        for index in range(len(reversed_coordinates))
    )
    return min(rotations)


def _canonical_polygon(coordinates: Any) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[tuple[float, float], ...], ...]] | None:
    if not _polygon_valid(coordinates):
        return None
    outer = _canonical_ring(coordinates[0])
    if outer is None:
        return None
    holes: list[tuple[tuple[float, float], ...]] = []
    for ring in coordinates[1:]:
        canonical_hole = _canonical_ring(ring)
        if canonical_hole is None:
            return None
        holes.append(canonical_hole)
    return (outer, tuple(sorted(holes)))


def _canonical_geometry(geometry: Any) -> tuple[str, Any] | None:
    classification = _classify_geometry(geometry)
    if classification["geometry_status"] != "VALID" or not isinstance(geometry, dict):
        return None
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return ("Point", _xy(coordinates))
    if geometry_type == "Polygon":
        polygon = _canonical_polygon(coordinates)
        return ("Polygon", polygon) if polygon is not None else None
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        polygons = [_canonical_polygon(polygon) for polygon in coordinates]
        if any(polygon is None for polygon in polygons):
            return None
        return ("MultiPolygon", tuple(sorted(polygon for polygon in polygons if polygon is not None)))
    return None


def _topological_equal(left_geometry: Any, right_geometry: Any) -> bool | None:
    left = _canonical_geometry(left_geometry)
    right = _canonical_geometry(right_geometry)
    if left is None or right is None:
        return None
    return left == right


def _hausdorff_vertex_distance(left_geometry: Any, right_geometry: Any) -> float | None:
    left_positions = _iter_positions(left_geometry.get("coordinates") if isinstance(left_geometry, dict) else None)
    right_positions = _iter_positions(right_geometry.get("coordinates") if isinstance(right_geometry, dict) else None)
    if not left_positions or not right_positions:
        return None
    left_to_right = max(min(math.dist(left, right) for right in right_positions) for left in left_positions)
    right_to_left = max(min(math.dist(right, left) for left in left_positions) for right in right_positions)
    return round(max(left_to_right, right_to_left), 12)


def _vertex_symmetric_difference_count(left_geometry: Any, right_geometry: Any) -> int | None:
    left_positions = _iter_positions(left_geometry.get("coordinates") if isinstance(left_geometry, dict) else None)
    right_positions = _iter_positions(right_geometry.get("coordinates") if isinstance(right_geometry, dict) else None)
    if not left_positions or not right_positions:
        return None
    return len(set(left_positions) ^ set(right_positions))


def _planar_overlay_metrics(left_geometry: Any, right_geometry: Any) -> dict[str, Any]:
    left_type = left_geometry.get("type") if isinstance(left_geometry, dict) else None
    right_type = right_geometry.get("type") if isinstance(right_geometry, dict) else None
    if left_type not in SUPPORTED_OVERLAY_GEOMETRY_TYPES or right_type not in SUPPORTED_OVERLAY_GEOMETRY_TYPES:
        return {
            "planar_overlay_state": "NOT_APPLICABLE",
            "planar_overlay_warning": None,
            "left_area": None,
            "right_area": None,
            "intersection_area": None,
            "symmetric_difference_area": None,
        }
    left_dimensions = _coordinate_dimension_summary(left_geometry)
    right_dimensions = _coordinate_dimension_summary(right_geometry)
    if left_dimensions["has_z"] or left_dimensions["has_m"] or right_dimensions["has_z"] or right_dimensions["has_m"]:
        return {
            "planar_overlay_state": "UNRESOLVED",
            "planar_overlay_warning": "GIS_PLANAR_OVERLAY_DIMENSION_UNSUPPORTED",
            "left_area": None,
            "right_area": None,
            "intersection_area": None,
            "symmetric_difference_area": None,
        }
    try:
        left_shape = _shapely_geojson_geometry(
            left_geometry,
            invalid_status="ERROR_GIS_PLANAR_OVERLAY_INVALID_GEOMETRY",
            topology_status="ERROR_GIS_PLANAR_OVERLAY_INVALID_TOPOLOGY",
        )
        right_shape = _shapely_geojson_geometry(
            right_geometry,
            invalid_status="ERROR_GIS_PLANAR_OVERLAY_INVALID_GEOMETRY",
            topology_status="ERROR_GIS_PLANAR_OVERLAY_INVALID_TOPOLOGY",
        )
        intersection_area = left_shape.intersection(right_shape).area
        symmetric_difference_area = left_shape.symmetric_difference(right_shape).area
    except GISProcessingError as exc:
        return {
            "planar_overlay_state": "UNRESOLVED",
            "planar_overlay_warning": exc.status,
            "left_area": None,
            "right_area": None,
            "intersection_area": None,
            "symmetric_difference_area": None,
        }
    except (AttributeError, ShapelyError, TypeError, ValueError) as exc:
        return {
            "planar_overlay_state": "UNRESOLVED",
            "planar_overlay_warning": "ERROR_GIS_PLANAR_OVERLAY_RUNTIME",
            "left_area": None,
            "right_area": None,
            "intersection_area": None,
            "symmetric_difference_area": None,
            "error_detail": str(exc),
        }
    return {
        "planar_overlay_state": "MEASURED",
        "planar_overlay_warning": None,
        "left_area": round(float(left_shape.area), 12),
        "right_area": round(float(right_shape.area), 12),
        "intersection_area": round(float(intersection_area), 12),
        "symmetric_difference_area": round(float(symmetric_difference_area), 12),
    }


def _apply_overlay_operation(left_shape: Any, right_shape: Any, operation: str) -> Any:
    if operation == "intersection":
        return left_shape.intersection(right_shape)
    if operation == "union":
        return left_shape.union(right_shape)
    if operation == "difference":
        return left_shape.difference(right_shape)
    if operation == "symmetric_difference":
        return left_shape.symmetric_difference(right_shape)
    raise GISProcessingError("ERROR_GIS_OVERLAY_OPERATION", f"Unsupported GIS overlay operation: {operation}")


def _feature_key(feature: dict[str, Any], index: int, *, match_on: str) -> str:
    if match_on == "index":
        return f"index:{index}"
    raw_id = feature.get("id")
    if raw_id is not None and raw_id != "":
        return f"id:{raw_id}"
    return f"index:{index}"


def _indexed_features(source: GeoJSONSource, *, match_on: str) -> dict[str, tuple[int, dict[str, Any]]]:
    indexed: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, feature in enumerate(_features(source)):
        indexed[_feature_key(feature, index, match_on=match_on)] = (index, feature)
    return indexed


def _duplicate_feature_keys(source: GeoJSONSource, *, match_on: str) -> list[str]:
    counts: dict[str, int] = {}
    for index, feature in enumerate(_features(source)):
        key = _feature_key(feature, index, match_on=match_on)
        counts[key] = counts.get(key, 0) + 1
    return sorted(key for key, count in counts.items() if count > 1)


def overlay_geojson_collections(
    left_path: Path,
    right_path: Path,
    *,
    cfg: Settings = settings,
    operation: str = "intersection",
    match_on: str = "id",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    normalized_operation = operation.strip().lower()
    if normalized_operation not in SUPPORTED_OVERLAY_OPERATIONS:
        raise GISProcessingError("ERROR_GIS_OVERLAY_OPERATION", f"Unsupported GIS overlay operation: {operation}")
    if match_on not in {"id", "index"}:
        raise GISProcessingError("ERROR_GIS_OVERLAY_MATCH_MODE", f"Unsupported GIS overlay match mode: {match_on}")

    left_source = _load_geojson(left_path)
    right_source = _load_geojson(right_path)
    left_raw_features = left_source.payload.get("features")
    right_raw_features = right_source.payload.get("features")
    if not isinstance(left_raw_features, list) or not isinstance(right_raw_features, list):
        raise GISProcessingError("ERROR_GIS_FEATURES_NOT_ARRAY", "GeoJSON FeatureCollection features must be an array.")
    if not all(isinstance(feature, dict) for feature in left_raw_features) or not all(isinstance(feature, dict) for feature in right_raw_features):
        raise GISProcessingError("ERROR_GIS_OVERLAY_FEATURES_NOT_OBJECTS", "All overlay input features must be objects.")
    if left_source.crs != right_source.crs:
        raise GISProcessingError("ERROR_GIS_OVERLAY_CRS_MISMATCH", "Overlay inputs must declare the same CRS.")

    duplicate_keys = {
        "left": _duplicate_feature_keys(left_source, match_on=match_on),
        "right": _duplicate_feature_keys(right_source, match_on=match_on),
    }
    if duplicate_keys["left"] or duplicate_keys["right"]:
        raise GISProcessingError("ERROR_GIS_OVERLAY_DUPLICATE_FEATURE_KEYS", f"Overlay input feature keys must be unique: {duplicate_keys}")

    left_index = _indexed_features(left_source, match_on=match_on)
    right_index = _indexed_features(right_source, match_on=match_on)
    shared_keys = sorted(set(left_index) & set(right_index))
    left_only = sorted(set(left_index) - set(right_index))
    right_only = sorted(set(right_index) - set(left_index))
    state_counts = {
        "EMITTED": 0,
        "EMPTY_INPUT": 0,
        "EMPTY_RESULT": 0,
        "UNRESOLVED": 0,
        "UNSUPPORTED": 0,
        "NON_POLYGON_RESULT": 0,
        "UNMATCHED_LEFT": len(left_only),
        "UNMATCHED_RIGHT": len(right_only),
    }
    feature_receipts: list[dict[str, Any]] = []
    output_features: list[dict[str, Any]] = []
    result_area_total = 0.0
    result_length_total = 0.0

    for key in shared_keys:
        left_feature_index, left_feature = left_index[key]
        right_feature_index, right_feature = right_index[key]
        left_geometry = left_feature.get("geometry")
        right_geometry = right_feature.get("geometry")
        left_classification = _classify_geometry(left_geometry)
        right_classification = _classify_geometry(right_geometry)
        left_dimensions = _coordinate_dimension_summary(left_geometry if isinstance(left_geometry, dict) else None)
        right_dimensions = _coordinate_dimension_summary(right_geometry if isinstance(right_geometry, dict) else None)
        row: dict[str, Any] = {
            "feature_key": key,
            "left_feature_id": _feature_id(left_feature, left_feature_index),
            "right_feature_id": _feature_id(right_feature, right_feature_index),
            "left_feature_index": left_feature_index,
            "right_feature_index": right_feature_index,
            "left_geometry_type": left_classification["geometry_type"],
            "right_geometry_type": right_classification["geometry_type"],
            "left_geometry_status": left_classification["geometry_status"],
            "right_geometry_status": right_classification["geometry_status"],
            "left_coordinate_dimension": left_dimensions["coordinate_dimension"],
            "right_coordinate_dimension": right_dimensions["coordinate_dimension"],
            "overlay_state": None,
            "overlay_warning": None,
            "result_feature_id": None,
            "result_geometry_type": None,
            "result_area": None,
            "result_length": None,
            "warnings": [],
        }

        if left_classification["geometry_status"] == "NULL_EMPTY" or right_classification["geometry_status"] == "NULL_EMPTY":
            row["overlay_state"] = "EMPTY_INPUT"
            row["overlay_warning"] = "GIS_OVERLAY_NULL_EMPTY_INPUT"
            row["warnings"] = ["GIS_OVERLAY_NULL_EMPTY_INPUT"]
            state_counts["EMPTY_INPUT"] += 1
            feature_receipts.append(row)
            continue
        if left_classification["geometry_status"] != "VALID" or right_classification["geometry_status"] != "VALID":
            row["overlay_state"] = "UNRESOLVED"
            row["overlay_warning"] = "GIS_OVERLAY_INVALID_INPUT_GEOMETRY"
            row["warnings"] = [*left_classification["warnings"], *right_classification["warnings"], "GIS_OVERLAY_INVALID_INPUT_GEOMETRY"]
            state_counts["UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        left_type = left_geometry.get("type") if isinstance(left_geometry, dict) else None
        right_type = right_geometry.get("type") if isinstance(right_geometry, dict) else None
        if left_type not in SUPPORTED_OVERLAY_GEOMETRY_TYPES or right_type not in SUPPORTED_OVERLAY_GEOMETRY_TYPES:
            row["overlay_state"] = "UNSUPPORTED"
            row["overlay_warning"] = "GIS_OVERLAY_UNSUPPORTED_GEOMETRY"
            row["warnings"] = ["GIS_OVERLAY_UNSUPPORTED_GEOMETRY"]
            state_counts["UNSUPPORTED"] += 1
            feature_receipts.append(row)
            continue
        if left_dimensions["has_z"] or left_dimensions["has_m"] or right_dimensions["has_z"] or right_dimensions["has_m"]:
            row["overlay_state"] = "UNRESOLVED"
            row["overlay_warning"] = "GIS_OVERLAY_COORDINATE_DIMENSION_UNSUPPORTED"
            row["warnings"] = ["GIS_OVERLAY_COORDINATE_DIMENSION_UNSUPPORTED"]
            state_counts["UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue

        try:
            left_shape = _shapely_geojson_geometry(
                left_geometry,
                invalid_status="ERROR_GIS_OVERLAY_INVALID_GEOMETRY",
                topology_status="ERROR_GIS_OVERLAY_INVALID_TOPOLOGY",
            )
            right_shape = _shapely_geojson_geometry(
                right_geometry,
                invalid_status="ERROR_GIS_OVERLAY_INVALID_GEOMETRY",
                topology_status="ERROR_GIS_OVERLAY_INVALID_TOPOLOGY",
            )
            result_shape = _apply_overlay_operation(left_shape, right_shape, normalized_operation)
        except GISProcessingError as exc:
            row["overlay_state"] = "UNRESOLVED"
            row["overlay_warning"] = exc.status
            row["warnings"] = [exc.status]
            state_counts["UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue
        except (AttributeError, ShapelyError, TypeError, ValueError) as exc:
            row["overlay_state"] = "UNRESOLVED"
            row["overlay_warning"] = "ERROR_GIS_OVERLAY_RUNTIME"
            row["warnings"] = ["ERROR_GIS_OVERLAY_RUNTIME"]
            row["error_detail"] = str(exc)
            state_counts["UNRESOLVED"] += 1
            feature_receipts.append(row)
            continue

        if result_shape.is_empty:
            row["overlay_state"] = "EMPTY_RESULT"
            state_counts["EMPTY_RESULT"] += 1
            feature_receipts.append(row)
            continue
        result_geometry = _json_geometry(shapely_mapping(result_shape))
        result_geometry_type = result_geometry.get("type") if isinstance(result_geometry, dict) else None
        if result_geometry_type not in SUPPORTED_OVERLAY_GEOMETRY_TYPES:
            row["overlay_state"] = "NON_POLYGON_RESULT"
            row["overlay_warning"] = f"GIS_OVERLAY_NON_POLYGON_RESULT:{result_geometry_type}"
            row["warnings"] = [str(row["overlay_warning"])]
            row["result_geometry_type"] = result_geometry_type
            state_counts["NON_POLYGON_RESULT"] += 1
            feature_receipts.append(row)
            continue

        result_area = round(float(result_shape.area), 12)
        result_length = round(float(result_shape.length), 12)
        result_feature_id = stable_id("gisoverlay", normalized_operation, key, left_source.sha256, right_source.sha256)
        output_features.append(
            {
                "type": "Feature",
                "id": result_feature_id,
                "properties": {
                    "overlay_operation": normalized_operation,
                    "feature_key": key,
                    "left_feature_id": row["left_feature_id"],
                    "right_feature_id": row["right_feature_id"],
                    "left_feature_index": left_feature_index,
                    "right_feature_index": right_feature_index,
                    "left_source_sha256": left_source.sha256,
                    "right_source_sha256": right_source.sha256,
                },
                "geometry": result_geometry,
            }
        )
        row.update(
            {
                "overlay_state": "EMITTED",
                "result_feature_id": result_feature_id,
                "result_geometry_type": result_geometry_type,
                "result_area": result_area,
                "result_length": result_length,
            }
        )
        state_counts["EMITTED"] += 1
        result_area_total += result_area
        result_length_total += result_length
        feature_receipts.append(row)

    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "overlay",
        normalized_operation,
        left_source.sha256,
        right_source.sha256,
        match_on,
        created_at,
    )
    output_payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": left_source.crs}},
        "features": output_features,
    }
    output = _write_geojson_output(cfg, report_id, output_payload)
    warnings = [
        warning
        for warning in [
            "GIS_UNMATCHED_FEATURES" if left_only or right_only else "",
            "GIS_OVERLAY_EMPTY_INPUTS" if state_counts["EMPTY_INPUT"] else "",
            "GIS_OVERLAY_EMPTY_RESULTS" if state_counts["EMPTY_RESULT"] else "",
            "GIS_OVERLAY_UNSUPPORTED_GEOMETRY" if state_counts["UNSUPPORTED"] else "",
            "GIS_OVERLAY_UNRESOLVED" if state_counts["UNRESOLVED"] else "",
            "GIS_OVERLAY_NON_POLYGON_RESULTS" if state_counts["NON_POLYGON_RESULT"] else "",
        ]
        if warning
    ]
    pair_state_count = (
        state_counts["EMITTED"]
        + state_counts["EMPTY_INPUT"]
        + state_counts["EMPTY_RESULT"]
        + state_counts["UNRESOLVED"]
        + state_counts["UNSUPPORTED"]
        + state_counts["NON_POLYGON_RESULT"]
    )
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_OVERLAY_OK" if not warnings else "GIS_OVERLAY_WARN",
        "operation": "overlay_geojson_collections",
        "created_at": created_at,
        "match_on": match_on,
        "parameters": {
            "overlay_operation": normalized_operation,
            "match_on": match_on,
            "adapter": "shapely_planar_overlay_v1",
            "overlay_adapter": "shapely_planar_overlay_v1",
            "supported_operations": sorted(SUPPORTED_OVERLAY_OPERATIONS),
            "supported_geometry_types": sorted(SUPPORTED_OVERLAY_GEOMETRY_TYPES),
        },
        "inputs": {
            "left": {
                "path": str(left_path),
                "sha256": left_source.sha256,
                "crs": left_source.crs,
                "crs_status": left_source.crs_status,
                "feature_count": len(left_raw_features),
            },
            "right": {
                "path": str(right_path),
                "sha256": right_source.sha256,
                "crs": right_source.crs,
                "crs_status": right_source.crs_status,
                "feature_count": len(right_raw_features),
            },
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": left_source.crs,
            "feature_count": len(output_features),
        },
        "left_only_feature_keys": left_only,
        "right_only_feature_keys": right_only,
        "features": feature_receipts,
        "state_counts": state_counts,
        "invariants": {
            "crs_match": True,
            "left_feature_count": len(left_raw_features),
            "right_feature_count": len(right_raw_features),
            "shared_feature_key_count": len(shared_keys),
            "left_only_count": len(left_only),
            "right_only_count": len(right_only),
            "output_feature_count": len(output_features),
            "emitted_feature_count": state_counts["EMITTED"],
            "empty_input_count": state_counts["EMPTY_INPUT"],
            "empty_result_count": state_counts["EMPTY_RESULT"],
            "unresolved_pair_count": state_counts["UNRESOLVED"],
            "unsupported_pair_count": state_counts["UNSUPPORTED"],
            "non_polygon_result_count": state_counts["NON_POLYGON_RESULT"],
            "feature_key_accounting_closed": len(shared_keys) + len(left_only) + len(right_only) == len(set(left_index) | set(right_index)),
            "overlay_pair_accounting_closed": pair_state_count == len(shared_keys),
            "result_area_total": round(result_area_total, 12),
            "result_length_total": round(result_length_total, 12),
        },
        "warnings": warnings,
        "limitations": [
            "standalone overlay is limited to matched Polygon and MultiPolygon pairs",
            "overlay uses Shapely planar operations in the input CRS and does not transform CRS",
            "Z and M coordinates remain unresolved instead of being flattened into XY output",
            "lower-dimensional or geometry-collection overlay results are recorded as residue, not emitted",
        ],
        "falsification_criteria": [
            "reject the overlay if feature_key_accounting_closed or overlay_pair_accounting_closed is false",
            "reject derived-output claims if output sha256 or output_feature_count does not match the saved GeoJSON",
            "reject planar overlay results for geodesic, legal-boundary, or engineering use without independent GIS-engine validation",
        ],
    }
    return _write_report(cfg, report)


def compare_geojson_geometries(
    left_path: Path,
    right_path: Path,
    *,
    cfg: Settings = settings,
    match_on: str = "id",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    if match_on not in {"id", "index"}:
        raise GISProcessingError("ERROR_GIS_COMPARE_MATCH_MODE", f"Unsupported GIS compare match mode: {match_on}")
    left_source = _load_geojson(left_path)
    right_source = _load_geojson(right_path)
    left_index = _indexed_features(left_source, match_on=match_on)
    right_index = _indexed_features(right_source, match_on=match_on)
    shared_keys = sorted(set(left_index) & set(right_index))
    left_only = sorted(set(left_index) - set(right_index))
    right_only = sorted(set(right_index) - set(left_index))
    comparisons: list[dict[str, Any]] = []
    for key in shared_keys:
        left_feature_index, left_feature = left_index[key]
        right_feature_index, right_feature = right_index[key]
        left_geometry = left_feature.get("geometry")
        right_geometry = right_feature.get("geometry")
        left_classification = _classify_geometry(left_geometry)
        right_classification = _classify_geometry(right_geometry)
        topological_equal = _topological_equal(left_geometry, right_geometry)
        overlay_metrics = _planar_overlay_metrics(left_geometry, right_geometry)
        dimension_left = _coordinate_dimension_summary(left_geometry if isinstance(left_geometry, dict) else None)
        dimension_right = _coordinate_dimension_summary(right_geometry if isinstance(right_geometry, dict) else None)
        comparisons.append(
            {
                "feature_key": key,
                "left_feature_id": _feature_id(left_feature, left_feature_index),
                "right_feature_id": _feature_id(right_feature, right_feature_index),
                "left_feature_index": left_feature_index,
                "right_feature_index": right_feature_index,
                "left_geometry_type": left_classification["geometry_type"],
                "right_geometry_type": right_classification["geometry_type"],
                "left_geometry_status": left_classification["geometry_status"],
                "right_geometry_status": right_classification["geometry_status"],
                "left_coordinate_dimension": dimension_left["coordinate_dimension"],
                "right_coordinate_dimension": dimension_right["coordinate_dimension"],
                "coordinate_dimension_preserved": dimension_left["coordinate_dimension"] == dimension_right["coordinate_dimension"],
                "exact_coordinate_equal": left_geometry == right_geometry,
                "topological_equal": topological_equal,
                "topology_state": "UNRESOLVED" if topological_equal is None else ("EQUAL" if topological_equal else "DIFFERENT"),
                "hausdorff_vertex_distance": _hausdorff_vertex_distance(left_geometry, right_geometry),
                "vertex_symmetric_difference_count": _vertex_symmetric_difference_count(left_geometry, right_geometry),
                **overlay_metrics,
            }
        )
    topology_unresolved_count = sum(1 for row in comparisons if row["topology_state"] == "UNRESOLVED")
    geometry_difference_count = sum(1 for row in comparisons if row["topology_state"] != "EQUAL")
    dimension_mismatch_count = sum(1 for row in comparisons if not row["coordinate_dimension_preserved"])
    vertex_symmetric_difference_total = sum(
        int(row["vertex_symmetric_difference_count"] or 0)
        for row in comparisons
    )
    hausdorff_distances = [
        float(row["hausdorff_vertex_distance"])
        for row in comparisons
        if row["hausdorff_vertex_distance"] is not None
    ]
    measured_overlay_rows = [row for row in comparisons if row["planar_overlay_state"] == "MEASURED"]
    overlay_symmetric_difference_areas = [
        float(row["symmetric_difference_area"])
        for row in measured_overlay_rows
        if row["symmetric_difference_area"] is not None
    ]
    planar_overlay_unresolved_count = sum(1 for row in comparisons if row["planar_overlay_state"] == "UNRESOLVED")
    planar_overlay_not_applicable_count = sum(1 for row in comparisons if row["planar_overlay_state"] == "NOT_APPLICABLE")
    planar_overlay_difference_count = sum(1 for value in overlay_symmetric_difference_areas if value > EPSILON)
    created_at = utc_now()
    report_id = stable_id("gisreport", "compare", left_source.sha256, right_source.sha256, match_on, created_at)
    warnings = [
        warning
        for warning in [
            "GIS_CRS_MISMATCH" if left_source.crs != right_source.crs else "",
            "GIS_UNMATCHED_FEATURES" if left_only or right_only else "",
            "GIS_GEOMETRY_DIFFERENCE_PRESENT" if geometry_difference_count else "",
            "GIS_TOPOLOGY_UNRESOLVED" if topology_unresolved_count else "",
            "GIS_PLANAR_OVERLAY_DIFFERENCE_PRESENT" if planar_overlay_difference_count else "",
            "GIS_PLANAR_OVERLAY_UNRESOLVED" if planar_overlay_unresolved_count else "",
            "GIS_COORDINATE_DIMENSION_MISMATCH" if dimension_mismatch_count else "",
        ]
        if warning
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_GEOMETRY_COMPARE_OK" if not warnings else "GIS_GEOMETRY_COMPARE_WARN",
        "operation": "compare_geojson_geometries",
        "created_at": created_at,
        "match_on": match_on,
        "parameters": {
            "match_on": match_on,
            "topology_adapter": "local_canonical_geometry_v1",
            "planar_overlay_adapter": "shapely_planar_overlay_v1",
            "supported_overlay_geometry_types": sorted(SUPPORTED_OVERLAY_GEOMETRY_TYPES),
        },
        "inputs": {
            "left": {
                "path": str(left_path),
                "sha256": left_source.sha256,
                "crs": left_source.crs,
                "crs_status": left_source.crs_status,
                "feature_count": len(_features(left_source)),
            },
            "right": {
                "path": str(right_path),
                "sha256": right_source.sha256,
                "crs": right_source.crs,
                "crs_status": right_source.crs_status,
                "feature_count": len(_features(right_source)),
            },
        },
        "left_only_feature_keys": left_only,
        "right_only_feature_keys": right_only,
        "comparisons": comparisons,
        "invariants": {
            "crs_match": left_source.crs == right_source.crs,
            "feature_key_conservation": len(shared_keys) + len(left_only) + len(right_only) == len(set(left_index) | set(right_index)),
            "compared_count": len(comparisons),
            "left_only_count": len(left_only),
            "right_only_count": len(right_only),
            "exact_equal_count": sum(1 for row in comparisons if row["exact_coordinate_equal"]),
            "topological_equal_count": sum(1 for row in comparisons if row["topology_state"] == "EQUAL"),
            "topology_unresolved_count": topology_unresolved_count,
            "geometry_difference_count": geometry_difference_count,
            "coordinate_dimension_mismatch_count": dimension_mismatch_count,
            "vertex_symmetric_difference_total": vertex_symmetric_difference_total,
            "max_hausdorff_vertex_distance": max(hausdorff_distances, default=None),
            "planar_overlay_measured_count": len(measured_overlay_rows),
            "planar_overlay_not_applicable_count": planar_overlay_not_applicable_count,
            "planar_overlay_unresolved_count": planar_overlay_unresolved_count,
            "planar_overlay_difference_count": planar_overlay_difference_count,
            "planar_overlay_intersection_area_total": round(
                sum(float(row["intersection_area"] or 0.0) for row in measured_overlay_rows),
                12,
            ),
            "planar_overlay_symmetric_difference_area_total": round(sum(overlay_symmetric_difference_areas), 12),
            "max_planar_symmetric_difference_area": max(overlay_symmetric_difference_areas, default=None),
        },
        "warnings": warnings,
        "limitations": [
            "topological equality is limited to Point, Polygon, and MultiPolygon coordinates",
            "planar overlay area metrics are limited to Polygon and MultiPolygon pairs in source CRS units",
            "Hausdorff distance and vertex symmetric difference remain vertex-based receipt checks",
        ],
        "falsification_criteria": [
            "reject the comparison if feature_key_conservation is false or unmatched residue is not reported",
            "reject topology conclusions when topology_state is UNRESOLVED inside the claimed scope",
            "compare material overlay differences with an independent GIS engine before legal-boundary or engineering use",
        ],
    }
    return _write_report(cfg, report)


def _point_tuple(geometry: dict[str, Any]) -> tuple[float, float] | None:
    coordinates = geometry.get("coordinates")
    if not _is_position(coordinates):
        return None
    return (float(coordinates[0]), float(coordinates[1]))


def _point_on_segment(point: tuple[float, float], start: Any, end: Any) -> bool:
    if not _is_position(start) or not _is_position(end):
        return False
    px, py = point
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > EPSILON:
        return False
    dot = (px - x1) * (px - x2) + (py - y1) * (py - y2)
    return dot <= EPSILON


def _point_in_ring(point: tuple[float, float], ring: list[Any]) -> str:
    for start, end in zip(ring, ring[1:]):
        if _point_on_segment(point, start, end):
            return "TOUCH_ONLY"
    x, y = point
    inside = False
    for start, end in zip(ring, ring[1:]):
        x1, y1 = float(start[0]), float(start[1])
        x2, y2 = float(end[0]), float(end[1])
        intersects = (y1 > y) != (y2 > y)
        if intersects:
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return "FULLY_WITHIN" if inside else "OUTSIDE"


def _point_in_polygon(point: tuple[float, float], polygon_coordinates: list[Any]) -> str:
    outer = _point_in_ring(point, polygon_coordinates[0])
    if outer != "FULLY_WITHIN":
        return outer
    for hole in polygon_coordinates[1:]:
        hole_state = _point_in_ring(point, hole)
        if hole_state == "TOUCH_ONLY":
            return "TOUCH_ONLY"
        if hole_state == "FULLY_WITHIN":
            return "OUTSIDE"
    return "FULLY_WITHIN"


def _point_in_polygon_geometry(point: tuple[float, float], polygon_geometry: dict[str, Any]) -> str:
    geometry_type = polygon_geometry.get("type")
    coordinates = polygon_geometry.get("coordinates")
    if geometry_type == "Polygon" and _polygon_valid(coordinates):
        return _point_in_polygon(point, coordinates)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        states = [_point_in_polygon(point, polygon) for polygon in coordinates if _polygon_valid(polygon)]
        if "TOUCH_ONLY" in states:
            return "TOUCH_ONLY"
        if "FULLY_WITHIN" in states:
            return "FULLY_WITHIN"
    return "OUTSIDE"


def spatial_join_points_to_polygons(
    points_path: Path,
    polygons_path: Path,
    *,
    cfg: Settings = settings,
    require_matching_crs: bool = True,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    points_source = _load_geojson(points_path)
    polygons_source = _load_geojson(polygons_path)
    crs_match = points_source.crs == polygons_source.crs
    crs_mismatch_allowed = not require_matching_crs and not crs_match
    if require_matching_crs and not crs_match:
        raise GISProcessingError(
            "ERROR_GIS_CRS_MISMATCH",
            f"Point CRS {points_source.crs} does not match polygon CRS {polygons_source.crs}.",
        )
    polygon_features = _features(polygons_source)
    point_results: list[dict[str, Any]] = []
    polygon_match_counts: dict[str, int] = {}
    for point_index, point_feature in enumerate(_features(points_source)):
        point_geometry = point_feature.get("geometry")
        point_classification = _classify_geometry(point_geometry)
        point_id = _feature_id(point_feature, point_index)
        if point_classification["geometry_status"] == "NULL_EMPTY":
            point_results.append(
                {
                    "point_feature_id": point_id,
                    "point_feature_index": point_index,
                    "spatial_state": "NULL_EMPTY",
                    "match_count": 0,
                    "matched_polygons": [],
                    "warnings": point_classification["warnings"],
                }
            )
            continue
        if point_classification["geometry_type"] != "Point" or point_classification["geometry_status"] != "VALID":
            point_results.append(
                {
                    "point_feature_id": point_id,
                    "point_feature_index": point_index,
                    "spatial_state": "UNRESOLVED",
                    "match_count": 0,
                    "matched_polygons": [],
                    "warnings": point_classification["warnings"] or ["POINT_GEOMETRY_INVALID"],
                }
            )
            continue
        point = _point_tuple(point_geometry)
        matches: list[dict[str, Any]] = []
        for polygon_index, polygon_feature in enumerate(polygon_features):
            polygon_geometry = polygon_feature.get("geometry")
            polygon_classification = _classify_geometry(polygon_geometry)
            if polygon_classification["geometry_status"] != "VALID":
                continue
            relation = _point_in_polygon_geometry(point, polygon_geometry)
            if relation in {"FULLY_WITHIN", "TOUCH_ONLY"}:
                polygon_id = _feature_id(polygon_feature, polygon_index)
                matches.append(
                    {
                        "polygon_feature_id": polygon_id,
                        "polygon_feature_index": polygon_index,
                        "relation": relation,
                    }
                )
                polygon_match_counts[polygon_id] = polygon_match_counts.get(polygon_id, 0) + 1
        if any(match["relation"] == "FULLY_WITHIN" for match in matches):
            spatial_state = "FULLY_WITHIN"
        elif any(match["relation"] == "TOUCH_ONLY" for match in matches):
            spatial_state = "TOUCH_ONLY"
        else:
            spatial_state = "OUTSIDE"
        point_results.append(
            {
                "point_feature_id": point_id,
                "point_feature_index": point_index,
                "spatial_state": spatial_state,
                "match_count": len(matches),
                "matched_polygons": matches,
                "warnings": ["MULTIPLE_POLYGON_MATCHES"] if len(matches) > 1 else [],
            }
        )
    max_matches_per_point = max((result["match_count"] for result in point_results), default=0)
    max_points_per_polygon = max(polygon_match_counts.values(), default=0)
    if max_matches_per_point > 1 and max_points_per_polygon > 1:
        cardinality = "N:N"
    elif max_matches_per_point > 1:
        cardinality = "1:N"
    elif max_points_per_polygon > 1:
        cardinality = "N:1"
    elif max_matches_per_point == 1:
        cardinality = "1:1"
    else:
        cardinality = "0:1"
    created_at = utc_now()
    report_id = stable_id("gisreport", "point_in_polygon", points_source.sha256, polygons_source.sha256, created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_SPATIAL_JOIN_OK",
        "operation": "point_in_polygon",
        "created_at": created_at,
        "inputs": {
            "points": {
                "path": str(points_path),
                "sha256": points_source.sha256,
                "crs": points_source.crs,
                "crs_status": points_source.crs_status,
                "feature_count": len(_features(points_source)),
            },
            "polygons": {
                "path": str(polygons_path),
                "sha256": polygons_source.sha256,
                "crs": polygons_source.crs,
                "crs_status": polygons_source.crs_status,
                "feature_count": len(polygon_features),
            },
        },
        "join_cardinality": cardinality,
        "state_counts": {
            state: sum(1 for result in point_results if result["spatial_state"] == state)
            for state in ["FULLY_WITHIN", "TOUCH_ONLY", "OUTSIDE", "NULL_EMPTY", "UNRESOLVED"]
        },
        "point_results": point_results,
        "invariants": {
            "crs_match": crs_match,
            "crs_mismatch_allowed": crs_mismatch_allowed,
            "point_row_conservation": len(point_results) == len(_features(points_source)),
            "polygon_candidate_count": len(polygon_features),
            "max_matches_per_point": max_matches_per_point,
            "max_points_per_polygon": max_points_per_polygon,
        },
        "warnings": [
            warning
            for warning in [
                "GIS_CRS_MISMATCH_ALLOWED" if crs_mismatch_allowed else "",
                "GIS_JOIN_MULTIPLICATION_PRESENT" if cardinality in {"1:N", "N:1", "N:N"} else "",
            ]
            if warning
        ],
        "limitations": [
            "point-in-polygon uses local ray casting against GeoJSON polygon coordinates",
            "CRS mismatch is rejected by default; explicit mismatch override records but does not repair CRS disagreement",
            "spatial joins classify point relations only and do not perform polygon overlay or distance operations",
        ],
        "falsification_criteria": [
            "reject the join if point_row_conservation is false",
            "treat CRS_MISMATCH_ALLOWED results as provisional until both layers are reprojected into a common CRS",
            "review any 1:N, N:1, or N:N cardinality before using joined rows as identity proof",
        ],
    }
    return _write_report(cfg, report)
