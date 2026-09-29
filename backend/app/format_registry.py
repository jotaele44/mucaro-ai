from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .gis_vector_formats import SUPPORTED_VECTOR_FORMATS
from .ingestion import FOLDER_SUPPORTED_SUFFIXES
from .multimodal import IMAGE_SUFFIXES, MULTIMODAL_SUFFIXES
from .parser import SUPPORTED_PARSER_SUFFIXES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FORMAT_REGISTRY = ROOT / "_architecture" / "format_registry_v1.json"
FORMAT_REGISTRY_SCHEMA = "MUCARO_FORMAT_REGISTRY/1.0"
FORMAT_STATES = {"SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED"}


class FormatRegistryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FormatRegistryError("ERROR_FORMAT_REGISTRY_READ", str(path)) from exc
    if not isinstance(payload, dict):
        raise FormatRegistryError("ERROR_FORMAT_REGISTRY_SCHEMA", "registry must contain a JSON object")
    return payload, raw


def _normalize_extension(value: str) -> str:
    extension = value.strip().lower()
    if not extension:
        raise FormatRegistryError("ERROR_FORMAT_EXTENSION", "extension must not be empty")
    if not extension.startswith("."):
        extension = "." + extension
    if "/" in extension or "\\" in extension or extension in {".", ".."}:
        raise FormatRegistryError("ERROR_FORMAT_EXTENSION", value)
    return extension


def validate_format_registry(path: str | Path = DEFAULT_FORMAT_REGISTRY) -> dict[str, Any]:
    registry_path = Path(path).expanduser().resolve()
    payload, raw = _load(registry_path)
    failures: list[str] = []
    if payload.get("schema_version") != FORMAT_REGISTRY_SCHEMA:
        failures.append("SCHEMA_VERSION_MISMATCH")
    if set(payload.get("states", [])) != FORMAT_STATES:
        failures.append("STATE_VOCABULARY_MISMATCH")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise FormatRegistryError("ERROR_FORMAT_REGISTRY_ENTRIES", "entries must be a non-empty array")

    ids: set[str] = set()
    manifestation_keys: set[tuple[str, str]] = set()
    core_declared: set[str] = set()
    image_declared: set[str] = set()
    vector_declared: set[str] = set()
    rows: list[dict[str, Any]] = []

    for index, row in enumerate(entries):
        if not isinstance(row, dict):
            failures.append(f"ENTRY_NOT_OBJECT:{index}")
            continue
        registry_id = row.get("registry_id")
        surface = row.get("surface")
        state = row.get("state")
        extensions = row.get("extensions")
        if not isinstance(registry_id, str) or not registry_id:
            failures.append(f"REGISTRY_ID_INVALID:{index}")
            continue
        if registry_id in ids:
            failures.append(f"REGISTRY_ID_DUPLICATE:{registry_id}")
        ids.add(registry_id)
        if not isinstance(surface, str) or not surface:
            failures.append(f"SURFACE_INVALID:{registry_id}")
            continue
        if state not in FORMAT_STATES:
            failures.append(f"STATE_INVALID:{registry_id}:{state}")
        if not isinstance(extensions, list) or not extensions:
            failures.append(f"EXTENSIONS_INVALID:{registry_id}")
            continue
        normalized_extensions: list[str] = []
        for raw_extension in extensions:
            if not isinstance(raw_extension, str):
                failures.append(f"EXTENSION_INVALID:{registry_id}")
                continue
            try:
                extension = _normalize_extension(raw_extension)
            except FormatRegistryError:
                failures.append(f"EXTENSION_INVALID:{registry_id}:{raw_extension}")
                continue
            if extension != raw_extension:
                failures.append(f"EXTENSION_NOT_CANONICAL:{registry_id}:{raw_extension}")
            key = (surface, extension)
            if key in manifestation_keys:
                failures.append(f"MANIFESTATION_DUPLICATE:{surface}:{extension}")
            manifestation_keys.add(key)
            normalized_extensions.append(extension)
            if surface == "core_ingestion" and state != "UNSUPPORTED":
                core_declared.add(extension)
            if surface == "ocr_image":
                image_declared.add(extension)
            if surface == "gis_vector":
                vector_declared.add(extension)
        rows.append(
            {
                "registry_id": registry_id,
                "surface": surface,
                "state": state,
                "extensions": normalized_extensions,
                "capabilities": row.get("capabilities", []),
                "limitations": row.get("limitations", []),
                "source_bindings": row.get("source_bindings", []),
            }
        )

    accepted_core = set(FOLDER_SUPPORTED_SUFFIXES)
    parser_core = set(SUPPORTED_PARSER_SUFFIXES)
    if core_declared != accepted_core:
        failures.append(
            "CORE_ALLOWLIST_MISMATCH:"
            + json.dumps(
                {
                    "registry_only": sorted(core_declared - accepted_core),
                    "code_only": sorted(accepted_core - core_declared),
                },
                sort_keys=True,
            )
        )
    if parser_core != accepted_core:
        failures.append(
            "PARSER_FOLDER_ALLOWLIST_MISMATCH:"
            + json.dumps(
                {
                    "parser_only": sorted(parser_core - accepted_core),
                    "folder_only": sorted(accepted_core - parser_core),
                },
                sort_keys=True,
            )
        )
    if image_declared != set(IMAGE_SUFFIXES):
        failures.append("OCR_IMAGE_ALLOWLIST_MISMATCH")
    if vector_declared != set(SUPPORTED_VECTOR_FORMATS):
        failures.append("GIS_VECTOR_ALLOWLIST_MISMATCH")

    core_multimodal_states = {
        extension: row["state"]
        for row in rows
        if row["surface"] == "core_ingestion"
        for extension in row["extensions"]
        if extension in MULTIMODAL_SUFFIXES
    }
    if set(core_multimodal_states) != set(MULTIMODAL_SUFFIXES):
        failures.append("CORE_MULTIMODAL_DENOMINATOR_MISMATCH")
    if any(state != "PARTIALLY_SUPPORTED" for state in core_multimodal_states.values()):
        failures.append("CORE_MULTIMODAL_OVERCLAIM")

    unknown = payload.get("unknown_extension")
    if not isinstance(unknown, dict):
        failures.append("UNKNOWN_EXTENSION_POLICY_MISSING")
    else:
        if unknown.get("surface") != "core_ingestion":
            failures.append("UNKNOWN_EXTENSION_SURFACE")
        if unknown.get("state") != "UNSUPPORTED":
            failures.append("UNKNOWN_EXTENSION_NOT_UNSUPPORTED")
        if unknown.get("behavior") != "ERROR_UNSUPPORTED_FORMAT":
            failures.append("UNKNOWN_EXTENSION_BEHAVIOR")

    status = "FORMAT_REGISTRY_OK" if not failures else "FORMAT_REGISTRY_BLOCKED"
    return {
        "schema_version": "mucaro-format-registry-validation/1.0",
        "status": status,
        "certification_ready": not failures,
        "registry_path": str(registry_path),
        "registry_sha256": hashlib.sha256(raw).hexdigest(),
        "counts": {
            "entries": len(entries),
            "manifestations": len(manifestation_keys),
            "core_declared": len(core_declared),
            "ocr_image_declared": len(image_declared),
            "gis_vector_declared": len(vector_declared),
            "failures": len(failures),
        },
        "source_denominators": {
            "folder_supported_suffixes": sorted(accepted_core),
            "parser_supported_suffixes": sorted(parser_core),
            "multimodal_suffixes": sorted(MULTIMODAL_SUFFIXES),
            "ocr_image_suffixes": sorted(IMAGE_SUFFIXES),
            "gis_vector_suffixes": sorted(SUPPORTED_VECTOR_FORMATS),
        },
        "entries": rows,
        "failures": failures,
        "invariants": {
            "registry_ids_unique": len(ids) == len(entries),
            "surface_extension_manifestations_unique": len(manifestation_keys)
            == sum(len(row["extensions"]) for row in rows),
            "core_allowlist_exact": core_declared == accepted_core == parser_core,
            "multimodal_partial_only": set(core_multimodal_states) == set(MULTIMODAL_SUFFIXES)
            and all(state == "PARTIALLY_SUPPORTED" for state in core_multimodal_states.values()),
            "ocr_image_allowlist_exact": image_declared == set(IMAGE_SUFFIXES),
            "gis_vector_allowlist_exact": vector_declared == set(SUPPORTED_VECTOR_FORMATS),
            "unknown_extension_fails_closed": isinstance(unknown, dict)
            and unknown.get("state") == "UNSUPPORTED"
            and unknown.get("behavior") == "ERROR_UNSUPPORTED_FORMAT",
        },
    }


