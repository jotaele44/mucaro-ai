from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.format_registry import (
    DEFAULT_FORMAT_REGISTRY,
    query_format_registry,
    validate_format_registry,
)
from backend.app.ingestion import FOLDER_SUPPORTED_SUFFIXES
from backend.app.multimodal import IMAGE_SUFFIXES, MULTIMODAL_SUFFIXES
from backend.app.parser import ParseError, SUPPORTED_PARSER_SUFFIXES, parse_file


def test_format_registry_closes_source_allowlists_exactly() -> None:
    report = validate_format_registry()
    assert report["status"] == "FORMAT_REGISTRY_OK"
    assert report["certification_ready"] is True
    assert all(report["invariants"].values())
    assert set(report["source_denominators"]["folder_supported_suffixes"]) == set(FOLDER_SUPPORTED_SUFFIXES)
    assert set(report["source_denominators"]["parser_supported_suffixes"]) == set(SUPPORTED_PARSER_SUFFIXES)
    assert set(report["source_denominators"]["multimodal_suffixes"]) == set(MULTIMODAL_SUFFIXES)
    assert set(report["source_denominators"]["ocr_image_suffixes"]) == set(IMAGE_SUFFIXES)


def test_same_suffix_different_surface_requires_disambiguation() -> None:
    ambiguous = query_format_registry(".json")
    assert ambiguous["status"] == "FORMAT_REGISTRY_AMBIGUOUS"
    assert {row["surface"] for row in ambiguous["candidates"]} == {"core_ingestion", "gis_vector"}

    core = query_format_registry("json", surface="core_ingestion")
    gis = query_format_registry(".json", surface="gis_vector")
    assert core["status"] == "FORMAT_REGISTRY_MATCH"
    assert core["state"] == "SUPPORTED"
    assert gis["status"] == "FORMAT_REGISTRY_MATCH"
    assert gis["state"] == "SUPPORTED"


def test_multimodal_core_ingestion_is_partial_not_semantic_overclaim() -> None:
    report = validate_format_registry()
    by_extension = {}
    for row in report["entries"]:
        if row["surface"] != "core_ingestion":
            continue
        for extension in row["extensions"]:
            by_extension[extension] = row
    for extension in MULTIMODAL_SUFFIXES:
        assert by_extension[extension]["state"] == "PARTIALLY_SUPPORTED"
        assert "metadata_inspect" in by_extension[extension]["capabilities"]


def test_unsupported_and_unknown_formats_fail_closed_even_when_bytes_are_text(tmp_path: Path) -> None:
    for suffix in (".xlsx", ".pptx", ".abc"):
        path = tmp_path / f"probe{suffix}"
        path.write_text("this is readable UTF-8 but the suffix is not declared", encoding="utf-8")
        with pytest.raises(ParseError) as exc:
            parse_file(path)
        assert exc.value.status == "ERROR_UNSUPPORTED_FORMAT"


def test_registry_duplicate_manifestation_is_blocked(tmp_path: Path) -> None:
    payload = json.loads(DEFAULT_FORMAT_REGISTRY.read_text(encoding="utf-8"))
    duplicate = dict(payload["entries"][0])
    duplicate["registry_id"] = "duplicate-core-text"
    payload["entries"].append(duplicate)
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = validate_format_registry(path)
    assert report["status"] == "FORMAT_REGISTRY_BLOCKED"
    assert any(item.startswith("MANIFESTATION_DUPLICATE:core_ingestion:") for item in report["failures"])


def test_unknown_extension_query_is_explicitly_unsupported() -> None:
    result = query_format_registry(".abc", surface="core_ingestion")
    assert result == {
        "status": "FORMAT_UNSUPPORTED",
        "extension": ".abc",
        "surface": "core_ingestion",
        "state": "UNSUPPORTED",
        "candidates": [],
    }
