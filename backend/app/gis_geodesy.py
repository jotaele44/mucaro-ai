from __future__ import annotations

from pathlib import Path
from typing import Any

from pyproj import Geod

from .config import Settings, settings
from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _classify_geometry, _feature_id, _load_geojson, _write_report
from .utils import stable_id, utc_now

GEOD = Geod(ellps="WGS84")
ELLIPSOIDAL_ADAPTER = "pyproj_geod_wgs84_v1"


def _line_length(coords: list[Any]) -> float:
    if len(coords) < 2:
        raise GISProcessingError("ERROR_GIS_GEOD_GEOMETRY", "Line requires at least two positions.")
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        _, _, distance = GEOD.inv(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        total += distance
    return total


def _polygon(coords: list[Any]) -> tuple[float, float]:
    if not coords:
        raise GISProcessingError("ERROR_GIS_GEOD_GEOMETRY", "Polygon requires rings.")
    areas: list[float] = []
    perimeter = 0.0
    for ring in coords:
        if len(ring) < 4:
            raise GISProcessingError("ERROR_GIS_GEOD_GEOMETRY", "Polygon ring requires at least four positions.")
        lons = [float(p[0]) for p in ring]
        lats = [float(p[1]) for p in ring]
        area, ring_perimeter = GEOD.polygon_area_perimeter(lons, lats)
        areas.append(abs(area))
        perimeter += ring_perimeter
    return max(0.0, areas[0] - sum(areas[1:])), perimeter


def _measure(geometry: dict[str, Any]) -> tuple[float, float]:
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind in {"Point", "MultiPoint"}:
        return 0.0, 0.0
    if kind == "LineString":
        return 0.0, _line_length(coords)
    if kind == "MultiLineString":
        return 0.0, sum(_line_length(line) for line in coords)
    if kind == "Polygon":
        return _polygon(coords)
    if kind == "MultiPolygon":
        measured = [_polygon(poly) for poly in coords]
        return sum(v[0] for v in measured), sum(v[1] for v in measured)
    raise GISProcessingError("ERROR_GIS_GEOD_UNSUPPORTED", str(kind))


def measure_geojson_ellipsoidal(path: Path, *, cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    if source.crs.strip().upper() != "EPSG:4326":
        raise GISProcessingError("ERROR_GIS_GEOD_CRS", "WGS84 ellipsoidal measurement requires EPSG:4326.")
    features = source.payload.get("features")
    if not isinstance(features, list) or not all(isinstance(feature, dict) for feature in features):
        raise GISProcessingError("ERROR_GIS_GEOD_FEATURES", "All features must be objects.")
    rows, totals = [], {"area_m2": 0.0, "length_m": 0.0}
    states = {"MEASURED": 0, "NULL_EMPTY": 0, "UNRESOLVED": 0}
    for index, feature in enumerate(features):
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        row = {"feature_index": index, "feature_id": _feature_id(feature, index), "geometry_type": classification["geometry_type"], "state": "UNRESOLVED", "area_m2": None, "length_m": None, "warnings": list(classification["warnings"])}
        if classification["geometry_status"] == "NULL_EMPTY":
            row["state"] = "NULL_EMPTY"; states["NULL_EMPTY"] += 1; rows.append(row); continue
        if classification["geometry_status"] != "VALID" or not isinstance(geometry, dict):
            states["UNRESOLVED"] += 1; rows.append(row); continue
        try:
            area, length = _measure(geometry)
        except (GISProcessingError, TypeError, ValueError) as exc:
            row["warnings"] = [getattr(exc, "status", "ERROR_GIS_GEOD_RUNTIME")]
            states["UNRESOLVED"] += 1; rows.append(row); continue
        row.update({"state": "MEASURED", "area_m2": round(area, 6), "length_m": round(length, 6), "warnings": []})
        states["MEASURED"] += 1; totals["area_m2"] += area; totals["length_m"] += length; rows.append(row)
    created_at = utc_now(); report_id = stable_id("gisreport", "ellipsoidal", source.sha256, created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id,
        "status": "GIS_ELLIPSOIDAL_OK" if not states["UNRESOLVED"] else "GIS_ELLIPSOIDAL_WARN",
        "operation": "measure_geojson_ellipsoidal", "created_at": created_at,
        "parameters": {"adapter": ELLIPSOIDAL_ADAPTER, "ellipsoid": "WGS84", "area_units": "meters^2", "length_units": "meters"},
        "source": {"path": str(path), "sha256": source.sha256, "crs": source.crs, "feature_count": len(features)},
        "measurements": rows, "state_counts": states,
        "invariants": {"row_accounting_closed": len(features) == sum(states.values()), "total_area_m2": round(totals["area_m2"], 6), "total_length_m": round(totals["length_m"], 6)},
        "warnings": ["GIS_ELLIPSOIDAL_UNRESOLVED"] if states["UNRESOLVED"] else [],
        "limitations": ["WGS84 ellipsoid only", "input must be EPSG:4326", "topology repair is a separate explicit operation"],
        "falsification_criteria": ["reject if row accounting does not close", "compare material results with an independent ellipsoidal GIS engine"],
    }
    return _write_report(cfg, report)
