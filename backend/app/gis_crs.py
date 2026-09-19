from __future__ import annotations

import hashlib
import warnings as python_warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer, __version__ as pyproj_version, network, proj_version_str
from pyproj.exceptions import ProjError
from pyproj.transformer import TransformerGroup

from .config import Settings, settings
from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary, _feature_id, _load_geojson, _write_geojson_output, _write_report
from .utils import sha256_file, stable_id, utc_now

CRS_ADAPTER = "pyproj_transformer_group_always_xy_v1"
CRS_NETWORK_POLICY = "offline_only"


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


def reproject_geojson_crs(path: Path, *, target_crs: str, cfg: Settings = settings, source_crs: str | None = None) -> dict[str, Any]:
    cfg.ensure_dirs()
    source = _load_geojson(path)
    source_name = source_crs or source.crs
    try:
        source_obj = CRS.from_user_input(source_name)
        target_obj = CRS.from_user_input(target_crs)
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
    except (ProjError, ValueError) as exc:
        raise GISProcessingError("ERROR_GIS_CRS_UNSUPPORTED", str(exc)) from exc
    pipeline = transformer.definition or transformer.description
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
    if not transformer_group.best_available:
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
            "adapter": CRS_ADAPTER,
            "source_crs": source_obj.to_string(),
            "target_crs": target_obj.to_string(),
            "always_xy": True,
            "axis_order_policy": "traditional_gis_xy",
            "network_policy": CRS_NETWORK_POLICY,
            "proj_network_enabled": network.is_network_enabled(),
            "pyproj_version": pyproj_version,
            "proj_version": proj_version_str,
            "available_candidate_count": len(transformer_group.transformers),
            "unavailable_candidate_count": len(transformer_group.unavailable_operations),
            "candidate_inventory": candidate_receipts,
            "unavailable_operation_inventory": unavailable_operation_receipts,
            "selected_candidate_index": selected_candidate_index,
            "selected_candidate_offline_capable": candidate_receipts[selected_candidate_index][
                "offline_capable"
            ],
            "selected_candidate_grid_count": len(
                candidate_receipts[selected_candidate_index]["grids"]
            ),
            "best_available": transformer_group.best_available,
            "engine_warnings": [str(item.message) for item in caught_warnings],
            "transformer_description": transformer.description,
            "transformer_definition": pipeline,
            "transformer_definition_sha256": pipeline_sha256,
            "accuracy": transformer.accuracy,
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
        },
        "warnings": report_warnings,
        "limitations": [
            "XY coordinates are transformed with pyproj/PROJ using always_xy=True",
            "Z values are preserved but not vertically transformed",
            "M coordinates fail closed",
            "remote grid downloads are not permitted; a lower-accuracy offline candidate may be selected",
            "local grid files are bound by path, byte count, and SHA-256 when selected",
            "candidate availability and transformer accuracy remain environment-dependent and are exposed in the receipt",
        ],
        "falsification_criteria": [
            "reject if feature_row_conservation is false",
            "reject broad CRS claims if transformer definition or axis-order policy is absent",
            "reject if the selected candidate requires an unfrozen network grid",
            "independently verify material transformations when local grid inventory or accuracy differs between runtimes",
        ],
    }
    return _write_report(cfg, report)
