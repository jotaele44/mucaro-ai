from __future__ import annotations

import hashlib
import json
import warnings as python_warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer, network, proj_version_str
from pyproj import __version__ as pyproj_version
from pyproj.exceptions import ProjError
from pyproj.transformer import TransformerGroup

from .config import Settings, settings
from .gis_processing import (
    GIS_REPORT_SCHEMA_VERSION,
    GISProcessingError,
    _coordinate_dimension_summary,
    _feature_id,
    _load_geojson,
    _write_geojson_output,
    _write_report,
)
from .utils import sha256_file, stable_id, utc_now

CRS_ADAPTER = "pyproj_transformer_group_always_xy_v1"
CRS_GOVERNED_GRID_ADAPTER = "pyproj_governed_local_grid_always_xy_v1"
CRS_NETWORK_POLICY = "offline_only"
GOVERNED_GRID_MANIFEST_SCHEMA_VERSION = "mucaro_gis_grid_manifest_v1"
GOVERNED_GRID_MANIFEST_SHA256 = "3d49d0bb364be9f41879ef3e74aa6766131ccb13ea6aef8bc11631f35d0fa1bc"
GOVERNED_GRID_ROOT = Path(__file__).resolve().parent / "gis_grids"
GOVERNED_GRID_ID = "us_noaa_pvhpgn"
GOVERNED_GRID_FILENAME = "us_noaa_pvhpgn.tif"
GOVERNED_GRID_BYTES = 3644
GOVERNED_GRID_SHA256 = "a6db010f956526a011ba719c45fe32e5aff987eeb1cb03f2b8338208a77c619d"
GOVERNED_GRID_SOURCE_CRS = "EPSG:4326"
GOVERNED_GRID_TARGET_CRS = "EPSG:32161"
GOVERNED_GRID_ACCURACY_METRES = 2.0
GOVERNED_GRID_OPERATION_NAME = (
    "Inverse of NAD83 to WGS 84 (35) + SPCS83 Puerto Rico & Virgin Islands zone (meter)"
)
GOVERNED_GRID_PIPELINE_TEMPLATE = (
    "proj=pipeline "
    "step proj=unitconvert xy_in=deg xy_out=rad "
    "step inv proj=hgridshift grids={grid} "
    "step proj=lcc lat_0=17.8333333333333 lon_0=-66.4333333333333 "
    "lat_1=18.4333333333333 lat_2=18.0333333333333 "
    "x_0=200000 y_0=200000 ellps=GRS80"
)


def _governed_grid_error(status: str, detail: str) -> GISProcessingError:
    return GISProcessingError(status, detail)


