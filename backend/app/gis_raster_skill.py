"""Permissioned GeoTIFF tasks using the shared raster, terrain and hydrology engines."""

from __future__ import annotations

import hashlib
import math
import tempfile
from dataclasses import replace
from functools import wraps
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.windows import Window

from .config import Settings
from .gis_hydrology import condition_raster_depressions, derive_raster_d8_flow
from .gis_processing import GISProcessingError
from .gis_raster import (
    clip_raster_bbox,
    derive_raster_hillshade,
    derive_raster_terrain,
    inspect_raster,
    resample_raster,
    sample_raster,
)
from .gis_skill import GIS_EXECUTION_POLICY, _preflight as vector_preflight
from .gis_vector_raster import analyze_vector_raster_intersection, compute_zonal_statistics
from .local_inputs import snapshot_local_file
from .spatial_reports import numeric_report_view, record_spatial_artifacts

OPERATIONS = {
    "inspect": "Inspect raster bands and NODATA",
    "sample": "Sample coordinates in the raster CRS",
    "clip": "Clip raster to a coordinate extent",
    "resample": "Resample to explicit grid dimensions",
    "terrain": "Derive slope and aspect",
    "hillshade": "Create terrain hillshade",
    "condition_depressions": "Condition depressions with a fill-depth limit",
    "d8_flow": "Calculate D8 flow and unresolved candidates",
    "vector_intersection": "Intersect vector zones with raster cells",
    "zonal_statistics": "Summarize raster values within vector zones",
}
ELEVATION_OPERATIONS = ["terrain", "hillshade", "condition_depressions", "d8_flow"]
VECTOR_OPERATIONS = ["vector_intersection", "zonal_statistics"]
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_PAM_ENABLED": False,
    "GDAL_GEOREF_SOURCES": "INTERNAL",
    "GDAL_VRT_ENABLE_PYTHON": "NO",
    "GDAL_NUM_THREADS": "1",
    "GDAL_CACHEMAX": 32 * 1024 * 1024,
    "PROJ_NETWORK": "OFF",
}
RASTER_EXECUTION_POLICY = {
    "source_policy": "directory_grant_and_frozen_bytes_v1",
    "driver": "GTiff",
    "external_resources": "private_snapshot_internal_georeferencing_no_sidecars_v1",
    "multi_grid_policy": "reject_subdatasets_select_one_self_contained_grid_v1",
    "decoded_size_policy": "header_limits_before_reads_finite_valid_pixels_v1",
    "coordinate_policy": "explicit_matching_crs_and_pixel_grid_bounds_v1",
    "nodata_policy": "masked_band_states_and_nonfinite_metadata_view_v1",
    "numeric_result_policy": "fail_closed_on_overflow_and_nonfinite_calculation_v1",
    "report_policy": "private_invocation_directory_hash_verified_v1",
    "max_source_bytes": 25 * 1024 * 1024,
    "max_vector_source_bytes": 5 * 1024 * 1024,
    "vector_input_policy": GIS_EXECUTION_POLICY,
    "max_raster_cells": 250_000,
    "max_raster_values": 1_000_000,
    "max_raster_bands": 8,
    "max_decoded_bytes": 32 * 1024 * 1024,
    "max_vector_work": 1_000_000,
    "max_report_bytes": 2 * 1024 * 1024,
    "max_output_bytes": 64 * 1024 * 1024,
    "gdal_environment": GDAL_ENV,
    "operations": list(OPERATIONS),
}


def _property(kind: str, title: str, operations: list[str], **constraints: Any) -> dict[str, Any]:
    return {"type": kind, "title": title, "x-operations": operations, **constraints}


