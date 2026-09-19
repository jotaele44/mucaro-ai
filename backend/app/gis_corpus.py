from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from shapely.geometry import shape

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary
from .utils import stable_id, utc_now, write_json


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REAL_GIS_CORPUS_MANIFEST = ROOT / "_architecture" / "gis_real_corpus_manifest.json"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _lookup_feature_value(feature: dict[str, Any], dotted_path: str) -> Any:
    current: Any = feature
    for part in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _load_manifest(path: Path) -> tuple[Path, dict[str, Any], str]:
    manifest_path = path.expanduser().resolve()
    if not manifest_path.exists() or not manifest_path.is_file():
        raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST_NOT_FOUND", f"Corpus manifest not found: {manifest_path}")
    try:
        raw = manifest_path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"Corpus manifest could not be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "mucaro_gis_real_corpus_v1":
        raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", "Corpus manifest schema_version must be mucaro_gis_real_corpus_v1.")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", "Corpus manifest assets must be a non-empty array.")
    return manifest_path, payload, _sha256_bytes(raw)


def _valid_retrieval_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def validate_real_gis_corpus(manifest_path: Path, *, cfg: Any) -> dict[str, Any]:
    cfg.ensure_dirs()
    manifest, payload, manifest_sha = _load_manifest(manifest_path)
    root = Path(cfg.project_root).expanduser().resolve()
    seen_asset_ids: set[str] = set()
    seen_asset_paths: set[Path] = set()
    asset_receipts: list[dict[str, Any]] = []

    for index, asset in enumerate(payload["assets"]):
        if not isinstance(asset, dict):
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"assets[{index}] must be an object.")
        asset_id = str(asset.get("asset_id") or "")
        if not asset_id or asset_id in seen_asset_ids:
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"assets[{index}] asset_id must be present and unique.")
        seen_asset_ids.add(asset_id)
        relative_path = asset.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"{asset_id} path must be a non-empty string.")
        source = (root / relative_path).resolve()
        if not source.is_relative_to(root):
            raise GISProcessingError("ERROR_GIS_CORPUS_PATH", f"{asset_id} escapes the project root.")
        if source in seen_asset_paths:
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"{asset_id} repeats a corpus asset path.")
        seen_asset_paths.add(source)
        if not source.exists() or not source.is_file():
            raise GISProcessingError("ERROR_GIS_CORPUS_ASSET_NOT_FOUND", f"{asset_id} asset not found: {source}")

        raw = source.read_bytes()
        observed_sha = _sha256_bytes(raw)
        observed_bytes = len(raw)
        if observed_sha != asset.get("sha256") or observed_bytes != asset.get("bytes"):
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_HASH",
                f"{asset_id} byte identity mismatch: sha256={observed_sha}, bytes={observed_bytes}.",
            )
        try:
            geojson = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GISProcessingError("ERROR_GIS_CORPUS_GEOJSON", f"{asset_id} is not valid UTF-8 GeoJSON: {exc}") from exc
        if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection" or not isinstance(geojson.get("features"), list):
            raise GISProcessingError("ERROR_GIS_CORPUS_GEOJSON", f"{asset_id} must be a GeoJSON FeatureCollection.")
        if geojson.get("error") is not None:
            raise GISProcessingError("ERROR_GIS_CORPUS_SOURCE_RESPONSE", f"{asset_id} contains a source-service error payload.")

        features = geojson["features"]
        expected_feature_count = asset.get("expected_feature_count")
        if not isinstance(expected_feature_count, int) or len(features) != expected_feature_count:
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_COUNT",
                f"{asset_id} expected {expected_feature_count!r} features but observed {len(features)}.",
            )
        stable_id_path = asset.get("stable_id_path")
        if not isinstance(stable_id_path, str) or not stable_id_path:
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"{asset_id} stable_id_path is required.")
        required_fields = asset.get("required_property_fields")
        if not isinstance(required_fields, list) or not all(isinstance(field, str) and field for field in required_fields):
            raise GISProcessingError("ERROR_GIS_CORPUS_MANIFEST", f"{asset_id} required_property_fields must be strings.")

        stable_ids: list[str] = []
        geometry_counts: dict[str, int] = {}
        coordinate_dimension_counts: dict[str, int] = {}
        features_with_z = 0
        features_with_m = 0
        for feature_index, feature in enumerate(features):
            if not isinstance(feature, dict):
                raise GISProcessingError("ERROR_GIS_CORPUS_FEATURE", f"{asset_id} feature {feature_index} is not an object.")
            observed_id = _lookup_feature_value(feature, stable_id_path)
            if observed_id is None or str(observed_id) == "":
                raise GISProcessingError("ERROR_GIS_CORPUS_ID", f"{asset_id} feature {feature_index} lacks {stable_id_path}.")
            stable_ids.append(str(observed_id))
            properties = feature.get("properties")
            if not isinstance(properties, dict) or any(field not in properties for field in required_fields):
                raise GISProcessingError("ERROR_GIS_CORPUS_SCHEMA", f"{asset_id} feature {feature_index} lacks required properties.")
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict):
                raise GISProcessingError("ERROR_GIS_CORPUS_GEOMETRY", f"{asset_id} feature {feature_index} lacks geometry.")
            try:
                parsed = shape(geometry)
            except Exception as exc:
                raise GISProcessingError("ERROR_GIS_CORPUS_GEOMETRY", f"{asset_id} feature {feature_index} geometry could not be parsed: {exc}") from exc
            if parsed.is_empty or not parsed.is_valid:
                raise GISProcessingError("ERROR_GIS_CORPUS_GEOMETRY", f"{asset_id} feature {feature_index} geometry is empty or invalid.")
            geometry_counts[parsed.geom_type] = geometry_counts.get(parsed.geom_type, 0) + 1
            dimensions = _coordinate_dimension_summary(geometry)
            label = str(dimensions["coordinate_dimension"])
            coordinate_dimension_counts[label] = coordinate_dimension_counts.get(label, 0) + 1
            features_with_z += int(dimensions["has_z"] is True)
            features_with_m += int(dimensions["has_m"] is True)

        if len(stable_ids) != len(set(stable_ids)):
            raise GISProcessingError("ERROR_GIS_CORPUS_DUPLICATE_ID", f"{asset_id} stable IDs are not unique.")
        expected_geometry_counts = asset.get("expected_geometry_counts")
        if geometry_counts != expected_geometry_counts:
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_GEOMETRY_COUNTS",
                f"{asset_id} geometry counts differ: expected={expected_geometry_counts!r}, observed={geometry_counts!r}.",
            )
        expected_dimension_counts = asset.get("expected_coordinate_dimension_counts")
        if coordinate_dimension_counts != expected_dimension_counts:
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_DIMENSIONS",
                f"{asset_id} coordinate dimensions differ: expected={expected_dimension_counts!r}, observed={coordinate_dimension_counts!r}.",
            )
        source_url = asset.get("source_url")
        parsed_url = urlparse(source_url) if isinstance(source_url, str) else None
        if (
            not asset.get("authority")
            or parsed_url is None
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or not _valid_retrieval_utc(asset.get("retrieval_utc"))
            or not asset.get("declared_crs")
            or not isinstance(asset.get("query"), dict)
            or not asset["query"]
        ):
            raise GISProcessingError("ERROR_GIS_CORPUS_PROVENANCE", f"{asset_id} authority, URL, retrieval UTC, and declared CRS are required.")

        asset_receipts.append(
            {
                "asset_id": asset_id,
                "path": str(source),
                "sha256": observed_sha,
                "bytes": observed_bytes,
                "authority": asset["authority"],
                "source_url": asset["source_url"],
                "retrieval_utc": asset["retrieval_utc"],
                "declared_crs": asset["declared_crs"],
                "feature_count": len(features),
                "stable_id_path": stable_id_path,
                "stable_id_unique": True,
                "geometry_counts": geometry_counts,
                "geometry_valid_count": len(features),
                "coordinate_dimension_counts": coordinate_dimension_counts,
                "features_with_z": features_with_z,
                "features_with_m": features_with_m,
            }
        )

    created_at = utc_now()
    report_id = stable_id("gisreport", "real_corpus", manifest_sha, created_at)
    report = {
        "schema_version": GIS_REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "GIS_CORPUS_VALIDATION_OK",
        "operation": "validate_real_gis_corpus",
        "created_at": created_at,
        "source": {"path": str(manifest), "sha256": manifest_sha, "bytes": manifest.stat().st_size},
        "assets": asset_receipts,
        "invariants": {
            "asset_accounting_closed": len(asset_receipts) == len(payload["assets"]),
            "all_asset_hashes_matched": True,
            "all_feature_counts_matched": True,
            "all_stable_ids_present_and_unique": True,
            "all_geometries_valid": True,
            "all_coordinate_dimensions_accounted": True,
            "provenance_complete": True,
        },
        "warnings": [],
        "limitations": [
            "The frozen fixtures are bounded authoritative snapshots, not an exhaustive sample of either source service.",
            "Declared CRS is supported by the recorded query and GeoJSON service contract; the snapshots do not contain a legacy GeoJSON crs member.",
            "A matching byte hash proves source manifestation identity, not semantic currency or equivalence to a later service response.",
        ],
        "falsification_criteria": [
            "reject if any frozen asset byte count or SHA-256 differs",
            "reject if expected feature, geometry, schema, or stable-ID accounting does not close",
            "reject if XY, XYZ, XYZM, or mixed coordinate dimensionality differs from the frozen manifest",
            "reject malformed, empty, invalid, or source-service error payloads",
        ],
    }
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"
    write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}
