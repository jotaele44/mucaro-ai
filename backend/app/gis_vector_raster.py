from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

import rasterio
from rasterio.errors import RasterioError
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.errors import ShapelyError
from shapely.geometry import Polygon, shape

from .gis_processing import GIS_REPORT_SCHEMA_VERSION, GISProcessingError, _coordinate_dimension_summary
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
        "covered_area": None,
        "valid_coverage_area": None,
        "nodata_coverage_area": None,
        "coverage_fraction_sum": None,
        "coverage_accounting_closed": None,
        "warnings": warnings,
    }


def _weighted_median(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    ordered = sorted(values, key=lambda item: item[0])
    total_weight = sum(weight for _, weight in ordered)
    if total_weight <= 0.0:
        return None
    midpoint = total_weight / 2.0
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= midpoint:
            return value
    return ordered[-1][0]


def _fractional_feature_row(
    geometry: Any,
    *,
    feature_index: int,
    feature_id: Any,
    dataset: Any,
    band_data: Any,
) -> dict[str, Any]:
    geom = shape(geometry)
    bounds_window = from_bounds(*geom.bounds, transform=dataset.transform)
    col_start = math.floor(bounds_window.col_off)
    row_start = math.floor(bounds_window.row_off)
    col_end = math.ceil(bounds_window.col_off + bounds_window.width)
    row_end = math.ceil(bounds_window.row_off + bounds_window.height)
    raster_window = Window(col_start, row_start, col_end - col_start, row_end - row_start)
    raster_window = raster_window.intersection(Window(0, 0, dataset.width, dataset.height))
    if raster_window.width <= 0 or raster_window.height <= 0:
        return _empty_feature_row(feature_index, "OUTSIDE_OR_NO_CELL_CENTER", [], feature_id=feature_id)

    window_data = dataset.read(band_data, masked=True, window=raster_window)
    window_transform = dataset.window_transform(raster_window)
    valid_values: list[tuple[float, float]] = []
    covered_cell_count = 0
    valid_cell_count = 0
    nodata_cell_count = 0
    zero_cell_count = 0
    covered_area = 0.0
    valid_coverage_area = 0.0
    nodata_coverage_area = 0.0
    coverage_fraction_sum = 0.0

    for local_row in range(window_data.shape[0]):
        for local_col in range(window_data.shape[1]):
            corners = [
                window_transform * (local_col, local_row),
                window_transform * (local_col + 1, local_row),
                window_transform * (local_col + 1, local_row + 1),
                window_transform * (local_col, local_row + 1),
            ]
            cell = Polygon(corners)
            cell_area = abs(float(cell.area))
            if cell_area <= 0.0:
                continue
            overlap_area = float(geom.intersection(cell).area)
            if overlap_area <= 1e-12:
                continue
            fraction = min(1.0, max(0.0, overlap_area / cell_area))
            covered_cell_count += 1
            covered_area += overlap_area
            coverage_fraction_sum += fraction
            value = window_data[local_row, local_col]
            if bool(getattr(value, "mask", False)):
                nodata_cell_count += 1
                nodata_coverage_area += overlap_area
                continue
            numeric_value = float(value)
            valid_cell_count += 1
            valid_coverage_area += overlap_area
            valid_values.append((numeric_value, overlap_area))
            if numeric_value == 0.0:
                zero_cell_count += 1

    if covered_cell_count == 0:
        return _empty_feature_row(feature_index, "OUTSIDE_OR_NO_CELL_CENTER", [], feature_id=feature_id)
    if valid_cell_count == 0:
        row = _empty_feature_row(feature_index, "NODATA_ONLY", [], feature_id=feature_id)
    else:
        weighted_sum = sum(value * weight for value, weight in valid_values)
        weighted_mean = weighted_sum / valid_coverage_area
        weighted_variance = sum(weight * (value - weighted_mean) ** 2 for value, weight in valid_values) / valid_coverage_area
        row = {
            "feature_index": feature_index,
            "feature_id": feature_id,
            "state": "INTERSECTS_VALID",
            "covered_cell_count": covered_cell_count,
            "valid_cell_count": valid_cell_count,
            "nodata_cell_count": nodata_cell_count,
            "zero_cell_count": zero_cell_count,
            "sum": weighted_sum,
            "minimum": min(value for value, _ in valid_values),
            "maximum": max(value for value, _ in valid_values),
            "mean": weighted_mean,
            "median": _weighted_median(valid_values),
            "population_standard_deviation": math.sqrt(max(0.0, weighted_variance)),
            "cell_accounting_closed": covered_cell_count == valid_cell_count + nodata_cell_count,
            "warnings": [],
        }
    row.update(
        {
            "covered_area": covered_area,
            "valid_coverage_area": valid_coverage_area,
            "nodata_coverage_area": nodata_coverage_area,
            "coverage_fraction_sum": coverage_fraction_sum,
            "coverage_accounting_closed": math.isclose(
                covered_area,
                valid_coverage_area + nodata_coverage_area,
                rel_tol=0.0,
                abs_tol=max(1e-9, abs(covered_area) * 1e-12),
            ),
        }
    )
    return row


def _build_vector_raster_report(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
    operation: str,
    status_prefix: str,
    inclusion_rule: str = "cell_center",
) -> dict[str, Any]:
    cfg.ensure_dirs()
    vector = vector_path.expanduser().resolve()
    raster = raster_path.expanduser().resolve()
    if not vector.exists() or not vector.is_file() or not raster.exists() or not raster.is_file():
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_NOT_FOUND", "Vector and raster inputs must both exist.")
    if inclusion_rule not in {"cell_center", "fractional_coverage"}:
        raise GISProcessingError("ERROR_GIS_VECTOR_RASTER_RULE", "inclusion_rule must be cell_center or fractional_coverage.")

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

    coverage_area_units: str | None = None
    with dataset:
        raster_crs = dataset.crs.to_string() if dataset.crs else None
        if not vector_crs or not raster_crs or vector_crs.upper() != raster_crs.upper():
            raise GISProcessingError(
                "ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH",
                f"Explicit equal CRS required; vector={vector_crs!r}, raster={raster_crs!r}. No implicit reprojection is permitted.",
            )
        if inclusion_rule == "fractional_coverage" and (dataset.crs is None or not dataset.crs.is_projected):
            raise GISProcessingError(
                "ERROR_GIS_VECTOR_RASTER_FRACTIONAL_CRS",
                "Fractional coverage requires a projected raster CRS with planar area units.",
            )
        if inclusion_rule == "fractional_coverage" and dataset.crs is not None:
            coverage_area_units = dataset.crs.linear_units
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
            except (ShapelyError, TypeError, ValueError):
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_GEOMETRY"], feature_id=feature_id))
                continue
            if geom.is_empty:
                rows.append(_empty_feature_row(index, "NULL_EMPTY", [], feature_id=feature_id))
                continue
            if not geom.is_valid:
                rows.append(_empty_feature_row(index, "UNRESOLVED", ["ERROR_GIS_VECTOR_RASTER_TOPOLOGY"], feature_id=feature_id))
                continue
            if inclusion_rule == "fractional_coverage":
                rows.append(
                    _fractional_feature_row(
                        geometry,
                        feature_index=index,
                        feature_id=feature_id,
                        dataset=dataset,
                        band_data=band,
                    )
                )
                continue
            try:
                mask = geometry_mask(
                    [geometry],
                    out_shape=(dataset.height, dataset.width),
                    transform=dataset.transform,
                    invert=True,
                    all_touched=False,
                )
            except (RasterioError, TypeError, ValueError):
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
        "parameters": {
            "band": band,
            "cell_inclusion_rule": inclusion_rule,
            "implicit_reprojection": False,
            "coverage_measurement": "planar_intersection_area" if inclusion_rule == "fractional_coverage" else None,
            "coverage_area_units": coverage_area_units,
            "weighted_statistics": inclusion_rule == "fractional_coverage",
        },
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
            "coverage_accounting_closed": inclusion_rule != "fractional_coverage"
            or all(row.get("coverage_accounting_closed") is True for row in rows),
        },
        "warnings": ["GIS_VECTOR_RASTER_UNRESOLVED_FEATURES"] if counts["UNRESOLVED"] else [],
        "limitations": [
            "Fractional coverage uses planar polygon-cell intersection areas and area-weighted statistics only for projected CRS inputs.",
            "The default cell-center rule remains available and is not equivalent to fractional coverage.",
            "GeoJSON and raster CRS strings must match explicitly; no implicit reprojection occurs.",
            "NODATA cells are excluded from numeric summaries and retained separately.",
        ],
        "falsification_criteria": [
            "reject if feature accounting fails",
            "reject if any feature's covered-cell count does not equal valid plus NODATA cells",
            "reject if CRS strings differ or are absent",
            "reject any summary that counts NODATA as numeric zero",
            "retain malformed geometries as explicit UNRESOLVED rows rather than silently dropping them",
            "reject fractional coverage for geographic CRS or when covered area does not close to valid plus NODATA area",
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
    inclusion_rule: str = "cell_center",
) -> dict[str, Any]:
    return _build_vector_raster_report(
        vector_path,
        raster_path,
        cfg=cfg,
        band=band,
        operation="analyze_vector_raster_intersection",
        status_prefix="GIS_VECTOR_RASTER",
        inclusion_rule=inclusion_rule,
    )


def compute_zonal_statistics(
    vector_path: Path,
    raster_path: Path,
    *,
    cfg: Any,
    band: int = 1,
    inclusion_rule: str = "cell_center",
) -> dict[str, Any]:
    return _build_vector_raster_report(
        vector_path,
        raster_path,
        cfg=cfg,
        band=band,
        operation="compute_zonal_statistics",
        status_prefix="GIS_ZONAL_STATS",
        inclusion_rule=inclusion_rule,
    )
