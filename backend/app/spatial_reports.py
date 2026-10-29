"""Shared byte verification and artifact admission for spatial task outputs."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from .artifact_identity import create_artifact_record
from .config import Settings
from .gis_processing import GISProcessingError


def record_spatial_artifacts(
    report: dict[str, Any],
    *,
    cfg: Settings,
    run_dir: Path,
    parent_ids: list[str],
    producer: str,
    max_report_bytes: int,
    max_output_bytes: int,
    intermediates: list[tuple[str, dict[str, Any]]] | None = None,
    downloads: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Admit only unchanged regular outputs inside this invocation's directory."""
    entries = [*(intermediates or [])]
    if report.get("output"):
        entries.append(("output", report["output"]))
    entries.append(("report", {"path": report["report_path"], "sha256": report["report_sha256"]}))
    artifact_ids = []
    for role, entry in entries:
        path = Path(entry["path"])
        limit = max_report_bytes if "report" in role else max_output_bytes
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(run_dir.resolve())
            or path.stat().st_size > limit
        ):
            raise GISProcessingError(
                "ERROR_GIS_OUTPUT_LIMIT", "Spatial output exceeds the artifact budget or invocation boundary."
            )
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise GISProcessingError(
                "ERROR_GIS_OUTPUT_HASH_MISMATCH", "Spatial output changed after the engine recorded it."
            )
        artifact = create_artifact_record(
            processed_dir=cfg.processed_dir,
            artifact_type="DERIVED",
            payload=raw,
            logical_identity=f"gis_{role}:{path}",
            producer=producer,
            producer_version="1.0.0",
            parent_artifact_ids=[*parent_ids, *artifact_ids],
            source_path_or_uri=str(path),
        )
        artifact_ids.append(artifact.artifact_id)
        if downloads is not None:
            downloads.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "name": path.name,
                    "role": role,
                    "sha256": artifact.sha256,
                    "bytes": len(raw),
                }
            )
    return artifact_ids


def numeric_report_view(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Keep raw report bytes intact; represent non-finite metadata explicitly in JSON views."""
    special = []

    def visit(item: Any, pointer: str) -> Any:
        if isinstance(item, float) and not math.isfinite(item):
            if not any(segment in {"nodata", "nodata_values"} for segment in pointer.split("/")):
                raise GISProcessingError(
                    "ERROR_GIS_NONFINITE_RESULT",
                    "A computed spatial result is not finite. Reduce the numeric range or split the workload; the result cannot be admitted as a valid calculation.",
                )
            special.append(
                {
                    "json_pointer": pointer,
                    "state": "NAN" if math.isnan(item) else "POSITIVE_INFINITY" if item > 0 else "NEGATIVE_INFINITY",
                }
            )
            return None
        if isinstance(item, dict):
            return {
                key: visit(child, f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}")
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [visit(child, f"{pointer}/{index}") for index, child in enumerate(item)]
        return item

    return visit(value, ""), special
