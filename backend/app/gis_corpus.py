from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from shapely.geometry import shape

try:
    import rasterio
except ImportError:  # pragma: no cover
    rasterio = None

from .gis_processing import GISProcessingError, GIS_REPORT_SCHEMA_VERSION, _coordinate_dimension_summary
from .capability_contracts import require_gis_report_contract
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


def _validate_provenance(asset_id: str, asset: dict[str, Any]) -> None:
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
        raise GISProcessingError(
            "ERROR_GIS_CORPUS_PROVENANCE",
            f"{asset_id} authority, URL, retrieval UTC, declared CRS, and query are required.",
        )


def _validate_geojson_asset(asset_id: str, raw: bytes, asset: dict[str, Any]) -> dict[str, Any]:
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

    return {
        "feature_count": len(features),
        "stable_id_path": stable_id_path,
        "stable_id_unique": True,
        "geometry_counts": geometry_counts,
        "geometry_valid_count": len(features),
        "coordinate_dimension_counts": coordinate_dimension_counts,
        "features_with_z": features_with_z,
        "features_with_m": features_with_m,
    }


def _numeric_sequence_matches(observed: list[Any], expected: Any, *, tolerance: float = 1e-6) -> bool:
    if not isinstance(expected, list) or len(observed) != len(expected):
        return False
    for left, right in zip(observed, expected, strict=True):
        if left is None or right is None:
            if left is not right:
                return False
        elif not math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=tolerance):
            return False
    return True


def _validate_raster_asset(asset_id: str, source: Path, asset: dict[str, Any]) -> dict[str, Any]:
    if rasterio is None:
        raise GISProcessingError("ERROR_GIS_CORPUS_RASTER_ENGINE", "rasterio is required to validate raster corpus assets.")
    try:
        with rasterio.open(source) as dataset:
            observed_crs = dataset.crs.to_string() if dataset.crs else None
            transform = list(dataset.transform)[:6]
            bounds = list(dataset.bounds)
            nodata_values = list(dataset.nodatavals)
            dtypes = list(dataset.dtypes)
            bands: list[dict[str, Any]] = []
            for band_index in range(1, dataset.count + 1):
                values = dataset.read(band_index, masked=True)
                compressed = values.compressed()
                valid_count = int(values.count())
                bands.append(
                    {
                        "band": band_index,
                        "valid_cell_count": valid_count,
                        "nodata_cell_count": int(values.size - valid_count),
                        "zero_cell_count": int((compressed == 0).sum()),
                        "minimum": float(compressed.min()) if valid_count else None,
                        "maximum": float(compressed.max()) if valid_count else None,
                        "mean": float(compressed.mean()) if valid_count else None,
                    }
                )
            observed = {
                "driver": dataset.driver,
                "width": dataset.width,
                "height": dataset.height,
                "band_count": dataset.count,
                "dtypes": dtypes,
                "nodata_values": nodata_values,
                "bounds": bounds,
                "transform": transform,
                "bands": bands,
            }
    except GISProcessingError:
        raise
    except Exception as exc:
        raise GISProcessingError("ERROR_GIS_CORPUS_RASTER", f"{asset_id} raster could not be inspected: {exc}") from exc

    scalar_expectations = {
        "driver": "expected_driver",
        "width": "expected_width",
        "height": "expected_height",
        "band_count": "expected_band_count",
        "dtypes": "expected_dtypes",
    }
    for observed_key, expected_key in scalar_expectations.items():
        if observed[observed_key] != asset.get(expected_key):
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_RASTER_METADATA",
                f"{asset_id} {observed_key} differs: expected={asset.get(expected_key)!r}, observed={observed[observed_key]!r}.",
            )
    if observed_crs != asset.get("declared_crs"):
        raise GISProcessingError(
            "ERROR_GIS_CORPUS_RASTER_CRS",
            f"{asset_id} embedded CRS differs: expected={asset.get('declared_crs')!r}, observed={observed_crs!r}.",
        )
    for observed_key, expected_key in (
        ("nodata_values", "expected_nodata_values"),
        ("bounds", "expected_bounds"),
        ("transform", "expected_transform"),
    ):
        if not _numeric_sequence_matches(observed[observed_key], asset.get(expected_key)):
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_RASTER_METADATA",
                f"{asset_id} {observed_key} differs: expected={asset.get(expected_key)!r}, observed={observed[observed_key]!r}.",
            )

    expected_bands = asset.get("expected_bands")
    if not isinstance(expected_bands, list) or len(expected_bands) != len(bands):
        raise GISProcessingError("ERROR_GIS_CORPUS_RASTER_BANDS", f"{asset_id} expected_bands must close to band_count.")
    for observed_band, expected_band in zip(bands, expected_bands, strict=True):
        if not isinstance(expected_band, dict):
            raise GISProcessingError("ERROR_GIS_CORPUS_RASTER_BANDS", f"{asset_id} expected band receipt must be an object.")
        for field in ("band", "valid_cell_count", "nodata_cell_count", "zero_cell_count"):
            if observed_band[field] != expected_band.get(field):
                raise GISProcessingError(
                    "ERROR_GIS_CORPUS_RASTER_BANDS",
                    f"{asset_id} band {observed_band['band']} {field} differs: expected={expected_band.get(field)!r}, observed={observed_band[field]!r}.",
                )
        for field in ("minimum", "maximum", "mean"):
            if not _numeric_sequence_matches([observed_band[field]], [expected_band.get(field)]):
                raise GISProcessingError(
                    "ERROR_GIS_CORPUS_RASTER_BANDS",
                    f"{asset_id} band {observed_band['band']} {field} differs: expected={expected_band.get(field)!r}, observed={observed_band[field]!r}.",
                )
    response_headers = asset.get("response_headers")
    if (
        not isinstance(response_headers, dict)
        or response_headers.get("content_type") != asset.get("media_type")
        or response_headers.get("content_length") != source.stat().st_size
        or not response_headers.get("etag")
    ):
        raise GISProcessingError(
            "ERROR_GIS_CORPUS_RASTER_METADATA",
            f"{asset_id} response content type, length, and ETag must match the frozen raster manifestation.",
        )
    if asset.get("elevation_units") != "meters" or not asset.get("elevation_units_evidence"):
        raise GISProcessingError("ERROR_GIS_CORPUS_RASTER_UNITS", f"{asset_id} must freeze metre elevation units and evidence.")
    if not asset.get("source_manifestation"):
        raise GISProcessingError("ERROR_GIS_CORPUS_PROVENANCE", f"{asset_id} must classify the raster source manifestation.")

    return {
        **observed,
        "embedded_crs": observed_crs,
        "elevation_units": asset["elevation_units"],
        "elevation_units_evidence": asset["elevation_units_evidence"],
        "source_manifestation": asset["source_manifestation"],
        "grid_cell_count": observed["width"] * observed["height"],
        "band_accounting_closed": all(
            band["valid_cell_count"] + band["nodata_cell_count"] == observed["width"] * observed["height"]
            and band["zero_cell_count"] <= band["valid_cell_count"]
            for band in bands
        ),
    }


