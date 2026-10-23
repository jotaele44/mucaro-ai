from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .gis_processing import GISProcessingError
from .utils import stable_id, utc_now, write_json


GIS_RENDER_VALIDATION_OK = "GIS_RENDER_VALIDATION_OK"
GIS_RENDER_VALIDATION_WARN = "GIS_RENDER_VALIDATION_WARN"
GIS_RENDER_VALIDATION_SCHEMA_VERSION = "mucaro_gis_render_validation_v1"
DEFAULT_MAX_RENDER_PIXELS = 50_000_000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_dimension(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GISProcessingError(f"ERROR_GIS_RENDER_{name.upper()}_INVALID", f"{name} must be a positive integer.")
    return value


def _manifest_binding(output: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists() or not manifest_path.is_file():
        raise GISProcessingError("ERROR_GIS_RENDER_MANIFEST_NOT_FOUND", "Source map export manifest was not found.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GISProcessingError(
            "ERROR_GIS_RENDER_MANIFEST_INVALID", "Source map export manifest is not valid JSON."
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("status") != "GIS_MAP_EXPORT_MANIFEST_OK":
        raise GISProcessingError(
            "ERROR_GIS_RENDER_MANIFEST_INVALID", "Source manifest is not a valid map export receipt."
        )
    manifest_output = manifest.get("output")
    if not isinstance(manifest_output, dict) or not isinstance(manifest_output.get("sha256"), str):
        raise GISProcessingError("ERROR_GIS_RENDER_MANIFEST_OUTPUT_MISSING", "Source manifest output hash is required.")
    output_hash = _sha256(output)
    if manifest_output["sha256"] != output_hash:
        raise GISProcessingError(
            "ERROR_GIS_RENDER_MANIFEST_HASH_MISMATCH", "Rendered output hash does not match source manifest."
        )
    manifest_output_path = manifest_output.get("path")
    path_match = True
    if isinstance(manifest_output_path, str) and manifest_output_path:
        path_match = Path(manifest_output_path).expanduser().resolve() == output
        if not path_match:
            raise GISProcessingError(
                "ERROR_GIS_RENDER_MANIFEST_PATH_MISMATCH", "Rendered output path does not match source manifest."
            )
    return {
        "path": str(manifest_path.resolve()),
        "sha256": _sha256(manifest_path),
        "output_sha256": manifest_output["sha256"],
        "output_path_bound": path_match,
    }


def validate_rendered_map(
    *,
    cfg: Any,
    output_path: Path,
    manifest_path: Path | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    max_pixels: int = DEFAULT_MAX_RENDER_PIXELS,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    output = output_path.expanduser().resolve()
    if not output.exists() or not output.is_file():
        raise GISProcessingError("ERROR_GIS_RENDER_NOT_FOUND", "Rendered map artifact was not found.")
    size = output.stat().st_size
    if size <= 0:
        raise GISProcessingError("ERROR_GIS_RENDER_EMPTY", "Rendered map artifact must not be empty.")
    if isinstance(max_pixels, bool) or not isinstance(max_pixels, int) or max_pixels <= 0:
        raise GISProcessingError("ERROR_GIS_RENDER_MAX_PIXELS_INVALID", "max_pixels must be a positive integer.")
    minimum_width = _validate_dimension("min_width", min_width)
    minimum_height = _validate_dimension("min_height", min_height)
    try:
        with Image.open(output) as image:
            image.verify()
        with Image.open(output) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                raise GISProcessingError("ERROR_GIS_RENDER_DIMENSIONS", "Rendered image dimensions must be positive.")
            pixel_count = width * height
            if pixel_count > max_pixels:
                raise GISProcessingError(
                    "ERROR_GIS_RENDER_PIXEL_LIMIT", "Rendered image exceeds the configured pixel limit."
                )
            if minimum_width is not None and width < minimum_width:
                raise GISProcessingError(
                    "ERROR_GIS_RENDER_WIDTH_TOO_SMALL", "Rendered image is narrower than the required minimum."
                )
            if minimum_height is not None and height < minimum_height:
                raise GISProcessingError(
                    "ERROR_GIS_RENDER_HEIGHT_TOO_SMALL", "Rendered image is shorter than the required minimum."
                )
            original_mode = image.mode
            rgba = image.convert("RGBA")
            pixels = list(rgba.get_flattened_data())
    except GISProcessingError:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise GISProcessingError(
            "ERROR_GIS_RENDER_DECODE", "Rendered map artifact could not be decoded as an image."
        ) from exc

    total_alpha = sum(1 for _, _, _, alpha in pixels if alpha > 0)
    transparent_pixels = pixel_count - total_alpha
    color_counts = Counter(pixels)
    unique_colors = len(color_counts)
    dominant_color_count = max(color_counts.values())
    non_dominant_pixels = pixel_count - dominant_color_count
    luminance_values = [
        round((0.2126 * red) + (0.7152 * green) + (0.0722 * blue), 6) for red, green, blue, alpha in pixels if alpha > 0
    ]
    luminance_range = max(luminance_values) - min(luminance_values) if luminance_values else 0.0
    content_present = total_alpha > 0 and unique_colors > 1 and non_dominant_pixels > 0
    warnings: list[str] = []
    if transparent_pixels:
        warnings.append("GIS_RENDER_TRANSPARENT_PIXELS_PRESENT")
    if not content_present:
        raise GISProcessingError(
            "ERROR_GIS_RENDER_BLANK", "Rendered map artifact contains no nonuniform visible content."
        )

    manifest_binding = None
    if manifest_path is not None:
        manifest_binding = _manifest_binding(output, manifest_path.expanduser().resolve())
    created_at = utc_now()
    output_hash = _sha256(output)
    report_id = stable_id("gisreport", "render_validation", output_hash, created_at)
    report = {
        "schema_version": GIS_RENDER_VALIDATION_SCHEMA_VERSION,
        "report_id": report_id,
        "status": GIS_RENDER_VALIDATION_WARN if warnings else GIS_RENDER_VALIDATION_OK,
        "operation": "validate_rendered_map",
        "created_at": created_at,
        "source": {
            "path": str(output),
            "sha256": output_hash,
            "bytes": size,
            "format": str(getattr(image, "format", "unknown") or "unknown"),
            "mode": original_mode,
            "width": width,
            "height": height,
        },
        "manifest": manifest_binding,
        "parameters": {
            "min_width": minimum_width,
            "min_height": minimum_height,
            "max_pixels": max_pixels,
            "adapter": "pillow_decode_and_pixel_distribution_v1",
        },
        "invariants": {
            "decode_success": True,
            "dimensions_positive": width > 0 and height > 0,
            "dimensions_meet_minimum": (minimum_width is None or width >= minimum_width)
            and (minimum_height is None or height >= minimum_height),
            "pixel_count": pixel_count,
            "alpha_accounting_closed": total_alpha + transparent_pixels == pixel_count,
            "visible_pixel_count": total_alpha,
            "transparent_pixel_count": transparent_pixels,
            "unique_color_count": unique_colors,
            "non_dominant_pixel_count": non_dominant_pixels,
            "nonblank_content_present": content_present,
            "luminance_range": luminance_range,
            "manifest_binding_closed": manifest_binding is None or manifest_binding["output_sha256"] == output_hash,
        },
        "warnings": warnings,
        "limitations": [
            "This receipt certifies image decoding and bounded pixel-content presence, not cartographic semantic correctness.",
            "Pixel-level validation does not prove layer completeness, projection correctness, label legibility, or visual accessibility.",
        ],
        "falsification_criteria": [
            "reject if output SHA-256 changes",
            "reject if the artifact cannot be decoded or dimensions exceed the configured pixel limit",
            "reject if all visible pixels are uniform or no visible pixels remain",
            "reject manifest binding when the manifest output hash or path differs",
        ],
    }
    report_path = cfg.export_dir / "gis_reports" / f"{report_id}.json"
    write_json(report_path, report)
    return {**report, "report_path": str(report_path), "report_sha256": _sha256(report_path)}
