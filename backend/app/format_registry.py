"""Canonical local-file ingestion format registry (TGT-012).

This module is the single declaration of formats accepted by the ordinary
file/folder ingestion surfaces. Parser implementations remain responsible for
their format-specific behavior, but MIME identity, parser family, handling
class, multimodal class, and expected coarse file signatures are declared here
and consumed by the API, folder ingestion, parser, multimodal metadata, and
signature guard.
"""
from __future__ import annotations

from typing import Any

FORMAT_REGISTRY_SCHEMA_VERSION = "mucaro-ingest-format-registry/1.0"

HANDLING_NOTES = {
    "text": "Decode as UTF-8 text with replacement for invalid byte sequences.",
    "structured_text": "Parse structure locally and emit normalized text plus parser metadata.",
    "document": "Extract document text locally while preserving document-specific metadata.",
    "archive": "Inspect archive safety before bounded text-member extraction; never execute members.",
    "email": "Extract bounded message text and supported text-like attachments without executing content.",
    "image_metadata": "Record local image metadata only; semantic image understanding requires a model adapter.",
    "audio_metadata": "Record local audio metadata only; semantic audio understanding requires a model adapter.",
    "video_metadata": "Record local video/container metadata only; semantic video understanding requires a model adapter.",
}

FORMAT_REGISTRY: dict[str, dict[str, Any]] = {
    ".txt": {"mime_type": "text/plain", "parser": "text_utf8", "handling": "text", "signature_kinds": None},
    ".md": {"mime_type": "text/markdown", "parser": "text_utf8", "handling": "text", "signature_kinds": None},
    ".csv": {"mime_type": "text/csv", "parser": "csv_dictreader", "handling": "structured_text", "signature_kinds": None},
    ".json": {"mime_type": "application/json", "parser": "json_parser", "handling": "structured_text", "signature_kinds": None},
    ".html": {"mime_type": "text/html", "parser": "html_text_v1", "handling": "structured_text", "signature_kinds": None},
    ".htm": {"mime_type": "text/html", "parser": "html_text_v1", "handling": "structured_text", "signature_kinds": None},
    ".docx": {
        "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "parser": "docx_text_v1",
        "handling": "document",
        "signature_kinds": ["zip"],
    },
    ".zip": {"mime_type": "application/zip", "parser": "zip_archive_v1", "handling": "archive", "signature_kinds": ["zip"]},
    ".eml": {"mime_type": "message/rfc822", "parser": "email_rfc822_v1", "handling": "email", "signature_kinds": None},
    ".msg": {
        "mime_type": "application/vnd.ms-outlook",
        "parser": "outlook_msg_text_probe_v1",
        "handling": "email",
        "signature_kinds": ["ole_compound"],
    },
    ".pdf": {"mime_type": "application/pdf", "parser": "pypdf", "handling": "document", "signature_kinds": ["pdf"]},
    ".png": {"mime_type": "image/png", "parser": "image_metadata_v1", "handling": "image_metadata", "signature_kinds": ["png"]},
    ".jpg": {"mime_type": "image/jpeg", "parser": "image_metadata_v1", "handling": "image_metadata", "signature_kinds": ["jpeg"]},
    ".jpeg": {"mime_type": "image/jpeg", "parser": "image_metadata_v1", "handling": "image_metadata", "signature_kinds": ["jpeg"]},
    ".gif": {"mime_type": "image/gif", "parser": "image_metadata_v1", "handling": "image_metadata", "signature_kinds": ["gif"]},
    ".webp": {"mime_type": "image/webp", "parser": "image_metadata_v1", "handling": "image_metadata", "signature_kinds": ["webp"]},
    ".wav": {"mime_type": "audio/wav", "parser": "audio_metadata_v1", "handling": "audio_metadata", "signature_kinds": ["wav"]},
    ".mp3": {"mime_type": "audio/mpeg", "parser": "audio_metadata_v1", "handling": "audio_metadata", "signature_kinds": ["mp3"]},
    ".m4a": {"mime_type": "audio/mp4", "parser": "audio_metadata_v1", "handling": "audio_metadata", "signature_kinds": ["iso_base_media"]},
    ".mp4": {"mime_type": "video/mp4", "parser": "video_metadata_v1", "handling": "video_metadata", "signature_kinds": ["iso_base_media"]},
    ".mov": {
        "mime_type": "video/quicktime",
        "parser": "video_metadata_v1",
        "handling": "video_metadata",
        "signature_kinds": ["iso_base_media", "quicktime"],
    },
    ".webm": {"mime_type": "video/webm", "parser": "video_metadata_v1", "handling": "video_metadata", "signature_kinds": ["ebml"]},
}

SUPPORTED_INGEST_SUFFIXES = frozenset(FORMAT_REGISTRY)
IMAGE_SUFFIXES = frozenset(suffix for suffix, row in FORMAT_REGISTRY.items() if row["handling"] == "image_metadata")
AUDIO_SUFFIXES = frozenset(suffix for suffix, row in FORMAT_REGISTRY.items() if row["handling"] == "audio_metadata")
VIDEO_SUFFIXES = frozenset(suffix for suffix, row in FORMAT_REGISTRY.items() if row["handling"] == "video_metadata")
MULTIMODAL_SUFFIXES = IMAGE_SUFFIXES | AUDIO_SUFFIXES | VIDEO_SUFFIXES
TEXT_SIGNATURE_SUFFIXES = frozenset(
    suffix for suffix, row in FORMAT_REGISTRY.items() if row["signature_kinds"] is None
)
EXPECTED_SIGNATURE_KINDS = {
    suffix: frozenset(row["signature_kinds"])
    for suffix, row in FORMAT_REGISTRY.items()
    if row["signature_kinds"] is not None
}


def format_record(suffix: str) -> dict[str, Any] | None:
    row = FORMAT_REGISTRY.get(suffix.lower())
    return dict(row) if row is not None else None


def detect_mime_for_suffix(suffix: str) -> str:
    row = FORMAT_REGISTRY.get(suffix.lower())
    return str(row["mime_type"]) if row is not None else "application/octet-stream"


def declared_format_registry() -> dict[str, Any]:
    rows = [
        {
            "suffix": suffix,
            "mime_type": row["mime_type"],
            "parser": row["parser"],
            "handling": row["handling"],
            "handling_note": HANDLING_NOTES[row["handling"]],
            "signature_kinds": list(row["signature_kinds"]) if row["signature_kinds"] is not None else [],
        }
        for suffix, row in sorted(FORMAT_REGISTRY.items())
    ]
    return {
        "schema_version": FORMAT_REGISTRY_SCHEMA_VERSION,
        "format_count": len(rows),
        "formats": rows,
    }
