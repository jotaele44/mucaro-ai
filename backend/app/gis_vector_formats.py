from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
from pathlib import Path
from typing import Any

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _classify_geometry, _coordinate_dimension_summary
from .capability_contracts import require_gis_report_contract
from .utils import stable_id, utc_now, write_json

SUPPORTED_VECTOR_FORMATS = {".geojson": "GEOJSON", ".json": "GEOJSON", ".gpkg": "GEOPACKAGE", ".shp": "SHAPEFILE"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report(cfg: Any, report: dict[str, Any]) -> dict[str, Any]:
    path = cfg.export_dir / "gis_reports" / f"{report['report_id']}.json"
    require_gis_report_contract(report, report_path=str(path))
    write_json(path, report)
    return {**report, "report_path": str(path), "report_sha256": _sha256(path)}


def _inspect_geojson(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISProcessingError("ERROR_GIS_VECTOR_GEOJSON", f"GeoJSON could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise GISProcessingError("ERROR_GIS_VECTOR_GEOJSON", "GeoJSON input must be a FeatureCollection.")
    features = payload["features"]
    if not all(isinstance(feature, dict) for feature in features):
        raise GISProcessingError("ERROR_GIS_VECTOR_GEOJSON", "GeoJSON features must be objects.")
    geometry_types: dict[str, int] = {}
    dimension_counts: dict[str, int] = {}
    for feature in features:
        geometry = feature.get("geometry")
        classification = _classify_geometry(geometry)
        key = str(classification.get("geometry_type") or "NULL")
        geometry_types[key] = geometry_types.get(key, 0) + 1
        dims = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
        dimension_counts[dims["coordinate_dimension"]] = dimension_counts.get(dims["coordinate_dimension"], 0) + 1
    return {"feature_count": len(features), "geometry_types": geometry_types, "coordinate_dimensions": dimension_counts, "crs_manifestation": payload.get("crs")}


def _inspect_gpkg(path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT table_name, data_type, identifier, srs_id FROM gpkg_contents ORDER BY table_name").fetchall()
            geometry_rows = connection.execute("SELECT table_name, column_name, geometry_type_name, srs_id, z, m FROM gpkg_geometry_columns ORDER BY table_name, column_name").fetchall()
    except sqlite3.Error as exc:
        raise GISProcessingError("ERROR_GIS_VECTOR_GPKG", f"GeoPackage metadata could not be read: {exc}") from exc
    return {"contents": [{"table_name": r[0], "data_type": r[1], "identifier": r[2], "srs_id": r[3]} for r in rows], "geometry_columns": [{"table_name": r[0], "column_name": r[1], "geometry_type": r[2], "srs_id": r[3], "z": r[4], "m": r[5]} for r in geometry_rows], "feature_table_count": sum(1 for r in rows if r[1] == "features")}


def _inspect_shapefile(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 100:
        raise GISProcessingError("ERROR_GIS_VECTOR_SHP", "Shapefile header is truncated.")
    file_code = struct.unpack(">i", data[0:4])[0]
    version = struct.unpack("<i", data[28:32])[0]
    shape_type = struct.unpack("<i", data[32:36])[0]
    if file_code != 9994 or version != 1000:
        raise GISProcessingError("ERROR_GIS_VECTOR_SHP", "Shapefile header signature/version is invalid.")
    sidecars = {}
    for suffix in (".dbf", ".shx", ".prj", ".cpg"):
        candidate = path.with_suffix(suffix)
        sidecars[suffix] = {"present": candidate.exists(), "sha256": _sha256(candidate) if candidate.exists() else None, "bytes": candidate.stat().st_size if candidate.exists() else None}
    return {"shape_type_code": shape_type, "bbox": list(struct.unpack("<4d", data[36:68])), "sidecars": sidecars, "crs_wkt": path.with_suffix(".prj").read_text(encoding="utf-8", errors="replace") if path.with_suffix(".prj").exists() else None}


def inspect_vector_format(path: Path, *, cfg: Any) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise GISProcessingError("ERROR_GIS_VECTOR_NOT_FOUND", f"Vector input not found: {source}")
    fmt = SUPPORTED_VECTOR_FORMATS.get(source.suffix.lower())
    if not fmt:
        raise GISProcessingError("ERROR_GIS_VECTOR_FORMAT", f"Unsupported vector format: {source.suffix}")
    manifest = _inspect_geojson(source) if fmt == "GEOJSON" else _inspect_gpkg(source) if fmt == "GEOPACKAGE" else _inspect_shapefile(source)
    created_at = utc_now()
    source_sha = _sha256(source)
    report_id = stable_id("gisreport", "vector_format", fmt, source_sha, created_at)
    report = {"schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id, "status": "GIS_VECTOR_FORMAT_OK", "operation": "inspect_vector_format", "created_at": created_at, "source": {"path": str(source), "sha256": source_sha, "bytes": source.stat().st_size, "format": fmt}, "manifest": manifest, "invariants": {"source_hash_present": bool(source_sha), "format_supported": True}, "warnings": ["GIS_SHAPEFILE_PRJ_MISSING"] if fmt == "SHAPEFILE" and not manifest["sidecars"][".prj"]["present"] else [], "limitations": ["This V1 adapter certifies format/schema/CRS manifestations and source bytes; it does not claim lossless cross-format conversion.", "GeoPackage inspection is metadata-first and does not synthesize feature rows.", "Shapefile identity includes explicit sidecar manifestations rather than treating the .shp byte stream as the entire dataset."], "falsification_criteria": ["reject if source SHA-256 changes", "reject Shapefile CRS claims when .prj is absent", "do not infer cross-format feature identity from equal counts or names alone"]}
    return _report(cfg, report)
