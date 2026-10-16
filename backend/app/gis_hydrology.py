from __future__ import annotations

import hashlib
import heapq
import math
from pathlib import Path
from typing import Any

from .gis_processing import GIS_REPORT_SCHEMA_VERSION, GISProcessingError
from .capability_contracts import require_gis_report_contract
from .utils import stable_id, utc_now, write_json

try:
    import numpy as np
    import rasterio
except ImportError:  # pragma: no cover
    np = None
    rasterio = None


D8_STATE_CODEBOOK = {
    "FLOW_UNIQUE": 1,
    "BOUNDARY_TERMINAL": 2,
    "SINK_UNRESOLVED": 3,
    "FLAT_UNRESOLVED": 4,
    "TIE_UNRESOLVED": 5,
}
D8_DIRECTION_CODEBOOK = {
    "E": 1,
    "SE": 2,
    "S": 4,
    "SW": 8,
    "W": 16,
    "NW": 32,
    "N": 64,
    "NE": 128,
}
D8_OUTPUT_NODATA = -9999.0
D8_MAX_CELLS = 1_000_000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_rasterio() -> None:
    if np is None or rasterio is None:
        raise GISProcessingError(
            "ERROR_GIS_D8_ENGINE_UNAVAILABLE",
            "rasterio and NumPy are required for the bounded D8 hydrology adapter.",
        )


def _resolve_source(path: Path) -> Path:
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise GISProcessingError("ERROR_GIS_D8_NOT_FOUND", f"Elevation raster not found: {source}")
    return source


def _write_report(cfg: Any, report: dict[str, Any]) -> dict[str, Any]:
    path = cfg.export_dir / "gis_reports" / f"{report['report_id']}.json"
    require_gis_report_contract(report, report_path=str(path))
    write_json(path, report)
    return {**report, "report_path": str(path), "report_sha256": _sha256(path)}