def _load_governed_grid_transform(
    source_obj: CRS,
    target_obj: CRS,
    *,
    grid_root: Path | None = None,
) -> dict[str, Any] | None:
    root = (grid_root or GOVERNED_GRID_ROOT).expanduser().resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_MISSING",
            "The governed local datum-grid manifest is absent or is not a regular file.",
        )
    manifest_sha256 = sha256_file(manifest_path)
    if manifest_sha256 != GOVERNED_GRID_MANIFEST_SHA256:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_HASH_MISMATCH",
            "The governed local datum-grid manifest does not match the source-bound SHA-256.",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_INVALID",
            "The governed local datum-grid manifest is not valid UTF-8 JSON.",
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != GOVERNED_GRID_MANIFEST_SCHEMA_VERSION
        or manifest.get("network_policy") != CRS_NETWORK_POLICY
        or not isinstance(manifest.get("grids"), list)
    ):
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_INVALID",
            "The governed local datum-grid manifest schema or network policy is invalid.",
        )

    matching_rows: list[dict[str, Any]] = []
    for row in manifest["grids"]:
        if not isinstance(row, dict) or not isinstance(row.get("allowed_operation"), dict):
            continue
        operation = row["allowed_operation"]
        try:
            operation_source = CRS.from_user_input(operation.get("source_crs")).to_epsg()
            operation_target = CRS.from_user_input(operation.get("target_crs")).to_epsg()
        except (ProjError, TypeError, ValueError):
            raise _governed_grid_error(
                "ERROR_GIS_CRS_GRID_OPERATION_MISMATCH",
                "The governed local datum-grid operation contains an invalid CRS.",
            )
        if operation_source == source_obj.to_epsg() and operation_target == target_obj.to_epsg():
            matching_rows.append(row)
    if not matching_rows:
        return None
    if len(matching_rows) != 1:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_INVALID",
            "The governed local datum-grid manifest must contain exactly one operation for the requested CRS pair.",
        )

    grid = matching_rows[0]
    allowed_operation = grid["allowed_operation"]
    expected_fields = {
        "source_crs": "EPSG:4269",
        "target_crs": "EPSG:4152",
    }
    if (
        any(grid.get(key) != value for key, value in expected_fields.items())
        or not isinstance(grid.get("grid_id"), str)
        or not isinstance(grid.get("filename"), str)
        or not isinstance(grid.get("bytes"), int)
        or not isinstance(grid.get("sha256"), str)
    ):
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MANIFEST_INVALID",
            "The governed local datum-grid manifest does not match its source-bound grid policy.",
        )
    if (
        not isinstance(allowed_operation.get("source_crs"), str)
        or not isinstance(allowed_operation.get("target_crs"), str)
        or not isinstance(allowed_operation.get("operation_name"), str)
        or not isinstance(allowed_operation.get("pipeline_template"), str)
        or not isinstance(allowed_operation.get("accuracy_metres"), int | float)
    ):
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_OPERATION_MISMATCH",
            "The governed local datum-grid operation is incomplete.",
        )

    filename = str(grid["filename"])
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_PATH_INVALID",
            "The governed local datum-grid filename must be a single relative path component.",
        )
    grid_candidate = root / filename
    if grid_candidate.is_symlink():
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_PATH_INVALID",
            "The governed local datum-grid file must not be a symbolic link.",
        )
    grid_path = grid_candidate.resolve()
    try:
        grid_path.relative_to(root)
    except ValueError as exc:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_PATH_INVALID",
            "The governed local datum-grid path escapes its package directory.",
        ) from exc
    if not grid_path.is_file():
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_MISSING",
            "The governed local datum-grid file is absent or is not a regular file.",
        )
    local_bytes = grid_path.stat().st_size
    if local_bytes != grid["bytes"]:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_SIZE_MISMATCH",
            "The governed local datum-grid byte count does not match the source-bound manifest.",
        )
    local_sha256 = sha256_file(grid_path)
    if local_sha256 != grid["sha256"]:
        raise _governed_grid_error(
            "ERROR_GIS_CRS_GRID_HASH_MISMATCH",
            "The governed local datum-grid SHA-256 does not match the source-bound manifest.",
        )

    pipeline_template = str(allowed_operation["pipeline_template"])
    canonical_pipeline = pipeline_template.format(grid=filename)
    execution_pipeline = pipeline_template.format(grid=json.dumps(grid_path.as_posix()))
    transformer = Transformer.from_pipeline(execution_pipeline)
    source = grid.get("source") if isinstance(grid.get("source"), dict) else {}
    license_row = grid.get("license") if isinstance(grid.get("license"), dict) else {}
    grid_receipt = {
        "grid_id": grid["grid_id"],
        "short_name": filename,
        "full_name": str(grid_path),
        "package_name": f"mucaro-ai-governed-grid-{grid['grid_id']}",
        "url": source.get("proj_cdn_url"),
        "direct_download": True,
        "open_license": license_row.get("classification") == "PUBLIC_DOMAIN",
        "license_classification": license_row.get("classification"),
        "license_evidence_url": license_row.get("evidence_url"),
        "source_authority": source.get("authority"),
        "source_commit_sha": source.get("file_commit_sha"),
        "source_pinned_url": source.get("pinned_file_url"),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "manifest_retrieval_utc": manifest.get("retrieval_utc"),
        "proj_available": True,
        "local_file_available": True,
        "local_bytes": local_bytes,
        "local_sha256": local_sha256,
        "requires_network_download": False,
        "available_without_network": True,
        "provenance_frozen": True,
    }
    candidate = {
        "index": 0,
        "description": str(allowed_operation["operation_name"]),
        "definition_sha256": hashlib.sha256(canonical_pipeline.encode("utf-8")).hexdigest(),
        "accuracy": float(allowed_operation["accuracy_metres"]),
        "grids": [grid_receipt],
        "grid_metadata_complete": True,
        "requires_network_download": False,
        "grid_provenance_frozen": True,
        "offline_capable": True,
        "selection_policy": "exact_source_bound_governed_grid",
    }
    return {
        "transformer": transformer,
        "adapter": CRS_GOVERNED_GRID_ADAPTER,
        "canonical_pipeline": canonical_pipeline,
        "execution_pipeline_sha256": hashlib.sha256(execution_pipeline.encode("utf-8")).hexdigest(),
        "candidate": candidate,
        "manifest": {
            "schema_version": manifest["schema_version"],
            "path": str(manifest_path),
            "sha256": manifest_sha256,
            "retrieval_utc": manifest.get("retrieval_utc"),
            "grid_id": grid["grid_id"],
            "grid_sha256": local_sha256,
            "grid_bytes": local_bytes,
            "source_crs": allowed_operation["source_crs"],
            "target_crs": allowed_operation["target_crs"],
            "operation_authority": allowed_operation.get("operation_authority"),
            "accuracy_metres": float(allowed_operation["accuracy_metres"]),
            "area_of_use": allowed_operation.get("area_of_use"),
            "bounds": allowed_operation.get("bounds"),
            "network_access_performed": False,
            "independent_engine_validation": "OPEN",
        },
        "grid_id": grid["grid_id"],
        "operation_name": allowed_operation["operation_name"],
        "accuracy_metres": float(allowed_operation["accuracy_metres"]),
    }


