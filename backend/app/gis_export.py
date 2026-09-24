from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION
from .utils import stable_id, utc_now, write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_map_export_manifest(*, cfg: Any, output_path: Path, layers: list[dict[str, Any]], extent: list[float], output_crs: str, renderer: str, renderer_version: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg.ensure_dirs(); output = output_path.expanduser().resolve()
    if not output.exists() or not output.is_file():
        raise GISProcessingError("ERROR_GIS_EXPORT_NOT_FOUND", f"Map/export artifact not found: {output}")
    if output.stat().st_size <= 0:
        raise GISProcessingError("ERROR_GIS_EXPORT_EMPTY", "Map/export artifact must not be empty.")
    if not isinstance(extent, list) or len(extent) != 4 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in extent) or extent[0] >= extent[2] or extent[1] >= extent[3]:
        raise GISProcessingError("ERROR_GIS_EXPORT_EXTENT", "Export extent must be [minx,miny,maxx,maxy].")
    if not all(isinstance(value, str) and value.strip() for value in (output_crs, renderer, renderer_version)):
        raise GISProcessingError("ERROR_GIS_EXPORT_IDENTITY", "Output CRS and renderer identity/version are required.")
    if not isinstance(layers, list) or not all(isinstance(layer, dict) for layer in layers):
        raise GISProcessingError("ERROR_GIS_EXPORT_LAYER", "Layers must be an array of source descriptors.")
    frozen_layers = []
    for index, layer in enumerate(layers):
        path_value = layer.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise GISProcessingError("ERROR_GIS_EXPORT_LAYER", f"Layer {index} path is required.")
        source = Path(path_value).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise GISProcessingError("ERROR_GIS_EXPORT_LAYER", f"Layer source not found: {source}")
        declared_crs = layer.get("crs")
        if not isinstance(declared_crs, str) or not declared_crs.strip():
            raise GISProcessingError("ERROR_GIS_EXPORT_LAYER_CRS", f"Layer {index} declared CRS is required.")
        frozen_layers.append({"layer_index": index, "path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size, "declared_crs": declared_crs, "role": layer.get("role"), "name": layer.get("name")})
    created_at = utc_now(); output_sha = _sha256(output)
    report_id = stable_id("gisreport", "map_export", output_sha, output_crs, renderer, renderer_version, created_at)
    report = {"schema_version": GIS_REPORT_SCHEMA_VERSION, "report_id": report_id, "status": "GIS_MAP_EXPORT_MANIFEST_OK", "operation": "create_map_export_manifest", "created_at": created_at, "output": {"path": str(output), "sha256": output_sha, "bytes": output.stat().st_size, "crs": output_crs, "extent": [float(v) for v in extent]}, "renderer": {"name": renderer, "version": renderer_version}, "parameters": parameters or {}, "layers": frozen_layers, "invariants": {"layer_count": len(frozen_layers), "all_layer_hashes_present": all(bool(row["sha256"]) for row in frozen_layers), "output_hash_present": bool(output_sha), "renderer_identity_present": True}, "warnings": ["GIS_EXPORT_NO_LAYERS"] if not frozen_layers else [], "limitations": ["This receipt certifies the exported artifact and declared rendering inputs; it does not infer that the renderer used every source byte semantically.", "Visual correctness requires independent regression controls for material maps."], "falsification_criteria": ["reject if output SHA-256 changes", "reject if any bound layer SHA-256 changes", "reject provenance claims when renderer identity/version or output CRS is absent"]}
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"; write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}