def validate_real_gis_corpus(manifest_path: Path, *, cfg: Any) -> dict[str, Any]:
    cfg.ensure_dirs()
    manifest, payload, manifest_sha = _load_manifest(manifest_path)
    root = Path(cfg.project_root).expanduser().resolve()
    seen_asset_ids: set[str] = set()
    seen_asset_paths: set[Path] = set()
    asset_receipts: list[dict[str, Any]] = []
    asset_type_counts = {"geojson": 0, "raster": 0}

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
        _validate_provenance(asset_id, asset)
        asset_type = asset.get("asset_type")
        if asset_type == "geojson":
            type_receipt = _validate_geojson_asset(asset_id, raw, asset)
        elif asset_type == "raster":
            type_receipt = _validate_raster_asset(asset_id, source, asset)
        else:
            raise GISProcessingError(
                "ERROR_GIS_CORPUS_ASSET_TYPE",
                f"{asset_id} asset_type must be one of: geojson, raster.",
            )
        asset_type_counts[asset_type] += 1

        asset_receipts.append(
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "path": str(source),
                "sha256": observed_sha,
                "bytes": observed_bytes,
                "authority": asset["authority"],
                "service": asset.get("service"),
                "source_url": asset["source_url"],
                "query": asset["query"],
                "retrieval_utc": asset["retrieval_utc"],
                "response_headers": asset.get("response_headers"),
                "media_type": asset.get("media_type"),
                "declared_crs": asset["declared_crs"],
                **type_receipt,
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
        "asset_type_counts": asset_type_counts,
        "invariants": {
            "asset_accounting_closed": len(asset_receipts) == len(payload["assets"]),
            "asset_type_accounting_closed": sum(asset_type_counts.values()) == len(asset_receipts),
            "all_asset_hashes_matched": True,
            "all_feature_counts_matched": True,
            "all_stable_ids_present_and_unique": True,
            "all_geometries_valid": True,
            "all_coordinate_dimensions_accounted": True,
            "all_raster_grids_matched": True,
            "all_raster_band_values_accounted": all(
                asset.get("asset_type") != "raster" or asset.get("band_accounting_closed") is True
                for asset in asset_receipts
            ),
            "provenance_complete": True,
        },
        "warnings": [],
        "limitations": [
            "The frozen fixtures are bounded authoritative manifestations, not exhaustive samples of their source services.",
            "Vector CRS is supported by recorded queries and service contracts; raster CRS is checked against the embedded GeoTIFF CRS.",
            "The 3DEP raster is a dynamic export that the service reprojected and bilinearly resampled; it is not native source-tile byte identity.",
            "A matching byte hash proves source manifestation identity, not semantic currency or equivalence to a later service response.",
        ],
        "falsification_criteria": [
            "reject if any frozen asset byte count or SHA-256 differs",
            "reject unknown asset types instead of guessing from filenames",
            "reject if expected vector feature, geometry, schema, or stable-ID accounting does not close",
            "reject if XY, XYZ, XYZM, or mixed coordinate dimensionality differs from the frozen manifest",
            "reject if raster driver, grid, CRS, NODATA, band accounting, or value statistics differ from the frozen manifest",
            "reject malformed, empty, invalid, or source-service error payloads",
        ],
    }
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"
    require_gis_report_contract(report, report_path=str(report_path))
    write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}