def _finite_nonnegative(
    value: Any,
    *,
    name: str,
    status: str = "ERROR_GIS_D8_TOLERANCE",
) -> float:
    if isinstance(value, bool):
        raise GISProcessingError(status, f"{name} must be a finite non-negative number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise GISProcessingError(status, f"{name} must be a finite non-negative number.") from exc
    if not math.isfinite(normalized) or normalized < 0.0:
        raise GISProcessingError(status, f"{name} must be a finite non-negative number.")
    return normalized


def _band_receipt(values: Any, valid_mask: Any, *, band: int, name: str) -> dict[str, Any]:
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


def _direction_rows(transform: Any) -> list[tuple[str, int, int, int, float]]:
    x_resolution = abs(float(transform.a))
    y_resolution = abs(float(transform.e))
    east_column = 1 if transform.a > 0 else -1
    north_row = -1 if transform.e < 0 else 1
    south_row = -north_row
    west_column = -east_column
    diagonal = math.hypot(x_resolution, y_resolution)
    return [
        ("E", 0, east_column, D8_DIRECTION_CODEBOOK["E"], x_resolution),
        ("SE", south_row, east_column, D8_DIRECTION_CODEBOOK["SE"], diagonal),
        ("S", south_row, 0, D8_DIRECTION_CODEBOOK["S"], y_resolution),
        ("SW", south_row, west_column, D8_DIRECTION_CODEBOOK["SW"], diagonal),
        ("W", 0, west_column, D8_DIRECTION_CODEBOOK["W"], x_resolution),
        ("NW", north_row, west_column, D8_DIRECTION_CODEBOOK["NW"], diagonal),
        ("N", north_row, 0, D8_DIRECTION_CODEBOOK["N"], y_resolution),
        ("NE", north_row, east_column, D8_DIRECTION_CODEBOOK["NE"], diagonal),
    ]


def _component_count(mask: Any) -> int:
    visited = np.zeros(mask.shape, dtype=bool)
    count = 0
    height, width = mask.shape
    for start_row, start_column in np.argwhere(mask):
        row = int(start_row)
        column = int(start_column)
        if visited[row, column]:
            continue
        count += 1
        visited[row, column] = True
        stack = [(row, column)]
        while stack:
            current_row, current_column = stack.pop()
            for row_delta in (-1, 0, 1):
                for column_delta in (-1, 0, 1):
                    if row_delta == 0 and column_delta == 0:
                        continue
                    neighbor_row = current_row + row_delta
                    neighbor_column = current_column + column_delta
                    if not (0 <= neighbor_row < height and 0 <= neighbor_column < width):
                        continue
                    if mask[neighbor_row, neighbor_column] and not visited[neighbor_row, neighbor_column]:
                        visited[neighbor_row, neighbor_column] = True
                        stack.append((neighbor_row, neighbor_column))
    return count


def condition_raster_depressions(
    path: Path,
    *,
    band: int = 1,
    elevation_units: str,
    maximum_fill_depth_m: float,
    cfg: Any,
) -> dict[str, Any]:
    if np is None or rasterio is None:
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL_ENGINE_UNAVAILABLE",
            "rasterio and NumPy are required for bounded depression conditioning.",
        )
    cfg.ensure_dirs()
    source = path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL_NOT_FOUND",
            f"Elevation raster not found: {source}",
        )
    source_sha = _sha256(source)
    if not isinstance(band, int) or isinstance(band, bool) or band < 1:
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL_BAND",
            "Depression conditioning band must be a positive one-based integer.",
        )
    if elevation_units != "meters":
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL_UNITS",
            "Depression conditioning requires an explicit elevation_units value of meters.",
        )
    maximum_fill_depth = _finite_nonnegative(
        maximum_fill_depth_m,
        name="maximum_fill_depth_m",
        status="ERROR_GIS_DEPRESSION_FILL_LIMIT",
    )

    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "raster_depression_fill",
        source_sha,
        str(band),
        elevation_units,
        str(maximum_fill_depth),
        created_at,
    )
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with rasterio.open(source) as dataset:
            if band > dataset.count:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_BAND",
                    f"band must be between 1 and {dataset.count}.",
                )
            if dataset.crs is None or not dataset.crs.is_projected:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_CRS",
                    "Depression conditioning requires a declared projected CRS; geographic angular grids are rejected.",
                )
            try:
                horizontal_unit_name, horizontal_unit_factor = dataset.crs.linear_units_factor
            except Exception as exc:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_CRS",
                    "Projected CRS linear units could not be resolved.",
                ) from exc
            if str(horizontal_unit_name).lower() not in {"metre", "meter", "metres", "meters"} or not math.isclose(
                float(horizontal_unit_factor), 1.0, rel_tol=0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_UNITS",
                    "Depression conditioning requires projected horizontal units with a one-metre conversion factor.",
                )
            transform = dataset.transform
            if not math.isclose(float(transform.b), 0.0, abs_tol=1e-12) or not math.isclose(
                float(transform.d), 0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_TRANSFORM",
                    "Depression conditioning requires an axis-aligned raster transform without rotation or shear.",
                )
            x_resolution = abs(float(transform.a))
            y_resolution = abs(float(transform.e))
            if x_resolution <= 0.0 or y_resolution <= 0.0:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_TRANSFORM",
                    "Depression-conditioning raster resolutions must be positive.",
                )
            total_cells = dataset.width * dataset.height
            if dataset.width < 3 or dataset.height < 3 or total_cells > D8_MAX_CELLS:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_DIMENSIONS",
                    f"Depression-conditioning input must be at least 3x3 and contain at most {D8_MAX_CELLS:,} cells.",
                )

            source_values = dataset.read(band, masked=True)
            source_data = np.asarray(source_values.data, dtype="float64")
            source_invalid = np.ma.getmaskarray(source_values) | ~np.isfinite(source_data)
            source_valid = ~source_invalid
            source_valid_count = int(source_valid.sum())
            source_nodata_count = total_cells - source_valid_count
            if source_valid_count == 0:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_EMPTY",
                    "Depression conditioning requires at least one valid elevation cell.",
                )

            height, width = source_data.shape
            outlet_seed_mask = np.zeros(source_data.shape, dtype=bool)
            for row_value, column_value in np.argwhere(source_valid):
                row = int(row_value)
                column = int(column_value)
                if row in {0, height - 1} or column in {0, width - 1}:
                    outlet_seed_mask[row, column] = True
                    continue
                for row_delta in (-1, 0, 1):
                    for column_delta in (-1, 0, 1):
                        if row_delta == 0 and column_delta == 0:
                            continue
                        if source_invalid[row + row_delta, column + column_delta]:
                            outlet_seed_mask[row, column] = True
                            break
                    if outlet_seed_mask[row, column]:
                        break

            outlet_seed_count = int(outlet_seed_mask.sum())
            if outlet_seed_count == 0:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_OUTLETS",
                    "Depression conditioning requires at least one valid raster-edge or NODATA-adjacent outlet seed.",
                )

            filled_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            fill_depth_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            filled_values[source_valid] = source_data[source_valid]
            fill_depth_values[source_valid] = 0.0
            processed = np.zeros(source_data.shape, dtype=bool)
            queue: list[tuple[float, int, int, int]] = []
            for row_value, column_value in np.argwhere(outlet_seed_mask):
                row = int(row_value)
                column = int(column_value)
                processed[row, column] = True
                flat_index = row * width + column
                heapq.heappush(queue, (float(source_data[row, column]), flat_index, row, column))

            while queue:
                spill_elevation, _, row, column = heapq.heappop(queue)
                for row_delta in (-1, 0, 1):
                    for column_delta in (-1, 0, 1):
                        if row_delta == 0 and column_delta == 0:
                            continue
                        neighbor_row = row + row_delta
                        neighbor_column = column + column_delta
                        if not (0 <= neighbor_row < height and 0 <= neighbor_column < width):
                            continue
                        if not source_valid[neighbor_row, neighbor_column] or processed[neighbor_row, neighbor_column]:
                            continue
                        original_elevation = float(source_data[neighbor_row, neighbor_column])
                        conditioned_elevation = max(original_elevation, spill_elevation)
                        filled_values[neighbor_row, neighbor_column] = conditioned_elevation
                        fill_depth_values[neighbor_row, neighbor_column] = conditioned_elevation - original_elevation
                        processed[neighbor_row, neighbor_column] = True
                        flat_index = neighbor_row * width + neighbor_column
                        heapq.heappush(
                            queue,
                            (conditioned_elevation, flat_index, neighbor_row, neighbor_column),
                        )

            processed_valid_count = int((processed & source_valid).sum())
            if processed_valid_count != source_valid_count:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_GRAPH",
                    "Priority-flood traversal did not classify every valid source cell.",
                )

            valid_fill_depths = fill_depth_values[source_valid]
            observed_maximum_fill_depth = float(valid_fill_depths.max())
            if observed_maximum_fill_depth > maximum_fill_depth + 1e-12:
                raise GISProcessingError(
                    "ERROR_GIS_DEPRESSION_FILL_LIMIT",
                    (
                        f"Required fill depth {observed_maximum_fill_depth} m exceeds "
                        f"maximum_fill_depth_m={maximum_fill_depth}."
                    ),
                )

            raised_mask = source_valid & (fill_depth_values > 0.0)
            raised_cell_count = int(raised_mask.sum())
            unchanged_cell_count = source_valid_count - raised_cell_count
            depression_component_count = _component_count(raised_mask)
            total_fill_depth = float(valid_fill_depths.sum())
            cell_area_square_metres = x_resolution * y_resolution
            fill_volume_cubic_metres = total_fill_depth * cell_area_square_metres
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
                dtype="float64",
                nodata=D8_OUTPUT_NODATA,
                tiled=False,
            )
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target:
                target.write(filled_values, 1)
                target.write(fill_depth_values, 2)
                target.set_band_description(1, "conditioned_elevation_meters")
                target.set_band_description(2, "depression_fill_depth_meters")
                target.update_tags(
                    hydrology_adapter="priority_flood_8_connected_open_boundary_v1",
                    elevation_units=elevation_units,
                    source_sha256=source_sha,
                    boundary_policy="raster_edge_and_nodata_adjacency_are_outlets",
                    flat_policy="preserve_spill_elevation_no_epsilon_gradient",
                )
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL",
            f"Depression-conditioned raster could not be derived: {exc}",
        ) from exc

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
        raise GISProcessingError(
            "ERROR_GIS_DEPRESSION_FILL",
            f"Depression-conditioned raster could not be verified: {exc}",
        ) from exc

    output_sha = _sha256(output_path)
    output_bands = [
        _band_receipt(
            filled_values,
            source_valid,
            band=1,
            name="conditioned_elevation_meters",
        ),
        _band_receipt(
            fill_depth_values,
            source_valid,
            band=2,
            name="depression_fill_depth_meters",
        ),
    ]
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_RASTER_DEPRESSION_FILL_OK",
        "operation": "condition_raster_depressions",
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
            "adapter": "priority_flood_8_connected_open_boundary_v1",
            "band": band,
            "elevation_units": elevation_units,
            "horizontal_units": "meters",
            "horizontal_unit_name": str(horizontal_unit_name),
            "horizontal_unit_factor": float(horizontal_unit_factor),
            "x_resolution": x_resolution,
            "y_resolution": y_resolution,
            "cell_area_square_metres": cell_area_square_metres,
            "maximum_fill_depth_m": maximum_fill_depth,
            "connectivity": 8,
            "boundary_policy": "raster_edge_and_nodata_adjacency_are_outlets",
            "fill_policy": "raise_enclosed_cells_to_lowest_reachable_spill_elevation",
            "flat_policy": "preserve_spill_elevation_no_epsilon_gradient",
            "breach_policy": "NONE",
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
            "bands": output_bands,
        },
        "depression_fill_accounting": {
            "total_cell_count": total_cells,
            "source_valid_cell_count": source_valid_count,
            "source_nodata_cell_count": source_nodata_count,
            "outlet_seed_cell_count": outlet_seed_count,
            "interior_nonseed_cell_count": source_valid_count - outlet_seed_count,
            "processed_valid_cell_count": processed_valid_count,
            "raised_cell_count": raised_cell_count,
            "unchanged_cell_count": unchanged_cell_count,
            "depression_component_count": depression_component_count,
            "maximum_fill_depth_m": observed_maximum_fill_depth,
            "total_fill_depth_m": total_fill_depth,
            "fill_volume_cubic_metres": fill_volume_cubic_metres,
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
            "seed_accounting_closed": outlet_seed_count + (source_valid_count - outlet_seed_count)
            == source_valid_count,
            "processed_accounting_closed": processed_valid_count == source_valid_count,
            "change_accounting_closed": raised_cell_count + unchanged_cell_count == source_valid_count,
            "nodata_preserved": bool(
                np.all(filled_values[source_invalid] == D8_OUTPUT_NODATA)
                and np.all(fill_depth_values[source_invalid] == D8_OUTPUT_NODATA)
            ),
            "outlet_seeds_unchanged": bool(
                np.all(filled_values[outlet_seed_mask] == source_data[outlet_seed_mask])
            ),
            "elevations_never_lowered": bool(np.all(filled_values[source_valid] >= source_data[source_valid])),
            "fill_depth_matches_elevation_delta": bool(
                np.allclose(
                    fill_depth_values[source_valid],
                    filled_values[source_valid] - source_data[source_valid],
                    rtol=0.0,
                    atol=1e-12,
                )
            ),
            "fill_depth_nonnegative": bool(np.all(fill_depth_values[source_valid] >= 0.0)),
            "maximum_fill_depth_within_limit": observed_maximum_fill_depth <= maximum_fill_depth + 1e-12,
            "fill_volume_accounting_closed": math.isclose(
                fill_volume_cubic_metres,
                total_fill_depth * cell_area_square_metres,
                rel_tol=0.0,
                abs_tol=max(1e-9, abs(fill_volume_cubic_metres) * 1e-12),
            ),
            "transactional_output_published": output_path.exists() and not temporary_output_path.exists(),
        },
        "warnings": [],
        "limitations": [
            "Priority-flood conditioning is bounded to one projected-metre elevation band in metres on an axis-aligned grid of at most 1,000,000 cells.",
            "Raster-edge and NODATA-adjacent cells are treated as known outlets and are never raised; unknown terrain beyond those boundaries is not inferred.",
            "The adapter fills enclosed depressions but does not breach barriers or add epsilon gradients, so spill-elevation flats remain for a separate routing policy.",
            "Fill volume uses constant affine pixel area and does not model porosity, infiltration, storage dynamics, runoff, flood, groundwater, or hydraulics.",
        ],
        "falsification_criteria": [
            "reject geographic, non-metre, rotated, sheared, undersized, oversized, empty, or outlet-free source grids",
            "reject invalid bands, vertical units, or non-finite/negative maximum fill depth",
            "reject when any required fill exceeds the explicit maximum_fill_depth_m guard",
            "reject if any source elevation is lowered, any outlet seed changes, or any valid/NODATA cell is lost",
            "reject if source, seed, processed, changed-cell, fill-depth, or fill-volume accounting does not close",
            "reject if CRS, transform, bounds, dimensions, band semantics, hashes, or transactional publication differ",
        ],
    }
    return _write_report(cfg, report)