def _grid_receipt(grid: Any) -> dict[str, Any]:
    short_name = str(getattr(grid, "short_name", "") or "")
    full_name = str(getattr(grid, "full_name", "") or "")
    local_path = Path(full_name).expanduser() if full_name else None
    local_file_available = bool(local_path and local_path.is_file())
    url = str(getattr(grid, "url", "") or "")
    requires_network_download = bool(url) and not local_file_available
    provenance_frozen = local_file_available or not short_name
    return {
        "short_name": short_name,
        "full_name": full_name or None,
        "package_name": str(getattr(grid, "package_name", "") or "") or None,
        "url": url or None,
        "direct_download": bool(getattr(grid, "direct_download", False)),
        "open_license": bool(getattr(grid, "open_license", False)),
        "proj_available": bool(getattr(grid, "available", False)),
        "local_file_available": local_file_available,
        "local_bytes": local_path.stat().st_size if local_file_available and local_path else None,
        "local_sha256": sha256_file(local_path) if local_file_available and local_path else None,
        "requires_network_download": requires_network_download,
        "available_without_network": bool(getattr(grid, "available", False)) and not requires_network_download,
        "provenance_frozen": provenance_frozen,
    }


def _transformer_grid_receipts(transformer: Transformer) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for operation in transformer.operations:
        for grid in operation.grids:
            receipt = _grid_receipt(grid)
            receipt["operation_name"] = operation.name
            receipts.append(receipt)
    return receipts


def _candidate_receipt(index: int, transformer: Transformer) -> dict[str, Any]:
    definition = transformer.definition or transformer.description or ""
    grids = _transformer_grid_receipts(transformer)
    grid_metadata_complete = "grids=" not in definition or bool(grids)
    return {
        "index": index,
        "description": transformer.description,
        "definition_sha256": hashlib.sha256(definition.encode("utf-8")).hexdigest() if definition else None,
        "accuracy": transformer.accuracy,
        "grids": grids,
        "grid_metadata_complete": grid_metadata_complete,
        "requires_network_download": any(grid["requires_network_download"] for grid in grids),
        "grid_provenance_frozen": all(grid["provenance_frozen"] for grid in grids),
        "offline_capable": grid_metadata_complete
        and all(
            grid["available_without_network"] and grid["provenance_frozen"]
            for grid in grids
        ),
    }


def _unavailable_operation_receipt(index: int, operation: Any) -> dict[str, Any]:
    grids = [_grid_receipt(grid) for grid in operation.grids]
    return {
        "index": index,
        "name": operation.name,
        "accuracy": operation.accuracy,
        "grids": grids,
        "requires_network_download": any(grid["requires_network_download"] for grid in grids),
    }


def _transform_coordinates(value: Any, transformer: Transformer) -> Any:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value[:2]):
        x, y = transformer.transform(float(value[0]), float(value[1]), errcheck=True)
        return [x, y, *value[2:]]
    if isinstance(value, list):
        return [_transform_coordinates(item, transformer) for item in value]
    raise GISProcessingError("ERROR_GIS_CRS_INVALID_COORDINATES", "Geometry contains a non-position coordinate value.")


