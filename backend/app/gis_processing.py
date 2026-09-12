from __future__ import annotations

import csv
import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json

GIS_REPORT_SCHEMA_VERSION = "mucaro_gis_report_v1"
GIS_REPORT_DIR = "gis_reports"
DEFAULT_CRS = "EPSG:4326"
EPSILON = 1e-9
WEB_MERCATOR_RADIUS = 6378137.0
WEB_MERCATOR_MAX_LATITUDE = 85.0511287798066
WEB_MERCATOR_MAX_EXTENT = 20037508.342789244
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
        geometry_type = classification["geometry_type"] or "NULL"
        geometry_counts[geometry_type] = geometry_counts.get(geometry_type, 0) + 1
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        inspected.append(
            {
                "feature_index": index,
                "feature_id": _feature_id(feature, index),
                "geometry_type": classification["geometry_type"],
                "geometry_status": classification["geometry_status"],
                **_coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None),
                "bbox": _bbox_for_geometry(geometry) if isinstance(geometry, dict) else None,
                "property_keys": sorted(str(key) for key in properties),
                "warnings": classification["warnings"],
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
    }
    return _write_report(cfg, report)


def inspect_geojson(path: Path, *, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    inspected, geometry_counts = _inspect_features(source)
    invalid_count = sum(1 for feature in inspected if feature["geometry_status"] in {"INVALID", "UNSUPPORTED"})
    null_empty_count = sum(1 for feature in inspected if feature["geometry_status"] == "NULL_EMPTY")
    feature_count = len(inspected)
    created_at = utc_now()
    report_id = stable_id("gisreport", "inspect", str(path), source.sha256, created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_INSPECT_OK" if invalid_count == 0 else "GIS_INSPECT_WARN",
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
        "warnings": [] if invalid_count == 0 else ["GIS_GEOMETRY_WARNINGS_PRESENT"],
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
    created_at = utc_now()
    report_id = stable_id("gisreport", "compare", left_source.sha256, right_source.sha256, match_on, created_at)
    warnings = [
        warning
        for warning in [
            "GIS_CRS_MISMATCH" if left_source.crs != right_source.crs else "",
            "GIS_UNMATCHED_FEATURES" if left_only or right_only else "",
            "GIS_GEOMETRY_DIFFERENCE_PRESENT" if geometry_difference_count else "",
            "GIS_TOPOLOGY_UNRESOLVED" if topology_unresolved_count else "",
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
        },
        "warnings": warnings,
        "limitations": [
            "topological equality is limited to Point, Polygon, and MultiPolygon coordinates",
            "Hausdorff distance and symmetric difference are vertex-based receipt checks, not robust planar overlay results",
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
    }
    return _write_report(cfg, report)
