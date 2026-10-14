from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION
from .capability_contracts import require_gis_report_contract
from .utils import stable_id, utc_now, write_json

try:
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds
except ImportError:  # pragma: no cover
    np = None
    rasterio = None
    Resampling = None
    from_bounds = None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_rasterio() -> None:
    if rasterio is None:
        raise GISProcessingError("ERROR_GIS_RASTER_ENGINE_UNAVAILABLE", "rasterio is required for the bounded raster adapter.")


def _resolve_raster_source(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise GISProcessingError("ERROR_GIS_RASTER_NOT_FOUND", f"Raster input not found: {source}")
    return source


def _write_report(cfg: Any, report: dict[str, Any]) -> dict[str, Any]:
    path = cfg.export_dir / "gis_reports" / f"{report['report_id']}.json"
    require_gis_report_contract(report, report_path=str(path))
    write_json(path, report)
    return {**report, "report_path": str(path), "report_sha256": _sha256(path)}


def _band_value_receipts(dataset: Any) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for band in range(1, dataset.count + 1):
        values = dataset.read(band, masked=True)
        compressed = values.compressed()
        valid_count = int(values.count())
        receipts.append(
            {
                "band": band,
                "valid_cell_count": valid_count,
                "nodata_cell_count": int(values.size - valid_count),
                "zero_cell_count": int((compressed == 0).sum()),
            }
        )
    return receipts


def inspect_raster(path: Path, *, cfg: Any) -> dict[str, Any]:
    _require_rasterio(); cfg.ensure_dirs()
    source = _resolve_raster_source(path)
    source_sha = _sha256(source)
    try:
        with rasterio.open(source) as dataset:
            band_receipts = []
            for band in range(1, dataset.count + 1):
                values = dataset.read(band, masked=True)
                valid_count = int(values.count())
                nodata_count = int(values.size - valid_count)
                band_receipts.append({"band": band, "dtype": dataset.dtypes[band - 1], "nodata": dataset.nodatavals[band - 1], "valid_cell_count": valid_count, "nodata_cell_count": nodata_count})
            manifest = {"driver": dataset.driver, "width": dataset.width, "height": dataset.height, "band_count": dataset.count, "crs": dataset.crs.to_string() if dataset.crs else None, "transform": list(dataset.transform)[:6], "bounds": list(dataset.bounds), "dtypes": list(dataset.dtypes), "nodata_values": list(dataset.nodatavals), "bands": band_receipts}
    except Exception as exc:
        raise GISProcessingError("ERROR_GIS_RASTER_OPEN", f"Raster could not be inspected: {exc}") from exc
    created_at = utc_now(); report_id = stable_id("gisreport", "raster_inspect", source_sha, created_at)
    report = {"schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id, "status": "GIS_RASTER_INSPECT_OK", "operation": "inspect_raster", "created_at": created_at, "source": {"path": str(source), "sha256": source_sha, "bytes": source.stat().st_size}, "manifest": manifest, "invariants": {"band_accounting_closed": len(manifest["bands"]) == manifest["band_count"], "dimensions_positive": manifest["width"] > 0 and manifest["height"] > 0}, "warnings": ["GIS_RASTER_CRS_MISSING"] if manifest["crs"] is None else [], "limitations": ["V1 raster support is bounded to rasterio-readable local rasters; GeoTIFF is the certification fixture.", "NODATA is preserved separately from numeric zero.", "Inspection does not infer semantic meaning from band values."], "falsification_criteria": ["reject if source SHA-256 changes", "reject if band accounting fails", "do not treat NODATA as zero"]}
    return _write_report(cfg, report)


def sample_raster(path: Path, points: list[tuple[float, float]], *, cfg: Any) -> dict[str, Any]:
    _require_rasterio(); cfg.ensure_dirs(); source = _resolve_raster_source(path); source_sha = _sha256(source)
    if not points:
        raise GISProcessingError("ERROR_GIS_RASTER_POINTS", "At least one raster sample point is required.")
    try:
        points_valid = all(
            len(point) == 2 and all(math.isfinite(float(value)) for value in point)
            for point in points
        )
    except (TypeError, ValueError):
        points_valid = False
    if not points_valid:
        raise GISProcessingError("ERROR_GIS_RASTER_POINTS", "Raster sample points must contain two finite coordinates.")
    rows = []
    try:
        with rasterio.open(source) as dataset:
            for index, (x, y) in enumerate(points):
                if not (dataset.bounds.left <= x <= dataset.bounds.right and dataset.bounds.bottom <= y <= dataset.bounds.top):
                    rows.append({"point_index": index, "x": x, "y": y, "state": "OUTSIDE", "values": None}); continue
                values = next(dataset.sample([(x, y)], masked=True))
                if getattr(values, "mask", False) is not False and bool(getattr(values, "mask").all()):
                    rows.append({"point_index": index, "x": x, "y": y, "state": "NODATA", "values": None}); continue
                rows.append({"point_index": index, "x": x, "y": y, "state": "SAMPLED", "values": [float(v) for v in values]})
            crs = dataset.crs.to_string() if dataset.crs else None
    except Exception as exc:
        raise GISProcessingError("ERROR_GIS_RASTER_SAMPLE", f"Raster could not be sampled: {exc}") from exc
    created_at = utc_now(); report_id = stable_id("gisreport", "raster_sample", source_sha, str(points), created_at)
    counts = {state: sum(1 for row in rows if row["state"] == state) for state in ("SAMPLED", "NODATA", "OUTSIDE")}
    return _write_report(cfg, {"schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id, "status": "GIS_RASTER_SAMPLE_OK", "operation": "sample_raster", "created_at": created_at, "source": {"path": str(source), "sha256": source_sha, "crs": crs}, "samples": rows, "state_counts": counts, "invariants": {"point_accounting_closed": len(points) == sum(counts.values())}, "warnings": [], "limitations": ["Point coordinates must already be in the raster CRS; no implicit reprojection occurs."], "falsification_criteria": ["reject if point accounting fails", "reject samples when point CRS is not independently known to match raster CRS"]})


def clip_raster_bbox(path: Path, bbox: tuple[float, float, float, float], *, cfg: Any) -> dict[str, Any]:
    _require_rasterio(); cfg.ensure_dirs(); source = _resolve_raster_source(path); source_sha = _sha256(source)
    try:
        minx, miny, maxx, maxy = map(float, bbox)
    except (TypeError, ValueError) as exc:
        raise GISProcessingError("ERROR_GIS_RASTER_BBOX", "Raster clip bbox must contain four numeric values.") from exc
    if not all(math.isfinite(value) for value in (minx, miny, maxx, maxy)):
        raise GISProcessingError("ERROR_GIS_RASTER_BBOX", "Raster clip bbox values must be finite.")
    if minx >= maxx or miny >= maxy:
        raise GISProcessingError("ERROR_GIS_RASTER_BBOX", "Raster clip bbox must have positive width and height.")
    created_at = utc_now(); report_id = stable_id("gisreport", "raster_clip", source_sha, str(bbox), created_at)
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(source) as dataset:
            overlap = (max(minx, dataset.bounds.left), max(miny, dataset.bounds.bottom), min(maxx, dataset.bounds.right), min(maxy, dataset.bounds.top))
            if overlap[0] >= overlap[2] or overlap[1] >= overlap[3]:
                raise GISProcessingError("ERROR_GIS_RASTER_CLIP_OUTSIDE", "Clip bbox does not intersect the raster extent.")
            window = from_bounds(*overlap, transform=dataset.transform).round_offsets().round_lengths()
            if int(window.width) <= 0 or int(window.height) <= 0:
                raise GISProcessingError("ERROR_GIS_RASTER_CLIP_EMPTY", "Clip bbox produced an empty raster window.")
            data = dataset.read(window=window)
            profile = dataset.profile.copy()
            profile.update(
                width=int(window.width),
                height=int(window.height),
                transform=dataset.window_transform(window),
                tiled=False,
            )
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target: target.write(data)
            crs = dataset.crs.to_string() if dataset.crs else None
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_RASTER_CLIP", f"Raster could not be clipped: {exc}") from exc
    output_sha = _sha256(output_path)
    return _write_report(cfg, {"schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id, "status": "GIS_RASTER_CLIP_OK", "operation": "clip_raster_bbox", "created_at": created_at, "source": {"path": str(source), "sha256": source_sha}, "parameters": {"requested_bbox": [minx, miny, maxx, maxy], "effective_bbox": list(overlap)}, "output": {"path": str(output_path), "sha256": output_sha, "crs": crs, "bytes": output_path.stat().st_size}, "invariants": {"source_and_output_hash_present": bool(source_sha and output_sha), "crs_preserved": True}, "warnings": [], "limitations": ["Clip is axis-aligned in the raster CRS and does not reproject."], "falsification_criteria": ["reject if output SHA-256 changes", "reject if CRS is not preserved"]})


def resample_raster(
    path: Path,
    *,
    width: int,
    height: int,
    method: str,
    cfg: Any,
) -> dict[str, Any]:
    _require_rasterio(); cfg.ensure_dirs(); source = _resolve_raster_source(path); source_sha = _sha256(source)
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
        or width * height > 50_000_000
    ):
        raise GISProcessingError(
            "ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS",
            "Resample width and height must be positive integers with at most 50,000,000 output cells per band.",
        )
    normalized_method = str(method).lower()
    methods = {"nearest": Resampling.nearest, "bilinear": Resampling.bilinear}
    if normalized_method not in methods:
        raise GISProcessingError(
            "ERROR_GIS_RASTER_RESAMPLE_METHOD",
            "Resampling method must be one of: nearest, bilinear.",
        )

    created_at = utc_now()
    report_id = stable_id("gisreport", "raster_resample", source_sha, str(width), str(height), normalized_method, created_at)
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(source) as dataset:
            if dataset.crs is None:
                raise GISProcessingError(
                    "ERROR_GIS_RASTER_RESAMPLE_CRS",
                    "A declared raster CRS is required to certify resampling provenance.",
                )
            source_transform = dataset.transform
            source_bounds = list(dataset.bounds)
            source_crs = dataset.crs.to_string()
            source_nodata = list(dataset.nodatavals)
            source_width = dataset.width
            source_height = dataset.height
            source_count = dataset.count
            if width * height * source_count > 50_000_000:
                raise GISProcessingError(
                    "ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS",
                    "Requested dimensions exceed the 50,000,000 output-value limit after band accounting.",
                )
            source_bands = _band_value_receipts(dataset)
            data = dataset.read(
                out_shape=(dataset.count, height, width),
                resampling=methods[normalized_method],
            )
            output_transform = dataset.transform @ dataset.transform.scale(
                dataset.width / width,
                dataset.height / height,
            )
            profile = dataset.profile.copy()
            profile.update(width=width, height=height, transform=output_transform, tiled=False)
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target:
                target.write(data)
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_RASTER_RESAMPLE", f"Raster could not be resampled: {exc}") from exc

    try:
        with rasterio.open(output_path) as output:
            output_bounds = list(output.bounds)
            output_crs = output.crs.to_string() if output.crs else None
            output_nodata = list(output.nodatavals)
            output_count = output.count
            output_width = output.width
            output_height = output.height
            output_dtypes = list(output.dtypes)
            output_transform_values = list(output.transform)[:6]
            output_bands = _band_value_receipts(output)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_RASTER_RESAMPLE", f"Resampled raster could not be verified: {exc}") from exc

    output_sha = _sha256(output_path)
    bounds_preserved = all(abs(left - right) <= 1e-9 for left, right in zip(source_bounds, output_bounds, strict=True))
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_RASTER_RESAMPLE_OK",
        "operation": "resample_raster",
        "created_at": created_at,
        "source": {
            "path": str(source),
            "sha256": source_sha,
            "bytes": source.stat().st_size,
            "crs": source_crs,
            "width": source_width,
            "height": source_height,
            "band_count": source_count,
            "transform": list(source_transform)[:6],
            "bounds": source_bounds,
            "nodata_values": source_nodata,
            "bands": source_bands,
        },
        "parameters": {"width": width, "height": height, "method": normalized_method},
        "output": {
            "path": str(output_path),
            "sha256": output_sha,
            "bytes": output_path.stat().st_size,
            "crs": output_crs,
            "width": output_width,
            "height": output_height,
            "band_count": output_count,
            "dtypes": output_dtypes,
            "transform": output_transform_values,
            "bounds": output_bounds,
            "nodata_values": output_nodata,
            "bands": output_bands,
        },
        "invariants": {
            "source_and_output_hash_present": bool(source_sha and output_sha),
            "dimensions_exact": output_width == width and output_height == height,
            "crs_preserved": output_crs == source_crs,
            "bounds_preserved": bounds_preserved,
            "band_count_preserved": output_count == source_count,
            "nodata_definition_preserved": output_nodata == source_nodata,
            "nodata_accounting_recorded": all(
                row["valid_cell_count"] + row["nodata_cell_count"] == output_width * output_height
                and row["zero_cell_count"] <= row["valid_cell_count"]
                for row in output_bands
            ),
            "transactional_output_published": output_path.exists() and not temporary_output_path.exists(),
        },
        "warnings": [],
        "limitations": [
            "Resampling is bounded to nearest-neighbor and bilinear methods over the source raster's existing extent and CRS.",
            "Resampling changes the grid manifestation and does not establish semantic equivalence with the source pixels.",
            "Bilinear interpolation may create values not present in the source; NODATA remains a distinct declared state.",
        ],
        "falsification_criteria": [
            "reject if output dimensions differ from the explicit request",
            "reject if CRS, extent, band count, or NODATA declaration changes",
            "reject if the output hash is absent or the temporary artifact remains",
        ],
    }
    return _write_report(cfg, report)


def _terrain_band_receipt(values: Any, valid_mask: Any, *, band: int, name: str) -> dict[str, Any]:
    valid_values = values[valid_mask]
    valid_count = int(valid_mask.sum())
    return {
        "band": band,
        "name": name,
        "valid_cell_count": valid_count,
        "nodata_cell_count": int(values.size - valid_count),
        "zero_cell_count": int((valid_values == 0).sum()),
        "minimum": float(valid_values.min()) if valid_count else None,
        "maximum": float(valid_values.max()) if valid_count else None,
        "mean": float(valid_values.mean()) if valid_count else None,
    }


def derive_raster_terrain(
    path: Path,
    *,
    band: int = 1,
    elevation_units: str,
    cfg: Any,
) -> dict[str, Any]:
    _require_rasterio()
    cfg.ensure_dirs()
    source = _resolve_raster_source(path)
    source_sha = _sha256(source)
    if not isinstance(band, int) or isinstance(band, bool) or band < 1:
        raise GISProcessingError("ERROR_GIS_TERRAIN_BAND", "Terrain band must be a positive one-based integer.")
    if elevation_units != "meters":
        raise GISProcessingError(
            "ERROR_GIS_TERRAIN_UNITS",
            "Terrain derivation requires an explicit elevation_units value of meters.",
        )

    created_at = utc_now()
    report_id = stable_id("gisreport", "raster_terrain", source_sha, str(band), elevation_units, created_at)
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_nodata = -9999.0

    try:
        with rasterio.open(source) as dataset:
            if band > dataset.count:
                raise GISProcessingError("ERROR_GIS_TERRAIN_BAND", f"band must be between 1 and {dataset.count}.")
            if dataset.crs is None or not dataset.crs.is_projected:
                raise GISProcessingError(
                    "ERROR_GIS_TERRAIN_CRS",
                    "Terrain derivation requires a declared projected CRS; geographic angular grids are rejected.",
                )
            try:
                horizontal_unit_name, horizontal_unit_factor = dataset.crs.linear_units_factor
            except Exception as exc:
                raise GISProcessingError(
                    "ERROR_GIS_TERRAIN_CRS",
                    "Projected CRS linear units could not be resolved.",
                ) from exc
            if str(horizontal_unit_name).lower() not in {"metre", "meter", "metres", "meters"} or not math.isclose(
                float(horizontal_unit_factor), 1.0, rel_tol=0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_TERRAIN_UNITS",
                    "Terrain derivation requires projected horizontal units with a one-metre conversion factor.",
                )
            transform = dataset.transform
            if not math.isclose(float(transform.b), 0.0, abs_tol=1e-12) or not math.isclose(
                float(transform.d), 0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_TERRAIN_TRANSFORM",
                    "Terrain derivation requires an axis-aligned raster transform without rotation or shear.",
                )
            x_resolution = abs(float(transform.a))
            y_resolution = abs(float(transform.e))
            if x_resolution <= 0 or y_resolution <= 0:
                raise GISProcessingError("ERROR_GIS_TERRAIN_TRANSFORM", "Terrain pixel resolutions must be positive.")
            if dataset.width < 3 or dataset.height < 3 or dataset.width * dataset.height > 50_000_000:
                raise GISProcessingError(
                    "ERROR_GIS_TERRAIN_DIMENSIONS",
                    "Terrain input must be at least 3x3 and contain at most 50,000,000 cells.",
                )

            source_values = dataset.read(band, masked=True)
            source_data = np.asarray(source_values.data, dtype="float64")
            source_invalid = np.ma.getmaskarray(source_values) | ~np.isfinite(source_data)
            source_valid = ~source_invalid
            source_valid_count = int(source_valid.sum())
            source_nodata_count = int(source_data.size - source_valid_count)

            interior_valid = np.ones((dataset.height - 2, dataset.width - 2), dtype=bool)
            for row_offset in range(3):
                for column_offset in range(3):
                    interior_valid &= source_valid[
                        row_offset : row_offset + dataset.height - 2,
                        column_offset : column_offset + dataset.width - 2,
                    ]
            neighborhood_valid = np.zeros(source_data.shape, dtype=bool)
            neighborhood_valid[1:-1, 1:-1] = interior_valid

            gradient_row, gradient_x = np.gradient(source_data, y_resolution, x_resolution)
            gradient_north = -gradient_row if transform.e < 0 else gradient_row
            gradient_magnitude = np.hypot(gradient_x, gradient_north)
            slope_values = np.full(source_data.shape, output_nodata, dtype="float32")
            aspect_values = np.full(source_data.shape, output_nodata, dtype="float32")
            slope_values[neighborhood_valid] = np.degrees(
                np.arctan(gradient_magnitude[neighborhood_valid])
            ).astype("float32")
            flat_mask = neighborhood_valid & np.isclose(gradient_magnitude, 0.0, rtol=0.0, atol=1e-12)
            aspect_valid = neighborhood_valid & ~flat_mask
            aspect_values[aspect_valid] = (
                np.degrees(np.arctan2(-gradient_x[aspect_valid], -gradient_north[aspect_valid])) + 360.0
            ).astype("float32") % 360.0

            source_crs = dataset.crs.to_string()
            source_transform = list(transform)[:6]
            source_bounds = list(dataset.bounds)
            source_width = dataset.width
            source_height = dataset.height
            source_band_count = dataset.count
            source_nodata = dataset.nodatavals[band - 1]
            profile = dataset.profile.copy()
            profile.update(
                driver="GTiff",
                count=2,
                dtype="float32",
                nodata=output_nodata,
                tiled=False,
            )
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target:
                target.write(slope_values, 1)
                target.write(aspect_values, 2)
                target.set_band_description(1, "slope_degrees")
                target.set_band_description(2, "aspect_degrees_clockwise_from_north")
                target.update_tags(
                    terrain_adapter="numpy_gradient_3x3_complete_neighborhood_v1",
                    elevation_units=elevation_units,
                    source_sha256=source_sha,
                )
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_TERRAIN", f"Terrain raster could not be derived: {exc}") from exc

    try:
        with rasterio.open(output_path) as output:
            output_crs = output.crs.to_string() if output.crs else None
            output_transform = list(output.transform)[:6]
            output_bounds = list(output.bounds)
            output_width = output.width
            output_height = output.height
            output_band_count = output.count
            output_dtypes = list(output.dtypes)
            output_nodata_values = list(output.nodatavals)
            output_descriptions = list(output.descriptions)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_TERRAIN", f"Derived terrain raster could not be verified: {exc}") from exc

    slope_receipt = _terrain_band_receipt(slope_values, neighborhood_valid, band=1, name="slope_degrees")
    aspect_receipt = _terrain_band_receipt(
        aspect_values,
        aspect_valid,
        band=2,
        name="aspect_degrees_clockwise_from_north",
    )
    output_sha = _sha256(output_path)
    total_cells = source_width * source_height
    neighborhood_valid_count = int(neighborhood_valid.sum())
    neighborhood_excluded_count = total_cells - neighborhood_valid_count
    flat_cell_count = int(flat_mask.sum())
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_RASTER_TERRAIN_OK",
        "operation": "derive_raster_terrain",
        "created_at": created_at,
        "source": {
            "path": str(source),
            "sha256": source_sha,
            "bytes": source.stat().st_size,
            "crs": source_crs,
            "width": source_width,
            "height": source_height,
            "band_count": source_band_count,
            "selected_band": band,
            "transform": source_transform,
            "bounds": source_bounds,
            "nodata": source_nodata,
            "valid_cell_count": source_valid_count,
            "nodata_cell_count": source_nodata_count,
        },
        "parameters": {
            "adapter": "numpy_gradient_3x3_complete_neighborhood_v1",
            "band": band,
            "elevation_units": elevation_units,
            "horizontal_units": "meters",
            "horizontal_unit_name": str(horizontal_unit_name),
            "horizontal_unit_factor": float(horizontal_unit_factor),
            "x_resolution": x_resolution,
            "y_resolution": y_resolution,
            "slope_units": "degrees",
            "aspect_units": "degrees_clockwise_from_north",
            "flat_aspect_policy": "NODATA",
            "neighborhood_policy": "complete_3x3",
        },
        "output": {
            "path": str(output_path),
            "sha256": output_sha,
            "bytes": output_path.stat().st_size,
            "crs": output_crs,
            "width": output_width,
            "height": output_height,
            "band_count": output_band_count,
            "dtypes": output_dtypes,
            "nodata_values": output_nodata_values,
            "descriptions": output_descriptions,
            "transform": output_transform,
            "bounds": output_bounds,
            "bands": [slope_receipt, aspect_receipt],
        },
        "terrain_accounting": {
            "total_cell_count": total_cells,
            "source_valid_cell_count": source_valid_count,
            "source_nodata_cell_count": source_nodata_count,
            "neighborhood_valid_cell_count": neighborhood_valid_count,
            "neighborhood_excluded_cell_count": neighborhood_excluded_count,
            "flat_cell_count": flat_cell_count,
            "slope_valid_cell_count": slope_receipt["valid_cell_count"],
            "aspect_valid_cell_count": aspect_receipt["valid_cell_count"],
        },
        "invariants": {
            "source_and_output_hash_present": bool(source_sha and output_sha),
            "projected_crs_required": True,
            "horizontal_units_metre": True,
            "vertical_units_metre": elevation_units == "meters",
            "axis_aligned_transform": True,
            "dimensions_preserved": output_width == source_width and output_height == source_height,
            "crs_preserved": output_crs == source_crs,
            "transform_preserved": output_transform == source_transform,
            "bounds_preserved": output_bounds == source_bounds,
            "output_band_count_exact": output_band_count == 2,
            "source_cell_accounting_closed": source_valid_count + source_nodata_count == total_cells,
            "slope_cell_accounting_closed": slope_receipt["valid_cell_count"] + slope_receipt["nodata_cell_count"] == total_cells,
            "aspect_cell_accounting_closed": aspect_receipt["valid_cell_count"] + aspect_receipt["nodata_cell_count"] == total_cells,
            "neighborhood_accounting_closed": neighborhood_valid_count + neighborhood_excluded_count == total_cells,
            "flat_aspect_accounting_closed": aspect_receipt["valid_cell_count"] + flat_cell_count + neighborhood_excluded_count == total_cells,
            "transactional_output_published": output_path.exists() and not temporary_output_path.exists(),
        },
        "warnings": [],
        "limitations": [
            "Terrain derivation is bounded to an axis-aligned projected metre grid and one explicitly selected elevation band in metres.",
            "Slope and aspect use NumPy central differences; only cells with a complete valid 3x3 neighborhood receive output values.",
            "Perimeter cells and cells adjacent to source NODATA are output as NODATA; flat cells have slope zero and aspect NODATA.",
            "This operation does not perform smoothing, scale-dependent generalization, curvature, hillshade, contouring, or hydrologic conditioning.",
        ],
        "falsification_criteria": [
            "reject geographic, non-metre, rotated, sheared, smaller-than-3x3, or oversized source grids",
            "reject if the selected source band or explicit elevation units are invalid",
            "reject if source, neighborhood, slope, aspect, or flat-cell arithmetic does not close",
            "reject if CRS, transform, bounds, dimensions, output band semantics, or transactional publication differ",
        ],
    }
    return _write_report(cfg, report)


def _hillshade_parameter(
    value: Any,
    *,
    name: str,
    minimum: float,
    maximum: float,
    minimum_inclusive: bool,
) -> float:
    status = f"ERROR_GIS_HILLSHADE_{name.upper()}"
    if isinstance(value, bool):
        raise GISProcessingError(status, f"Hillshade {name} must be a finite number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise GISProcessingError(status, f"Hillshade {name} must be a finite number.") from exc
    minimum_valid = normalized >= minimum if minimum_inclusive else normalized > minimum
    if not math.isfinite(normalized) or not minimum_valid or normalized > maximum:
        lower_bound = "inclusive" if minimum_inclusive else "exclusive"
        raise GISProcessingError(
            status,
            f"Hillshade {name} must be finite and within {minimum} ({lower_bound}) to {maximum} (inclusive).",
        )
    return normalized


def derive_raster_hillshade(
    path: Path,
    *,
    band: int = 1,
    elevation_units: str,
    azimuth_degrees: float = 315.0,
    altitude_degrees: float = 45.0,
    cfg: Any,
) -> dict[str, Any]:
    _require_rasterio()
    cfg.ensure_dirs()
    azimuth = _hillshade_parameter(
        azimuth_degrees,
        name="azimuth",
        minimum=0.0,
        maximum=360.0,
        minimum_inclusive=True,
    )
    if azimuth == 360.0:
        raise GISProcessingError(
            "ERROR_GIS_HILLSHADE_AZIMUTH",
            "Hillshade azimuth must be greater than or equal to 0 and less than 360 degrees.",
        )
    altitude = _hillshade_parameter(
        altitude_degrees,
        name="altitude",
        minimum=0.0,
        maximum=90.0,
        minimum_inclusive=False,
    )

    terrain_report = derive_raster_terrain(
        path,
        band=band,
        elevation_units=elevation_units,
        cfg=cfg,
    )
    source_sha = str(terrain_report["source"]["sha256"])
    terrain_path = Path(str(terrain_report["output"]["path"]))
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "raster_hillshade",
        source_sha,
        str(band),
        elevation_units,
        str(azimuth),
        str(altitude),
        created_at,
    )
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_nodata = -9999.0

    try:
        with rasterio.open(terrain_path) as terrain:
            if terrain.count != 2 or terrain.descriptions != (
                "slope_degrees",
                "aspect_degrees_clockwise_from_north",
            ):
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_TERRAIN",
                    "Hillshade requires the certified two-band slope/aspect terrain manifestation.",
                )
            slope = terrain.read(1, masked=True)
            aspect = terrain.read(2, masked=True)
            slope_data = np.asarray(slope.data, dtype="float64")
            aspect_data = np.asarray(aspect.data, dtype="float64")
            slope_valid = ~np.ma.getmaskarray(slope) & np.isfinite(slope_data)
            aspect_valid = ~np.ma.getmaskarray(aspect) & np.isfinite(aspect_data)
            flat_mask = slope_valid & ~aspect_valid & np.isclose(
                slope_data,
                0.0,
                rtol=0.0,
                atol=1e-6,
            )
            unexpected_aspect_state = (aspect_valid & ~slope_valid) | (
                slope_valid & ~aspect_valid & ~flat_mask
            )
            if bool(unexpected_aspect_state.any()):
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_TERRAIN",
                    "Slope/aspect masks do not match the certified flat-aspect policy.",
                )
            terrain_accounting = terrain_report["terrain_accounting"]
            slope_valid_count = int(slope_valid.sum())
            aspect_valid_count = int(aspect_valid.sum())
            flat_cell_count = int(flat_mask.sum())
            if slope_valid_count == 0:
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_EMPTY",
                    "Hillshade requires at least one complete valid terrain neighborhood.",
                )
            if (
                slope_valid_count != terrain_accounting["slope_valid_cell_count"]
                or aspect_valid_count != terrain_accounting["aspect_valid_cell_count"]
                or flat_cell_count != terrain_accounting["flat_cell_count"]
            ):
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_TERRAIN",
                    "Slope/aspect masks do not match the bound terrain receipt accounting.",
                )
            if bool(((slope_data[slope_valid] < 0.0) | (slope_data[slope_valid] > 90.0)).any()):
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_TERRAIN",
                    "Slope values must remain within 0 through 90 degrees.",
                )
            if bool(((aspect_data[aspect_valid] < 0.0) | (aspect_data[aspect_valid] >= 360.0)).any()):
                raise GISProcessingError(
                    "ERROR_GIS_HILLSHADE_TERRAIN",
                    "Aspect values must remain within 0 inclusive through 360 exclusive degrees.",
                )

            slope_radians = np.radians(slope_data)
            aspect_radians = np.radians(np.where(aspect_valid, aspect_data, 0.0))
            azimuth_radians = math.radians(azimuth)
            zenith_radians = math.radians(90.0 - altitude)
            cosine_incidence = (
                math.cos(zenith_radians) * np.cos(slope_radians)
                + math.sin(zenith_radians)
                * np.sin(slope_radians)
                * np.cos(azimuth_radians - aspect_radians)
            )
            hillshade_values = np.full(slope_data.shape, output_nodata, dtype="float32")
            hillshade_values[slope_valid] = (
                255.0 * np.clip(cosine_incidence[slope_valid], 0.0, 1.0)
            ).astype("float32")

            profile = terrain.profile.copy()
            profile.update(count=1, dtype="float32", nodata=output_nodata, tiled=False)
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target:
                target.write(hillshade_values, 1)
                target.set_band_description(1, "hillshade_0_255")
                target.update_tags(
                    hillshade_adapter="terrain_slope_aspect_cosine_v1",
                    azimuth_degrees_clockwise_from_north=str(azimuth),
                    altitude_degrees_above_horizon=str(altitude),
                    source_sha256=source_sha,
                    terrain_sha256=str(terrain_report["output"]["sha256"]),
                )
            output_crs = terrain.crs.to_string() if terrain.crs else None
            output_transform = list(terrain.transform)[:6]
            output_bounds = list(terrain.bounds)
            output_width = terrain.width
            output_height = terrain.height
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_HILLSHADE", f"Hillshade raster could not be derived: {exc}") from exc

    try:
        with rasterio.open(output_path) as output:
            verified_crs = output.crs.to_string() if output.crs else None
            verified_transform = list(output.transform)[:6]
            verified_bounds = list(output.bounds)
            output_band_count = output.count
            output_dtypes = list(output.dtypes)
            output_nodata_values = list(output.nodatavals)
            output_descriptions = list(output.descriptions)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_HILLSHADE", f"Derived hillshade raster could not be verified: {exc}") from exc

    hillshade_receipt = _terrain_band_receipt(
        hillshade_values,
        slope_valid,
        band=1,
        name="hillshade_0_255",
    )
    valid_values = hillshade_values[slope_valid]
    total_cells = output_width * output_height
    valid_cell_count = int(slope_valid.sum())
    nodata_cell_count = total_cells - valid_cell_count
    shadow_cell_count = int(np.isclose(valid_values, 0.0, rtol=0.0, atol=1e-6).sum())
    fully_lit_cell_count = int(np.isclose(valid_values, 255.0, rtol=0.0, atol=1e-5).sum())
    output_sha = _sha256(output_path)
    source = terrain_report["source"]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_RASTER_HILLSHADE_OK",
        "operation": "derive_raster_hillshade",
        "created_at": created_at,
        "source": source,
        "parameters": {
            "adapter": "terrain_slope_aspect_cosine_v1",
            "band": band,
            "elevation_units": elevation_units,
            "azimuth_degrees_clockwise_from_north": azimuth,
            "altitude_degrees_above_horizon": altitude,
            "illumination_model": "lambertian_direct_light_clamped",
            "output_scale": [0.0, 255.0],
            "neighborhood_policy": "complete_3x3",
            "nodata_policy": "preserve_terrain_neighborhood_mask",
        },
        "terrain_lineage": {
            "report_id": terrain_report["report_id"],
            "report_path": terrain_report["report_path"],
            "report_sha256": terrain_report["report_sha256"],
            "source_sha256": terrain_report["source"]["sha256"],
            "output_path": str(terrain_path),
            "output_sha256": terrain_report["output"]["sha256"],
            "adapter": terrain_report["parameters"]["adapter"],
            "flat_aspect_policy": terrain_report["parameters"]["flat_aspect_policy"],
        },
        "output": {
            "path": str(output_path),
            "sha256": output_sha,
            "bytes": output_path.stat().st_size,
            "crs": verified_crs,
            "width": output_width,
            "height": output_height,
            "band_count": output_band_count,
            "dtypes": output_dtypes,
            "nodata_values": output_nodata_values,
            "descriptions": output_descriptions,
            "transform": verified_transform,
            "bounds": verified_bounds,
            "bands": [hillshade_receipt],
        },
        "hillshade_accounting": {
            "total_cell_count": total_cells,
            "valid_cell_count": valid_cell_count,
            "nodata_cell_count": nodata_cell_count,
            "flat_cell_count": flat_cell_count,
            "shadow_cell_count": shadow_cell_count,
            "illuminated_cell_count": valid_cell_count - shadow_cell_count,
            "fully_lit_cell_count": fully_lit_cell_count,
        },
        "invariants": {
            "source_and_output_hash_present": bool(source_sha and output_sha),
            "terrain_lineage_hashes_present": bool(
                terrain_report["report_sha256"] and terrain_report["output"]["sha256"]
            ),
            "terrain_source_identity_bound": terrain_report["source"]["sha256"] == source_sha,
            "dimensions_preserved": output_width == source["width"] and output_height == source["height"],
            "crs_preserved": verified_crs == source["crs"] == output_crs,
            "transform_preserved": verified_transform == source["transform"] == output_transform,
            "bounds_preserved": verified_bounds == source["bounds"] == output_bounds,
            "output_band_count_exact": output_band_count == 1,
            "output_semantics_exact": output_descriptions == ["hillshade_0_255"],
            "cell_accounting_closed": valid_cell_count + nodata_cell_count == total_cells,
            "illumination_accounting_closed": shadow_cell_count + (valid_cell_count - shadow_cell_count)
            == valid_cell_count,
            "hillshade_range_bounded": bool(
                valid_cell_count
                and float(valid_values.min()) >= 0.0
                and float(valid_values.max()) <= 255.0
            ),
            "terrain_validity_mask_preserved": valid_cell_count
            == terrain_report["terrain_accounting"]["slope_valid_cell_count"],
            "transactional_output_published": output_path.exists() and not temporary_output_path.exists(),
        },
        "warnings": [],
        "limitations": [
            "Hillshade is derived from the certified slope/aspect manifestation and inherits its projected-metre, vertical-metre, axis-aligned, and complete-neighborhood limits.",
            "The bounded illumination model uses direct Lambertian light only; it does not model cast shadows, ambient light, atmospheric effects, or multi-directional shading.",
            "The 0..255 values remain float32 so zero-valued shadow stays distinct from -9999 NODATA.",
            "Hillshade is a visualization derivative and must not be interpreted as elevation, slope, land cover, or measured radiance.",
        ],
        "falsification_criteria": [
            "reject azimuth outside [0,360) or altitude outside (0,90]",
            "reject if certified terrain lineage hashes or source identity are absent",
            "reject if the terrain slope/aspect mask violates the flat-aspect policy",
            "reject if output values leave 0..255 or valid/NODATA arithmetic does not close",
            "reject if CRS, transform, bounds, dimensions, band semantics, or transactional publication differ",
        ],
    }
    return _write_report(cfg, report)