def query_format_registry(
    extension: str,
    *,
    surface: str | None = None,
    path: str | Path = DEFAULT_FORMAT_REGISTRY,
) -> dict[str, Any]:
    canonical = _normalize_extension(extension)
    validation = validate_format_registry(path)
    if validation["status"] != "FORMAT_REGISTRY_OK":
        return {
            "status": "FORMAT_REGISTRY_BLOCKED",
            "extension": canonical,
            "surface": surface,
            "failures": validation["failures"],
            "candidates": [],
        }
    candidates = [
        row
        for row in validation["entries"]
        if canonical in row["extensions"] and (surface is None or row["surface"] == surface)
    ]
    if not candidates:
        return {
            "status": "FORMAT_UNSUPPORTED",
            "extension": canonical,
            "surface": surface,
            "state": "UNSUPPORTED",
            "candidates": [],
        }
    if surface is None and len(candidates) > 1:
        return {
            "status": "FORMAT_REGISTRY_AMBIGUOUS",
            "extension": canonical,
            "surface": None,
            "state": "UNRESOLVED",
            "candidates": candidates,
            "reason": "Same extension has multiple declared capability manifestations; specify --surface.",
        }
    if len(candidates) != 1:
        return {
            "status": "FORMAT_REGISTRY_AMBIGUOUS",
            "extension": canonical,
            "surface": surface,
            "state": "UNRESOLVED",
            "candidates": candidates,
        }
    return {
        "status": "FORMAT_REGISTRY_MATCH",
        "extension": canonical,
        "surface": candidates[0]["surface"],
        "state": candidates[0]["state"],
        "candidate": candidates[0],
        "candidates": candidates,
    }