def derive_raster_d8_flow(
    path: Path,
    *,
    band: int = 1,
    elevation_units: str,
    elevation_tolerance_m: float = 0.0,
    gradient_tie_tolerance: float = 1e-12,
    cfg: Any,
) -> dict[str, Any]:
    _require_rasterio()
    cfg.ensure_dirs()
    source = _resolve_source(path)
    source_sha = _sha256(source)
    if not isinstance(band, int) or isinstance(band, bool) or band < 1:
        raise GISProcessingError("ERROR_GIS_D8_BAND", "D8 elevation band must be a positive one-based integer.")
    if elevation_units != "meters":
        raise GISProcessingError(
            "ERROR_GIS_D8_UNITS",
            "D8 derivation requires an explicit elevation_units value of meters.",
        )
    elevation_tolerance = _finite_nonnegative(
        elevation_tolerance_m,
        name="elevation_tolerance_m",
    )
    tie_tolerance = _finite_nonnegative(
        gradient_tie_tolerance,
        name="gradient_tie_tolerance",
    )

    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "raster_d8_flow",
        source_sha,
        str(band),
        elevation_units,
        str(elevation_tolerance),
        str(tie_tolerance),
        created_at,
    )
    output_path = cfg.export_dir / "gis_reports" / f"{report_id}.tif"
    temporary_output_path = output_path.with_suffix(".tmp.tif")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with rasterio.open(source) as dataset:
            if band > dataset.count:
                raise GISProcessingError("ERROR_GIS_D8_BAND", f"band must be between 1 and {dataset.count}.")
            if dataset.crs is None or not dataset.crs.is_projected:
                raise GISProcessingError(
                    "ERROR_GIS_D8_CRS",
                    "D8 derivation requires a declared projected CRS; geographic angular grids are rejected.",
                )
            try:
                horizontal_unit_name, horizontal_unit_factor = dataset.crs.linear_units_factor
            except Exception as exc:
                raise GISProcessingError(
                    "ERROR_GIS_D8_CRS",
                    "Projected CRS linear units could not be resolved.",
                ) from exc
            if str(horizontal_unit_name).lower() not in {"metre", "meter", "metres", "meters"} or not math.isclose(
                float(horizontal_unit_factor), 1.0, rel_tol=0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_D8_UNITS",
                    "D8 derivation requires projected horizontal units with a one-metre conversion factor.",
                )
            transform = dataset.transform
            if not math.isclose(float(transform.b), 0.0, abs_tol=1e-12) or not math.isclose(
                float(transform.d), 0.0, abs_tol=1e-12
            ):
                raise GISProcessingError(
                    "ERROR_GIS_D8_TRANSFORM",
                    "D8 derivation requires an axis-aligned raster transform without rotation or shear.",
                )
            x_resolution = abs(float(transform.a))
            y_resolution = abs(float(transform.e))
            if x_resolution <= 0.0 or y_resolution <= 0.0:
                raise GISProcessingError("ERROR_GIS_D8_TRANSFORM", "D8 raster resolutions must be positive.")
            total_cells = dataset.width * dataset.height
            if dataset.width < 3 or dataset.height < 3 or total_cells > D8_MAX_CELLS:
                raise GISProcessingError(
                    "ERROR_GIS_D8_DIMENSIONS",
                    f"D8 input must be at least 3x3 and contain at most {D8_MAX_CELLS:,} cells.",
                )

            source_values = dataset.read(band, masked=True)
            source_data = np.asarray(source_values.data, dtype="float64")
            source_invalid = np.ma.getmaskarray(source_values) | ~np.isfinite(source_data)
            source_valid = ~source_invalid
            source_valid_count = int(source_valid.sum())
            source_nodata_count = total_cells - source_valid_count
            if source_valid_count == 0:
                raise GISProcessingError(
                    "ERROR_GIS_D8_EMPTY",
                    "D8 derivation requires at least one valid elevation cell.",
                )

            complete_interior = np.ones((dataset.height - 2, dataset.width - 2), dtype=bool)
            for row_offset in range(3):
                for column_offset in range(3):
                    complete_interior &= source_valid[
                        row_offset : row_offset + dataset.height - 2,
                        column_offset : column_offset + dataset.width - 2,
                    ]
            complete_neighborhood = np.zeros(source_data.shape, dtype=bool)
            complete_neighborhood[1:-1, 1:-1] = complete_interior
            eligible_count = int(complete_neighborhood.sum())
            if eligible_count == 0:
                raise GISProcessingError(
                    "ERROR_GIS_D8_NO_COMPLETE_NEIGHBORHOOD",
                    "D8 derivation requires at least one complete valid 3x3 neighborhood.",
                )

            state_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            direction_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            state_values[source_valid] = D8_STATE_CODEBOOK["BOUNDARY_TERMINAL"]
            direction_values[source_valid] = 0.0
            downstream = np.full(source_data.size, -1, dtype="int64")
            direction_counts = {name: 0 for name in D8_DIRECTION_CODEBOOK}
            tie_candidate_direction_count = 0
            directions = _direction_rows(transform)

            for row, column in np.argwhere(complete_neighborhood):
                center = float(source_data[row, column])
                candidates: list[tuple[str, int, int, int, float]] = []
                equal_neighbor_present = False
                for name, row_delta, column_delta, code, distance in directions:
                    neighbor_row = int(row + row_delta)
                    neighbor_column = int(column + column_delta)
                    drop = center - float(source_data[neighbor_row, neighbor_column])
                    if abs(drop) <= elevation_tolerance:
                        equal_neighbor_present = True
                    if drop > elevation_tolerance:
                        candidates.append(
                            (name, neighbor_row, neighbor_column, code, drop / distance)
                        )

                if not candidates:
                    state_values[row, column] = D8_STATE_CODEBOOK[
                        "FLAT_UNRESOLVED" if equal_neighbor_present else "SINK_UNRESOLVED"
                    ]
                    continue

                maximum_gradient = max(candidate[4] for candidate in candidates)
                top_candidates = [
                    candidate
                    for candidate in candidates
                    if abs(candidate[4] - maximum_gradient) <= tie_tolerance
                ]
                direction_values[row, column] = float(sum(candidate[3] for candidate in top_candidates))
                if len(top_candidates) > 1:
                    state_values[row, column] = D8_STATE_CODEBOOK["TIE_UNRESOLVED"]
                    tie_candidate_direction_count += len(top_candidates)
                    continue

                name, neighbor_row, neighbor_column, _, _ = top_candidates[0]
                state_values[row, column] = D8_STATE_CODEBOOK["FLOW_UNIQUE"]
                direction_counts[name] += 1
                downstream[int(row) * dataset.width + int(column)] = (
                    neighbor_row * dataset.width + neighbor_column
                )

            accumulation_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            accumulation_values[source_valid] = 1.0
            valid_flat_indices = np.flatnonzero(source_valid.ravel())
            descending_order = valid_flat_indices[
                np.argsort(-source_data.ravel()[valid_flat_indices], kind="stable")
            ]
            strict_descent_edges = True
            accumulation_flat = accumulation_values.ravel()
            source_flat = source_data.ravel()
            for source_index in descending_order:
                target_index = int(downstream[source_index])
                if target_index < 0:
                    continue
                if not source_flat[source_index] > source_flat[target_index] + elevation_tolerance:
                    strict_descent_edges = False
                    break
                accumulation_flat[target_index] += accumulation_flat[source_index]
            if not strict_descent_edges:
                raise GISProcessingError(
                    "ERROR_GIS_D8_GRAPH",
                    "D8 graph contains an edge that does not satisfy strict descent.",
                )

            cell_area_square_metres = x_resolution * y_resolution
            contributing_area_values = np.full(source_data.shape, D8_OUTPUT_NODATA, dtype="float64")
            contributing_area_values[source_valid] = (
                accumulation_values[source_valid] * cell_area_square_metres
            )
            state_counts = {
                name: int((state_values == code).sum())
                for name, code in D8_STATE_CODEBOOK.items()
            }
            flow_edge_count = int((downstream >= 0).sum())
            terminal_mask = source_valid & (
                state_values != D8_STATE_CODEBOOK["FLOW_UNIQUE"]
            )
            terminal_cell_count = int(terminal_mask.sum())
            terminal_accumulation_total = float(accumulation_values[terminal_mask].sum())
            terminal_contributing_area_total = float(
                contributing_area_values[terminal_mask].sum()
            )
            maximum_accumulation = float(accumulation_values[source_valid].max())
            nonzero_direction_count = int((direction_values[source_valid] > 0.0).sum())
            zero_direction_count = source_valid_count - nonzero_direction_count

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
                count=4,
                dtype="float64",
                nodata=D8_OUTPUT_NODATA,
                tiled=False,
            )
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            with rasterio.open(temporary_output_path, "w", **profile) as target:
                target.write(direction_values, 1)
                target.write(accumulation_values, 2)
                target.write(contributing_area_values, 3)
                target.write(state_values, 4)
                target.set_band_description(1, "d8_candidate_bitmask_esri")
                target.set_band_description(2, "flow_accumulation_cells_including_self")
                target.set_band_description(3, "contributing_area_square_metres")
                target.set_band_description(4, "flow_state_code")
                target.update_tags(
                    hydrology_adapter="d8_steepest_descent_complete_neighborhood_v1",
                    elevation_units=elevation_units,
                    source_sha256=source_sha,
                    direction_convention="esri_powers_of_two_clockwise_from_east",
                    boundary_policy="terminal_no_external_elevation_assumption",
                )
        temporary_output_path.replace(output_path)
    except GISProcessingError:
        temporary_output_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary_output_path.unlink(missing_ok=True)
        raise GISProcessingError("ERROR_GIS_D8", f"D8 flow raster could not be derived: {exc}") from exc

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
        raise GISProcessingError("ERROR_GIS_D8", f"Derived D8 raster could not be verified: {exc}") from exc

    output_sha = _sha256(output_path)
    output_bands = [
        _band_receipt(
            direction_values,
            source_valid,
            band=1,
            name="d8_candidate_bitmask_esri",
        ),
        _band_receipt(
            accumulation_values,
            source_valid,
            band=2,
            name="flow_accumulation_cells_including_self",
        ),
        _band_receipt(
            contributing_area_values,
            source_valid,
            band=3,
            name="contributing_area_square_metres",
        ),
        _band_receipt(
            state_values,
            source_valid,
            band=4,
            name="flow_state_code",
        ),
    ]
    terminal_expected_area = source_valid_count * cell_area_square_metres
    terminal_states = (
        state_counts["BOUNDARY_TERMINAL"]
        + state_counts["SINK_UNRESOLVED"]
        + state_counts["FLAT_UNRESOLVED"]
        + state_counts["TIE_UNRESOLVED"]
    )
    warnings = []
    if state_counts["SINK_UNRESOLVED"]:
        warnings.append("GIS_D8_SINKS_UNRESOLVED")
    if state_counts["FLAT_UNRESOLVED"]:
        warnings.append("GIS_D8_FLATS_UNRESOLVED")
    if state_counts["TIE_UNRESOLVED"]:
        warnings.append("GIS_D8_TIES_UNRESOLVED")
    status = "GIS_RASTER_D8_WARN" if warnings else "GIS_RASTER_D8_OK"
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": status,
        "operation": "derive_raster_d8_flow",
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
            "adapter": "d8_steepest_descent_complete_neighborhood_v1",
            "band": band,
            "elevation_units": elevation_units,
            "horizontal_units": "meters",
            "horizontal_unit_name": str(horizontal_unit_name),
            "horizontal_unit_factor": float(horizontal_unit_factor),
            "x_resolution": x_resolution,
            "y_resolution": y_resolution,
            "cell_area_square_metres": cell_area_square_metres,
            "elevation_tolerance_m": elevation_tolerance,
            "gradient_tie_tolerance": tie_tolerance,
            "direction_convention": "esri_powers_of_two_clockwise_from_east",
            "neighborhood_policy": "complete_3x3",
            "boundary_policy": "terminal_no_external_elevation_assumption",
            "sink_policy": "UNRESOLVED_NO_FILL",
            "flat_policy": "UNRESOLVED_NO_ROUTING",
            "tie_policy": "PRESERVE_CANDIDATE_BITMASK_NO_ROUTING",
            "accumulation_policy": "upstream_cell_count_including_self",
        },
        "direction_codebook": D8_DIRECTION_CODEBOOK,
        "state_codebook": D8_STATE_CODEBOOK,
        "direction_counts": direction_counts,
        "state_counts": state_counts,
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
            "bands": output_bands,
        },
        "flow_accounting": {
            "total_cell_count": total_cells,
            "source_valid_cell_count": source_valid_count,
            "source_nodata_cell_count": source_nodata_count,
            "complete_neighborhood_cell_count": eligible_count,
            "boundary_terminal_cell_count": state_counts["BOUNDARY_TERMINAL"],
            "unique_flow_cell_count": state_counts["FLOW_UNIQUE"],
            "sink_unresolved_cell_count": state_counts["SINK_UNRESOLVED"],
            "flat_unresolved_cell_count": state_counts["FLAT_UNRESOLVED"],
            "tie_unresolved_cell_count": state_counts["TIE_UNRESOLVED"],
            "tie_candidate_direction_count": tie_candidate_direction_count,
            "flow_edge_count": flow_edge_count,
            "terminal_cell_count": terminal_cell_count,
            "nonzero_direction_cell_count": nonzero_direction_count,
            "zero_direction_cell_count": zero_direction_count,
            "terminal_accumulation_cell_count_total": terminal_accumulation_total,
            "terminal_contributing_area_square_metres_total": terminal_contributing_area_total,
            "maximum_accumulation_cell_count": maximum_accumulation,
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
            "output_band_count_exact": output_band_count == 4,
            "source_cell_accounting_closed": source_valid_count + source_nodata_count == total_cells,
            "state_accounting_closed": sum(state_counts.values()) == source_valid_count,
            "flow_edge_accounting_closed": flow_edge_count == state_counts["FLOW_UNIQUE"],
            "terminal_state_accounting_closed": terminal_states == terminal_cell_count,
            "direction_state_accounting_closed": nonzero_direction_count
            == state_counts["FLOW_UNIQUE"] + state_counts["TIE_UNRESOLVED"],
            "terminal_contribution_accounting_closed": math.isclose(
                terminal_accumulation_total,
                float(source_valid_count),
                rel_tol=0.0,
                abs_tol=1e-9,
            ),
            "terminal_area_accounting_closed": math.isclose(
                terminal_contributing_area_total,
                terminal_expected_area,
                rel_tol=0.0,
                abs_tol=max(1e-9, terminal_expected_area * 1e-12),
            ),
            "strict_descent_edges": strict_descent_edges,
            "accumulation_range_bounded": 1.0 <= output_bands[1]["minimum"]
            <= output_bands[1]["maximum"] <= float(source_valid_count),
            "contributing_area_matches_cell_area": math.isclose(
                output_bands[2]["maximum"],
                output_bands[1]["maximum"] * cell_area_square_metres,
                rel_tol=0.0,
                abs_tol=max(1e-9, output_bands[2]["maximum"] * 1e-12),
            ),
            "transactional_output_published": output_path.exists() and not temporary_output_path.exists(),
        },
        "warnings": warnings,
        "limitations": [
            "D8 routing is bounded to one projected-metre elevation band in metres on an axis-aligned grid of at most 1,000,000 cells.",
            "Only cells with a complete valid 3x3 neighborhood are routed; perimeter and NODATA-adjacent cells are terminal without assuming unknown external elevations.",
            "The adapter does not fill depressions, breach barriers, resolve flats, or select among tied steepest candidates; those cells remain explicit terminal residue.",
            "Accumulation counts upstream cells including self and contributing area assumes every valid grid cell has the declared constant affine cell area.",
            "D8 is a single-flow-direction approximation and is not a watershed, stream, runoff, flood, groundwater, or hydraulic model.",
        ],
        "falsification_criteria": [
            "reject geographic, non-metre, rotated, sheared, undersized, oversized, empty, or all-boundary source grids",
            "reject invalid bands, vertical units, or non-finite/negative tolerances",
            "reject if tied top candidates are collapsed to one direction or unresolved flats/sinks receive synthetic routing",
            "reject if state, direction, edge, terminal accumulation, or contributing-area arithmetic does not close",
            "reject if any routed edge does not descend beyond the declared elevation tolerance",
            "reject if CRS, transform, bounds, dimensions, band semantics, hashes, or transactional publication differ",
        ],
    }
    return _write_report(cfg, report)
