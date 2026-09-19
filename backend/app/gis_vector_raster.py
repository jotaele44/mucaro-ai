from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import rasterio
from rasterio.features import geometry_mask
from shapely.geometry import shape

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary
from .utils import stable_id, utc_now, write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze_vector_raster_intersection(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    vector = vector_path.expanduser().resolve()
    raster = raster_path.expanduser().resolve()
    if not vector.exists() or not vector.is_file() or not raster.exists() or not raster.is_file():
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_NOT_FOUND", "Vector and raster inputs must both exist.")

    try:
        payload = json.loads(vector.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_VECTOR", f"Unable to read GeoJSON vector input: {exc}") from exc
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_VECTOR", "Vector input must be a GeoJSON FeatureCollection.")
    vector_crs = ((payload.get("crs") or {}).get("properties") or {}).get("name")

    try:
        dataset = rasterio.open(raster)
    except Exception as exc:
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_RASTER", f"Unable to open raster input: {exc}") from exc

    with dataset:
        raster_crs = dataset.crs.to_string() if dataset.crs else None
        if not vector_crs or not raster_crs or vector_crs.upper() != raster_crs.upper():
            raise GISProcessingError(
                "ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH",
                f"Explicit equal CRS required; vector={vector_crs!r}, raster={raster_crs!r}. No implicit reprojection is permitted.",
            )
        if not isinstance(band, int) or isinstance(band, bool) or band < 1 or band > dataset.count:
            raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_BAND", f"band must be between 1 and {dataset.count}.")

        data = dataset.read(band, masked=True)
        rows: list[dict[str, Any]] = []
        for index, feature in enumerate(payload["features"]):
            if not isinstance(feature, dict):
                rows.append({"feature_index": index, "state": "UNRESOLVED", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": ["ERROR_GIS_VECTOR_RASTER_FEATURE"]})
                continue
            geometry = feature.get("geometry")
            if geometry is None:
                rows.append({"feature_index": index, "state": "NULL_EMPTY", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": []})
                continue
            dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
            if dimensions["has_z"] or dimensions["has_m"]:
                rows.append({"feature_index": index, "state": "UNRESOLVED", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": ["GIS_VECTOR_RASTER_DIMENSION_UNSUPPORTED"]})
                continue
            try:
                geom = shape(geometry)
            except Exception:
                rows.append({"feature_index": index, "state": "UNRESOLVED", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": ["ERROR_GIS_VECTOR_RASTER_GEOMETRY"]})
                continue
            if geom.is_empty:
                rows.append({"feature_index": index, "state": "NULL_EMPTY", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": []})
                continue
            if not geom.is_valid:
                rows.append({"feature_index": index, "state": "UNRESOLVED", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": ["ERROR_GIS_VECTOR_RASTER_TOPOLOGY"]})
                continue
            try:
                mask = geometry_mask(
                    [geometry],
                    out_shape=(dataset.height, dataset.width),
                    transform=dataset.transform,
                    invert=True,
                    all_touched=False,
                )
            except Exception:
                rows.append({"feature_index": index, "state": "UNRESOLVED", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": ["ERROR_GIS_VECTOR_RASTER_MASK"]})
                continue
            covered = int(mask.sum())
            if covered == 0:
                rows.append({"feature_index": index, "state": "OUTSIDE_OR_NO_CELL_CENTER", "covered_cell_count": 0, "valid_cell_count": 0, "nodata_cell_count": 0, "warnings": []})
                continue
            masked_values = data[mask]
            valid = int(masked_values.count())
            nodata = covered - valid
            values = [float(v) for v in masked_values.compressed()]
            rows.append(
                {
                    "feature_index": index,
                    "state": "INTERSECTS_VALID" if valid else "NODATA_ONLY",
                    "covered_cell_count": covered,
                    "valid_cell_count": valid,
                    "nodata_cell_count": nodata,
                    "minimum": min(values) if values else None,
                    "maximum": max(values) if values else None,
                    "mean": sum(values) / len(values) if values else None,
                    "warnings": [],
                }
            )

    states = ("INTERSECTS_VALID", "NODATA_ONLY", "OUTSIDE_OR_NO_CELL_CENTER", "NULL_EMPTY", "UNRESOLVED")
    counts = {state: sum(1 for row in rows if row["state"] == state) for state in states}
    created_at = utc_now()
    vector_sha = _sha256(vector)
    raster_sha = _sha256(raster)
    report_id = stable_id("gisreport", "vector_raster", vector_sha, raster_sha, str(band), created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_VECTOR_RASTER_OK" if counts["UNRESOLVED"] == 0 else "GIS_VECTOR_RASTER_WARN",
        "operation": "analyze_vector_raster_intersection",
        "created_at": created_at,
        "parameters": {"band": band, "cell_inclusion_rule": "cell_center", "implicit_reprojection": False},
        "vector_source": {"path": str(vector), "sha256": vector_sha, "crs": vector_crs, "feature_count": len(payload["features"])},
        "raster_source": {"path": str(raster), "sha256": raster_sha, "crs": raster_crs},
        "features": rows,
        "state_counts": counts,
        "invariants": {
            "feature_accounting_closed": len(rows) == len(payload["features"]) == sum(counts.values()),
            "crs_exactly_matched": True,
            "nodata_separate_from_zero": True,
            "no_implicit_reprojection": True,
        },
        "warnings": ["GIS_VECTOR_RASTER_UNRESOLVED_FEATURES"] if counts["UNRESOLVED"] else [],
        "limitations": [
            "V1 vector-raster intersection uses raster cell-center inclusion, not fractional cell coverage.",
            "GeoJSON and raster CRS strings must match explicitly; no implicit reprojection occurs.",
            "NODATA cells are excluded from numeric summaries and retained separately.",
        ],
        "falsification_criteria": [
            "reject if feature accounting fails",
            "reject if CRS strings differ or are absent",
            "reject any summary that counts NODATA as numeric zero",
            "retain malformed geometries as explicit UNRESOLVED rows rather than silently dropping them",
        ],
    }
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"
    write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}
