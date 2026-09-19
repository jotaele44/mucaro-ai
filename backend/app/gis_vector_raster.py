from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

import rasterio
from rasterio.features import geometry_mask
from shapely.geometry import shape

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary
from .utils import stable_id, utc_now, write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _empty_feature_row(index: int, state: str, warnings: list[str], *, feature_id: Any = None) -> dict[str, Any]:
    return {
        "feature_index": index,
        "feature_id": feature_id,
        "state": state,
        "covered_cell_count": 0,
        "valid_cell_count": 0,
        "nodata_cell_count": 0,
        "zero_cell_count": 0,
        "sum": None,
        "minimum": None,
        "maximum": None,
        "mean": None,
        "median": None,
        "population_standard_deviation": None,
        "cell_accounting_closed": True,
        "warnings": warnings,
    }


def _build_vector_raster_report(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
    operation: str,
    status_prefix: str,
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
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_FEATURE"]))
                continue
            feature_id = feature.get("id")
            geometry = feature.get("geometry")
            if geometry is None:
                rows.append(_empty_feature_row(index, "NULL_EMPTY", [], feature_id=feature_id))
                continue
            dimensions = _coordinate_dimension_summary(geometry if isinstance(geometry, dict) else None)
            if dimensions["has_z"] or dimensions["has_m"]:
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["GIS_VECTOR_RASTER_DIMENSION_UNSUPPORTED"], feature_id=feature_id))
                continue
            try:
                geom = shape(geometry)
            except Exception:
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_GEOMETRY"], feature_id=feature_id))
                continue
            if geom.is_empty:
                rows.append(_empty_feature_row(index, "NULL_EMPTY", [], feature_id=feature_id))
                continue
            if not geom.is_valid:
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_TOPOLOGY"], feature_id=feature_id))
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
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_MASK"], feature_id=feature_id))
                continue
            covered = int(mask.sum())
            if covered == 0:
                rows.append(_empty_feature_row(index, "OUTSIDE_OR_NO_CELL_CENTER", [], feature_id=feature_id))
                continue
            masked_values = data[mask]
            valid = int(masked_values.count())
            nodata = covered - valid
            values = [float(v) for v in masked_values.compressed()]
            zero_count = sum(1 for value in values if value == 0.0)
            rows.append(
                {
                    "feature_index": index,
                    "feature_id": feature_id,
                    "state": "INTERSECTS_VALID" if valid else "NODATA_ONLY",
                    "covered_cell_count": covered,
                    "valid_cell_count": valid,
                    "nodata_cell_count": nodata,
                    "zero_cell_count": zero_count,
                    "sum": sum(values) if values else None,
                    "minimum": min(values) if values else None,
                    "maximum": max(values) if values else None,
                    "mean": statistics.fmean(values) if values else None,
                    "median": statistics.median(values) if values else None,
                    "population_standard_deviation": statistics.pstdev(values) if values else None,
                    "cell_accounting_closed": covered == valid + nodata and zero_count <= valid,
                    "warnings": [],
                }
            )

    states = ("INTERSECTS_VALID", "NODATA_ONLY", "OUTSIDE_OR_NO_CELL_CENTER", "NULL_EMPTY", "UNRESOLVED")
    counts = {state: sum(1 for row in rows if row["state"] == state) for state in states}
    created_at = utc_now()
    vector_sha = _sha256(vector)
    raster_sha = _sha256(raster)
    report_id = stable_id("gisreport", operation, vector_sha, raster_sha, str(band), created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": f"{status_prefix}_OK" if counts["UNRESOLVED"] == 0 else f"{status_prefix}_WARN",
        "operation": operation,
        "created_at": created_at,
        "parameters": {"band": band, "cell_inclusion_rule": "cell_center", "implicit_reprojection": False},
        "vector_source": {"path": str(vector), "sha256": vector_sha, "crs": vector_crs, "feature_count": len(payload["features"])},
        "raster_source": {"path": str(raster), "sha256": raster_sha, "crs": raster_crs},
        "features": rows,
        "state_counts": counts,
        "invariants": {
            "feature_accounting_closed": len(rows) == len(payload["features"]) == sum(counts.values()),
            "cell_accounting_closed": all(row["cell_accounting_closed"] is True for row in rows),
            "crs_exactly_matched": True,
            "nodata_separate_from_zero": True,
            "no_implicit_reprojection": True,
        },
        "warnings": ["GIS_VECTOR_RASTER_UNRESOLVED_FEATURES"] if counts["UNRESOLVED"] else [],
        "limitations": [
            "Bounded zonal statistics use raster cell-center inclusion, not fractional cell coverage.",
            "GeoJSON and raster CRS strings must match explicitly; no implicit reprojection occurs.",
            "NODATA cells are excluded from numeric summaries and retained separately.",
        ],
        "falsification_criteria": [
            "reject if feature accounting fails",
            "reject if any feature's covered-cell count does not equal valid plus NODATA cells",
            "reject if CRS strings differ or are absent",
            "reject any summary that counts NODATA as numeric zero",
            "retain malformed geometries as explicit UNRESOLVED rows rather than silently dropping them",
        ],
    }
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"
    write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}


def analyze_vector_raster_intersection(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
) -> dict[str, Any]:
    return _build_vector_raster_report(
        vector_path,
        raster_path,
        cfg=cfg,
        band=band,
        operation="analyze_vector_raster_intersection",
        status_prefix="GIS_VECTOR_RASTER",
    )


def compute_zonal_statistics(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
) -> dict[str, Any]:
    return _build_vector_raster_report(
        vector_path,
        raster_path,
        cfg=cfg,
        band=band,
        operation="compute_zonal_statistics",
        status_prefix="GIS_ZONAL_STATS",
    )