PROPERTIES = {
    "operation": {
        "type": "string",
        "title": "Raster operation",
        "enum": list(OPERATIONS),
        "x-enum-labels": OPERATIONS,
        "default": "inspect",
    },
    "path": {"type": "string", "title": "Input GeoTIFF path", "minLength": 1, "maxLength": 4096, "pattern": r"\S"},
    "points": _property(
        "array",
        "Sample points",
        ["sample"],
        minItems=1,
        maxItems=1000,
        items={"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
        **{"x-input": "coordinate-pairs"},
    ),
    "coordinate_crs": _property(
        "string", "Coordinate CRS (for example EPSG:32620)", ["sample", "clip"], pattern=r"^EPSG:[0-9]{3,6}$"
    ),
    **{
        name: _property("number", label, ["clip"])
        for name, label in [
            ("min_x", "Minimum X"),
            ("min_y", "Minimum Y"),
            ("max_x", "Maximum X"),
            ("max_y", "Maximum Y"),
        ]
    },
    "width": _property("integer", "Output width (cells)", ["resample"], minimum=1, maximum=10000),
    "height": _property("integer", "Output height (cells)", ["resample"], minimum=1, maximum=10000),
    "method": _property("string", "Interpolation", ["resample"], enum=["nearest", "bilinear"], default="nearest"),
    "band": _property(
        "integer", "Raster band (one-based)", ELEVATION_OPERATIONS + VECTOR_OPERATIONS, minimum=1, maximum=8, default=1
    ),
    "elevation_units": _property(
        "string", "Elevation units (operator declaration)", ELEVATION_OPERATIONS, enum=["meters"], default="meters"
    ),
    "maximum_fill_depth_m": _property(
        "number", "Maximum permitted fill depth (m)", ["condition_depressions"], minimum=0
    ),
    "azimuth_degrees": _property(
        "number",
        "Light azimuth (degrees clockwise from north)",
        ["hillshade"],
        minimum=0,
        exclusiveMaximum=360,
        default=315,
    ),
    "altitude_degrees": _property(
        "number", "Light altitude (degrees above horizon)", ["hillshade"], exclusiveMinimum=0, maximum=90, default=45
    ),
    "elevation_tolerance_m": _property("number", "Elevation tolerance (m)", ["d8_flow"], minimum=0, default=0),
    "gradient_tie_tolerance": _property("number", "Gradient tie tolerance", ["d8_flow"], minimum=0, default=1e-12),
    "vector_path": _property(
        "string", "Vector zones GeoJSON path", VECTOR_OPERATIONS, minLength=1, maxLength=4096, pattern=r"\S"
    ),
    "inclusion_rule": _property(
        "string",
        "Cell inclusion rule",
        VECTOR_OPERATIONS,
        enum=["cell_center", "fractional_coverage"],
        default="cell_center",
    ),
    "expected_sha256": {"type": "string", "title": "Expected raster SHA-256 (optional)", "pattern": "^[0-9a-f]{64}$"},
    "expected_vector_sha256": _property(
        "string", "Expected zones SHA-256 (optional)", VECTOR_OPERATIONS, pattern="^[0-9a-f]{64}$"
    ),
}
REQUIRED_BY_OPERATION = {
    "sample": ["points", "coordinate_crs"],
    "clip": ["min_x", "min_y", "max_x", "max_y", "coordinate_crs"],
    "resample": ["width", "height"],
    **{operation: ["elevation_units"] for operation in ELEVATION_OPERATIONS},
    "condition_depressions": ["elevation_units", "maximum_fill_depth_m"],
    **{operation: ["vector_path"] for operation in VECTOR_OPERATIONS},
}
RASTER_ARGUMENTS_SCHEMA = {
    "type": "object",
    "title": "Analyze raster and hydrology data",
    "properties": PROPERTIES,
    "required": ["operation", "path"],
    "additionalProperties": False,
    "allOf": [
        *[
            {"if": {"properties": {"operation": {"const": operation}}}, "then": {"required": required}}
            for operation, required in REQUIRED_BY_OPERATION.items()
        ],
        *[
            {"if": {"properties": {"operation": {"enum": prop["x-operations"]}}}, "else": {"not": {"required": [name]}}}
            for name, prop in PROPERTIES.items()
            if "x-operations" in prop
        ],
    ],
}


def _fail(code: str, detail: str) -> GISProcessingError:
    return GISProcessingError(f"ERROR_GIS_RASTER_{code}", detail)


def _raster_preflight(path: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    with path.open("rb") as header:
        signature = header.read(4)
    if signature not in {b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"}:
        raise _fail(
            "DRIVER_DENIED",
            "Choose a self-contained TIFF/GeoTIFF; virtual datasets and external-resource formats are not accepted.",
        )
    with rasterio.open(path, driver="GTiff", sharing=False) as dataset:
        if dataset.subdatasets:
            raise _fail(
                "MULTIGRID_UNSUPPORTED",
                "This TIFF contains multiple raster grids. Extract the intended grid into a self-contained GeoTIFF so other grids are not silently omitted.",
            )
        cells, bands = dataset.width * dataset.height, dataset.count
        dtypes = [np.dtype(dtype) for dtype in dataset.dtypes]
        decoded = cells * sum(dtype.itemsize for dtype in dtypes)
        if (
            not cells
            or not 1 <= bands <= RASTER_EXECUTION_POLICY["max_raster_bands"]
            or cells > RASTER_EXECUTION_POLICY["max_raster_cells"]
            or cells * bands > RASTER_EXECUTION_POLICY["max_raster_values"]
            or decoded > RASTER_EXECUTION_POLICY["max_decoded_bytes"]
        ):
            raise _fail(
                "SIZE_LIMIT",
                "Decoded raster dimensions, bands, or memory exceed the declared budget; clip or split the source explicitly.",
            )
        if any(dtype.kind not in "iuf" or dtype.itemsize > 8 for dtype in dtypes):
            raise _fail("DTYPE_UNSUPPORTED", "Raster tasks require real integer/float bands of at most 64 bits.")
        if not all(math.isfinite(value) for value in (*dataset.transform, *dataset.bounds)):
            raise _fail("TRANSFORM_INVALID", "Raster coordinates and transform must be finite.")
        band = arguments.get("band", 1)
        if band > bands:
            raise _fail("BAND_INVALID", "The selected band does not exist in this raster.")
        operation = arguments["operation"]
        if operation in {"sample", "clip"}:
            if dataset.crs is None or dataset.crs != CRS.from_user_input(arguments["coordinate_crs"]):
                raise _fail(
                    "COORDINATE_CRS",
                    "Declare the CRS of the supplied coordinates; it must match the raster. Transform coordinates explicitly first.",
                )
        if operation in ELEVATION_OPERATIONS:
            if dataset.scales[band - 1] != 1 or dataset.offsets[band - 1] != 0:
                raise _fail(
                    "SCALED_ELEVATION",
                    "Convert scaled/offset elevation samples into explicit metre values before derivation.",
                )
            unit = (dataset.units[band - 1] or "").lower()
            if unit and unit not in {"m", "meter", "meters", "metre", "metres"}:
                raise _fail("ELEVATION_UNIT_CONFLICT", "The band unit contradicts the requested metre declaration.")
        if operation == "resample" and (
            arguments["width"] * arguments["height"] > RASTER_EXECUTION_POLICY["max_raster_cells"]
            or arguments["width"] * arguments["height"] * bands > RASTER_EXECUTION_POLICY["max_raster_values"]
        ):
            raise _fail(
                "OUTPUT_SIZE_LIMIT", "Requested output dimensions exceed the total-value budget after band accounting."
            )
        counts = []
        for index in range(1, bands + 1):
            valid = nodata = zeros = 0
            for row in range(0, dataset.height, 128):
                values = dataset.read(
                    index, window=Window(0, row, dataset.width, min(128, dataset.height - row)), masked=True
                )
                compressed = values.compressed()
                if not bool(np.isfinite(compressed).all()):
                    raise _fail(
                        "NONFINITE_DATA", "A valid raster cell contains NaN or infinity without a matching NODATA mask."
                    )
                valid += int(values.count())
                nodata += int(values.size - values.count())
                zeros += int((compressed == 0).sum())
            counts.append(
                {"band": index, "valid_cell_count": valid, "nodata_cell_count": nodata, "zero_cell_count": zeros}
            )
        return {
            "width": dataset.width,
            "height": dataset.height,
            "band_count": bands,
            "crs": dataset.crs.to_string() if dataset.crs else None,
            "transform": list(dataset.transform)[:6],
            "bounds": list(dataset.bounds),
            "dtypes": list(dataset.dtypes),
            "nodata_values": list(dataset.nodatavals),
            "scales": list(dataset.scales),
            "offsets": list(dataset.offsets),
            "bands": counts,
            "decoded_bytes": decoded,
        }


def _guard_numeric_result(handler):
    @wraps(handler)
    def guarded(arguments, cfg):
        try:
            return handler(arguments, cfg)
        except (OverflowError, FloatingPointError) as exc:
            raise GISProcessingError(
                "ERROR_GIS_NONFINITE_RESULT",
                "Spatial arithmetic exceeded the supported numeric range. Scale the input values explicitly or split the workload; no valid calculation is claimed.",
            ) from exc

    return guarded


@_guard_numeric_result
def analyze_raster_data(arguments: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    source, manifest, artifact = snapshot_local_file(
        arguments["path"],
        arguments.get("expected_sha256"),
        cfg,
        extensions={".tif", ".tiff"},
        max_bytes=RASTER_EXECUTION_POLICY["max_source_bytes"],
        namespace="GIS_RASTER",
        producer="gis_raster_skill",
        error_type=GISProcessingError,
    )
    sources = [(source, manifest, artifact)]
    operation = arguments["operation"]
    with rasterio.Env(**GDAL_ENV):
        manifest["raster"] = _raster_preflight(source, arguments)
        if operation in VECTOR_OPERATIONS:
            vector, vector_manifest, vector_artifact = snapshot_local_file(
                arguments["vector_path"],
                arguments.get("expected_vector_sha256"),
                cfg,
                extensions={".geojson", ".json"},
                max_bytes=RASTER_EXECUTION_POLICY["max_vector_source_bytes"],
                namespace="GIS",
                producer="gis_raster_skill",
                error_type=GISProcessingError,
            )
            vector_manifest["preflight"] = vector_preflight(vector, require_ids=False)
            work = (
                manifest["raster"]["width"]
                * manifest["raster"]["height"]
                * (
                    sum(vector_manifest["preflight"]["coordinate_counts"])
                    if arguments.get("inclusion_rule") == "fractional_coverage"
                    else vector_manifest["preflight"]["feature_count"]
                )
            )
            if work > RASTER_EXECUTION_POLICY["max_vector_work"]:
                raise _fail(
                    "VECTOR_WORK_LIMIT",
                    "Clip these layers before zonal analysis; candidate cell/geometry work exceeds the declared budget.",
                )
            sources.append((vector, vector_manifest, vector_artifact))
        run_dir = Path(tempfile.mkdtemp(prefix="gis-raster-run-", dir=cfg.export_dir))
        run_cfg = replace(cfg, export_dir=run_dir)
        elevation = {"band": arguments.get("band", 1), "elevation_units": arguments.get("elevation_units")}
        if operation == "inspect":
            report = inspect_raster(source, cfg=run_cfg)
        elif operation == "sample":
            report = sample_raster(source, [tuple(point) for point in arguments["points"]], cfg=run_cfg)
        elif operation == "clip":
            report = clip_raster_bbox(
                source, tuple(arguments[key] for key in ("min_x", "min_y", "max_x", "max_y")), cfg=run_cfg
            )
        elif operation == "resample":
            report = resample_raster(
                source,
                width=arguments["width"],
                height=arguments["height"],
                method=arguments.get("method", "nearest"),
                cfg=run_cfg,
            )
        elif operation == "terrain":
            report = derive_raster_terrain(source, **elevation, cfg=run_cfg)
        elif operation == "hillshade":
            report = derive_raster_hillshade(
                source,
                **elevation,
                azimuth_degrees=arguments.get("azimuth_degrees", 315),
                altitude_degrees=arguments.get("altitude_degrees", 45),
                cfg=run_cfg,
            )
        elif operation == "condition_depressions":
            report = condition_raster_depressions(
                source, **elevation, maximum_fill_depth_m=arguments["maximum_fill_depth_m"], cfg=run_cfg
            )
        elif operation == "d8_flow":
            report = derive_raster_d8_flow(
                source,
                **elevation,
                elevation_tolerance_m=arguments.get("elevation_tolerance_m", 0),
                gradient_tie_tolerance=arguments.get("gradient_tie_tolerance", 1e-12),
                cfg=run_cfg,
            )
        else:
            handler = (
                compute_zonal_statistics if operation == "zonal_statistics" else analyze_vector_raster_intersection
            )
            report = handler(
                sources[1][0],
                source,
                band=arguments.get("band", 1),
                inclusion_rule=arguments.get("inclusion_rule", "cell_center"),
                cfg=run_cfg,
            )
        output_grid = None
        if report.get("output"):
            output = Path(report["output"]["path"])
            if (
                output.is_symlink()
                or not output.resolve().is_relative_to(run_dir.resolve())
                or output.stat().st_size > RASTER_EXECUTION_POLICY["max_output_bytes"]
            ):
                raise _fail("OUTPUT_SIZE_LIMIT", "Generated output exceeds its declared boundary or byte budget.")
            with rasterio.open(output, driver="GTiff", sharing=False) as dataset:
                output_grid = {
                    "width": dataset.width,
                    "height": dataset.height,
                    "band_count": dataset.count,
                    "crs": dataset.crs.to_string() if dataset.crs else None,
                    "descriptions": list(dataset.descriptions),
                    "nodata_values": list(dataset.nodatavals),
                }
    report, _ = numeric_report_view(report)
    for frozen, source_manifest, _ in sources:
        if hashlib.sha256(frozen.read_bytes()).hexdigest() != source_manifest["sha256"]:
            raise _fail("SOURCE_CHANGED", "Frozen raster or vector input changed during execution.")
    reported_hashes = (
        [report[key]["sha256"] for key in ("raster_source", "vector_source")]
        if operation in VECTOR_OPERATIONS
        else [report["source"]["sha256"]]
    )
    if sorted(reported_hashes) != sorted(item[1]["sha256"] for item in sources):
        raise _fail("SOURCE_HASH_MISMATCH", "Engine sources do not match the frozen manifestations.")
    if any(value is False for value in report.get("invariants", {}).values()):
        raise _fail(
            "INVARIANT_FAILED", "Raster accounting or output invariants failed; review the retained engine reports."
        )
    intermediates = []
    if report.get("terrain_lineage"):
        lineage = report["terrain_lineage"]
        intermediates = [
            ("terrain_output", {"path": lineage["output_path"], "sha256": lineage["output_sha256"]}),
            ("terrain_report", {"path": lineage["report_path"], "sha256": lineage["report_sha256"]}),
        ]
    downloads = []
    artifact_ids = record_spatial_artifacts(
        report,
        cfg=cfg,
        run_dir=run_dir,
        parent_ids=[item[2].artifact_id for item in sources],
        producer="gis_raster_skill",
        max_report_bytes=RASTER_EXECUTION_POLICY["max_report_bytes"],
        max_output_bytes=RASTER_EXECUTION_POLICY["max_output_bytes"],
        intermediates=intermediates,
        downloads=downloads,
    )
    warnings = list(report.get("warnings", []))
    if any(item.get("state") in {"NODATA", "PARTIAL_NODATA", "OUTSIDE"} for item in report.get("samples", [])):
        warnings.append("GIS_RASTER_SAMPLE_NONVALUE_STATES")
    result, special = numeric_report_view(
        {
            "kind": "GIS_RASTER_ANALYSIS",
            "operation": operation,
            "operation_label": OPERATIONS[operation],
            "report": report,
            "source_manifests": [item[1] for item in sources],
            "output_grid": output_grid,
            "source_artifact_ids": [item[2].artifact_id for item in sources],
            "artifact_ids": artifact_ids,
            "files": downloads,
            "trust": "UNTRUSTED_CONTENT",
            "identity_state": "CANDIDATE_NOT_IDENTITY",
            "epistemic_classification": "COMPUTED",
            "limitations": [
                *report.get("limitations", []),
                "Input elevation units are an explicit operator declaration, checked against any band unit metadata.",
                "Raw report bytes and the JSON-safe inspection view retain separate representations; non-finite metadata is identified by JSON pointer.",
                "These local raster/terrain/hydrology calculations do not establish canonical identity, hydraulic forecasts, or independent-engine certification.",
            ],
        }
    )
    if special:
        warnings.append("GIS_RASTER_SPECIAL_NUMERIC_METADATA")
    result.update(
        status="GIS_SKILL_REVIEW_REQUIRED" if warnings else "GIS_SKILL_OK",
        warnings=list(dict.fromkeys(warnings)),
        numeric_metadata_states=special,
    )
    return result
