from __future__ import annotations

import json
from pathlib import Path

from backend.app.file_signatures import _KIND_BY_SUFFIX, TEXT_SUFFIXES
from backend.app.format_registry import (
    AUDIO_SUFFIXES,
    EXPECTED_SIGNATURE_KINDS,
    FORMAT_REGISTRY,
    IMAGE_SUFFIXES,
    MULTIMODAL_SUFFIXES,
    SUPPORTED_INGEST_SUFFIXES,
    TEXT_SIGNATURE_SUFFIXES,
    VIDEO_SUFFIXES,
    declared_format_registry,
    detect_mime_for_suffix,
)
from backend.app.ingestion import FOLDER_SUPPORTED_SUFFIXES
from backend.app.main import SUPPORTED_UPLOAD_SUFFIXES
from backend.app.multimodal import (
    AUDIO_SUFFIXES as MULTIMODAL_AUDIO_SUFFIXES,
    IMAGE_SUFFIXES as MULTIMODAL_IMAGE_SUFFIXES,
    MULTIMODAL_SUFFIXES as MULTIMODAL_ALL_SUFFIXES,
    VIDEO_SUFFIXES as MULTIMODAL_VIDEO_SUFFIXES,
)
from backend.app.parser import detect_mime

ROOT = Path(__file__).resolve().parents[2]
DECLARATION = ROOT / "_architecture" / "ingest_format_registry_v1.json"


def test_declared_format_registry_projection_is_exact() -> None:
    frozen = json.loads(DECLARATION.read_text(encoding="utf-8"))
    assert frozen == declared_format_registry()
    assert frozen["schema_version"] == "mucaro-ingest-format-registry/1.0"
    assert frozen["format_count"] == 22
    assert len(frozen["formats"]) == len({row["suffix"] for row in frozen["formats"]})


def test_all_ingestion_surfaces_use_the_same_supported_suffix_denominator() -> None:
    assert set(FORMAT_REGISTRY) == set(SUPPORTED_INGEST_SUFFIXES)
    assert set(FOLDER_SUPPORTED_SUFFIXES) == set(SUPPORTED_INGEST_SUFFIXES)
    assert set(SUPPORTED_UPLOAD_SUFFIXES) == set(SUPPORTED_INGEST_SUFFIXES)


def test_mime_detection_is_registry_driven_for_every_declared_format(tmp_path: Path) -> None:
    for suffix, row in FORMAT_REGISTRY.items():
        path = tmp_path / f"sample{suffix}"
        assert detect_mime_for_suffix(suffix) == row["mime_type"]
        assert detect_mime(path) == row["mime_type"]
    assert detect_mime_for_suffix(".unknown") == "application/octet-stream"


def test_multimodal_suffix_classes_are_registry_driven() -> None:
    assert IMAGE_SUFFIXES == MULTIMODAL_IMAGE_SUFFIXES
    assert AUDIO_SUFFIXES == MULTIMODAL_AUDIO_SUFFIXES
    assert VIDEO_SUFFIXES == MULTIMODAL_VIDEO_SUFFIXES
    assert MULTIMODAL_SUFFIXES == MULTIMODAL_ALL_SUFFIXES
    assert MULTIMODAL_SUFFIXES == IMAGE_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES
    assert not (IMAGE_SUFFIXES & AUDIO_SUFFIXES)
    assert not (IMAGE_SUFFIXES & VIDEO_SUFFIXES)
    assert not (AUDIO_SUFFIXES & VIDEO_SUFFIXES)


def test_signature_guard_expectations_are_registry_driven() -> None:
    assert _KIND_BY_SUFFIX == EXPECTED_SIGNATURE_KINDS
    assert TEXT_SUFFIXES == TEXT_SIGNATURE_SUFFIXES
    assert set(_KIND_BY_SUFFIX) | set(TEXT_SUFFIXES) == set(SUPPORTED_INGEST_SUFFIXES)


def test_every_registry_row_has_actionable_handling_and_parser_identity() -> None:
    allowed_handling = {
        "text",
        "structured_text",
        "document",
        "archive",
        "email",
        "image_metadata",
        "audio_metadata",
        "video_metadata",
    }
    for suffix, row in FORMAT_REGISTRY.items():
        assert suffix.startswith(".") and suffix == suffix.lower()
        assert row["mime_type"] and "/" in row["mime_type"]
        assert row["parser"]
        assert row["handling"] in allowed_handling
        projection = next(item for item in declared_format_registry()["formats"] if item["suffix"] == suffix)
        assert projection["handling_note"]
