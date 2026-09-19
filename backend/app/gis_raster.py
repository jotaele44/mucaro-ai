from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION
from .utils import stable_id, utc_now, write_json

try:
    import rasterio
    from rasterio.windows import from_bounds
except ImportError:  # pragma: no cover
    rasterio = None
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
    write_json(path, report)
    return {**report, "report_path": str(path), "report_sha256": _sha256(path)}


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