def reproject_geojson_crs(
    path: Path,
    *,
    target_crs: str,
    cfg: Settings = settings,
    source_crs: str | None = None,
    _grid_root: Path | None = None,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    source_name = source_crs or source.crs
    try:
        source_obj = CRS.from_user_input(source_name)
        target_obj = CRS.from_user_input(target_crs)
        governed = _load_governed_grid_transform(source_obj, target_obj, grid_root=_grid_root)
        if governed is not None:
            transformer = governed["transformer"]
            adapter = governed["adapter"]
            candidate_receipts = [governed["candidate"]]
            selected_candidate_index = 0
            unavailable_operation_receipts: list[dict[str, Any]] = []
            engine_warnings: list[str] = []
            best_available = True
            pipeline = governed["canonical_pipeline"]
            execution_pipeline_sha256 = governed["execution_pipeline_sha256"]
            governed_grid_receipt: dict[str, Any] | None = governed["manifest"]
            transformer_description = governed["operation_name"]
            transformer_accuracy = governed["accuracy_metres"]
        else:
            with python_warnings.catch_warnings(record=True) as caught_warnings:
                python_warnings.simplefilter("always")
                transformer_group = TransformerGroup(source_obj, target_obj, always_xy=True)
            if not transformer_group.transformers:
                raise GISProcessingError(
                    "ERROR_GIS_CRS_PIPELINE_UNRESOLVED",
                    "PROJ did not provide an available transformation pipeline.",
                )
            candidate_receipts = [
                _candidate_receipt(index, candidate)
                for index, candidate in enumerate(transformer_group.transformers)
            ]
            selected_candidate_index = next(
                (row["index"] for row in candidate_receipts if row["offline_capable"]),
                None,
            )
            if selected_candidate_index is None:
                raise GISProcessingError(
                    "ERROR_GIS_CRS_NETWORK_PERMISSION_REQUIRED",
                    "PROJ did not provide a transformation candidate whose grid dependencies are available as frozen local files.",
                )
            transformer = transformer_group.transformers[selected_candidate_index]
            unavailable_operation_receipts = [
                _unavailable_operation_receipt(index, operation)
                for index, operation in enumerate(transformer_group.unavailable_operations)
            ]
            adapter = CRS_ADAPTER
            engine_warnings = [str(item.message) for item in caught_warnings]
            best_available = transformer_group.best_available
            pipeline = transformer.definition or transformer.description
            execution_pipeline_sha256 = None
            governed_grid_receipt = None
            transformer_description = transformer.description
            transformer_accuracy = transformer.accuracy
    except (ProjError, ValueError) as exc:
        raise GISProcessingError("ERROR_GIS_CRS_UNSUPPORTED", str(exc)) from exc
    if not pipeline or pipeline.lower().startswith("unavailable until"):
        raise GISProcessingError(
            "ERROR_GIS_CRS_PIPELINE_UNRESOLVED",
            "The selected PROJ transformation pipeline could not be frozen.",
        )
    pipeline_sha256 = hashlib.sha256(pipeline.encode("utf-8")).hexdigest()
    raw_features = source.payload.get("features")
    if not isinstance(raw_features, list) or not all(
        isinstance(feature, dict) for feature in raw_features
    ):
        raise GISProcessingError("ERROR_GIS_CRS_FEATURES", "All GeoJSON features must be objects.")
    output_features: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    for index, feature in enumerate(raw_features):
        geometry = feature.get("geometry")
        if geometry is None:
            output_features.append(deepcopy(feature))
            receipts.append(
                {
                    "feature_index": index,
                    "feature_id": _feature_id(feature, index),
                    "transform_state": "NULL_EMPTY",
                }
            )
            continue
        if not isinstance(geometry, dict) or "coordinates" not in geometry:
            raise GISProcessingError("ERROR_GIS_CRS_GEOMETRY", f"Feature {index} has unsupported geometry.")
        dims = _coordinate_dimension_summary(geometry)
        if dims["has_m"]:
            raise GISProcessingError(
                "ERROR_GIS_CRS_M_UNSUPPORTED",
                "M coordinates require an explicit semantic transform policy.",
            )
        transformed = deepcopy(feature)
        try:
            transformed["geometry"]["coordinates"] = _transform_coordinates(geometry["coordinates"], transformer)
        except ProjError as exc:
            raise GISProcessingError("ERROR_GIS_CRS_TRANSFORM", str(exc)) from exc
        output_features.append(transformed)
        receipts.append(
            {
                "feature_index": index,
                "feature_id": _feature_id(feature, index),
                "transform_state": "TRANSFORMED" if source_obj != target_obj else "NOOP",
                "coordinate_dimension": dims["coordinate_dimension"],
                "has_z": dims["has_z"],
            }
        )
    created_at = utc_now()
    report_id = stable_id(
        "gisreport",
        "crs_engine",
        source.sha256,
        source_obj.to_string(),
        target_obj.to_string(),
        created_at,
    )
    output_payload = deepcopy(source.payload)
    output_payload["features"] = output_features
    output_payload["crs"] = {"type": "name", "properties": {"name": target_obj.to_string()}}
    output = _write_geojson_output(cfg, report_id, output_payload)
    report_warnings = []
    if not best_available:
        report_warnings.append("GIS_CRS_BEST_OPERATION_UNAVAILABLE")
    if selected_candidate_index > 0:
        report_warnings.append("GIS_CRS_OFFLINE_CANDIDATE_FALLBACK")
    if any(
        row["requires_network_download"]
        for row in candidate_receipts[:selected_candidate_index]
    ):
        report_warnings.append("GIS_CRS_NETWORK_GRID_SKIPPED")
    if any(row.get("has_z") for row in receipts):
        report_warnings.append("GIS_CRS_Z_PASSTHROUGH_UNTRANSFORMED")
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_CRS_REPROJECT_WARN" if report_warnings else "GIS_CRS_REPROJECT_OK",
        "operation": "reproject_geojson_crs",
        "created_at": created_at,
        "parameters": {
            "adapter": adapter,
            "source_crs": source_obj.to_string(),
            "target_crs": target_obj.to_string(),
            "always_xy": True,
            "axis_order_policy": "traditional_gis_xy",
            "network_policy": CRS_NETWORK_POLICY,
            "proj_network_enabled": network.is_network_enabled(),
            "pyproj_version": pyproj_version,
            "proj_version": proj_version_str,
            "available_candidate_count": len(candidate_receipts),
            "unavailable_candidate_count": len(unavailable_operation_receipts),
            "candidate_inventory": candidate_receipts,
            "unavailable_operation_inventory": unavailable_operation_receipts,
            "selected_candidate_index": selected_candidate_index,
            "selected_candidate_offline_capable": candidate_receipts[selected_candidate_index][
                "offline_capable"
            ],
            "selected_candidate_grid_count": len(
                candidate_receipts[selected_candidate_index]["grids"]
            ),
            "best_available": best_available,
            "engine_warnings": engine_warnings,
            "transformer_description": transformer_description,
            "transformer_definition": pipeline,
            "transformer_definition_sha256": pipeline_sha256,
            "execution_pipeline_sha256": execution_pipeline_sha256,
            "accuracy": transformer_accuracy,
            "governed_grid": governed_grid_receipt,
        },
        "source": {
            "path": str(path),
            "sha256": source.sha256,
            "crs": source_obj.to_string(),
            "declared_crs": source.crs,
            "feature_count": len(raw_features),
        },
        "output": {
            "path": output["path"],
            "sha256": output["sha256"],
            "crs": target_obj.to_string(),
            "feature_count": len(output_features),
        },
        "features": receipts,
        "invariants": {
            "feature_row_conservation": len(raw_features) == len(output_features) == len(receipts),
            "source_crs_parseable": True,
            "target_crs_parseable": True,
            "axis_order_explicit": True,
            "transform_pipeline_frozen": bool(pipeline) and not pipeline.lower().startswith("unavailable until"),
            "offline_policy_satisfied": candidate_receipts[selected_candidate_index]["offline_capable"],
            "grid_inventory_complete": all(row["grid_metadata_complete"] for row in candidate_receipts),
            "governed_grid_binding_satisfied": governed_grid_receipt is None
            or (
                governed_grid_receipt["grid_sha256"]
                == candidate_receipts[selected_candidate_index]["grids"][0]["local_sha256"]
                and governed_grid_receipt["grid_bytes"]
                == candidate_receipts[selected_candidate_index]["grids"][0]["local_bytes"]
                and governed_grid_receipt["network_access_performed"] is False
            ),
        },
        "warnings": report_warnings,
        "limitations": [
            "XY coordinates are transformed with pyproj/PROJ using always_xy=True",
            "Z values are preserved but not vertically transformed",
            "M coordinates fail closed",
            "remote grid downloads are not permitted; a governed local grid is used only for an exact source-bound CRS pair, otherwise a lower-accuracy offline candidate may be selected",
            "local grid files are bound by path, byte count, and SHA-256 when selected",
            "candidate availability and transformer accuracy remain environment-dependent and are exposed in the receipt",
            "governed regional datum-grid controls are implementation and provenance evidence, not independent-engine certification",
        ],
        "falsification_criteria": [
            "reject if feature_row_conservation is false",
            "reject broad CRS claims if transformer definition or axis-order policy is absent",
            "reject if the selected candidate requires an unfrozen network grid",
            "reject the governed-grid result if the manifest, grid bytes, authorized CRS pair, area of use, or canonical pipeline differs from the frozen receipt",
            "independently verify material transformations when local grid inventory or accuracy differs between runtimes",
        ],
    }
    return _write_report(cfg, report)
