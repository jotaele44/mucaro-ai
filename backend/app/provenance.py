"""Provenance records for source manifestations (INV-018, TGT-015)."""

from __future__ import annotations

import importlib.metadata
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import validate_contract

PROVENANCE_SCHEMA_VERSION = "mucaro-provenance/1.0"


@lru_cache(maxsize=1)
def app_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        try:
            return str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
        except (KeyError, tomllib.TOMLDecodeError):
            pass
    try:
        return importlib.metadata.version("mucaro-ai")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def build_provenance_record(
    *,
    source_kind: str,
    source_locator: str,
    retrieval_utc: str,
    raw_byte_sha256: str,
    raw_byte_count: int,
    processor: str,
    identity_basis: list[str] | None = None,
    trust: str = "UNTRUSTED_CONTENT",
    service_or_repository: str | None = None,
    layer_or_query: str | None = None,
    source_refresh_date: str | None = None,
    page_or_offset: str | None = None,
    schema_identity: str | None = None,
    row_or_feature_count: int | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a provenance record.

    Ingested content defaults to UNTRUSTED_CONTENT: it is evidence, never instructions (INV-014).
    """
    processing: dict[str, Any] = {"processor": processor, "processor_version": app_version()}
    if parameters:
        processing["parameters"] = parameters
    record = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_kind": source_kind,
        "source_locator": source_locator,
        "service_or_repository": service_or_repository,
        "layer_or_query": layer_or_query,
        "retrieval_utc": retrieval_utc,
        "source_refresh_date": source_refresh_date,
        "page_or_offset": page_or_offset,
        "raw_byte_sha256": raw_byte_sha256,
        "raw_byte_count": raw_byte_count,
        "schema_identity": schema_identity,
        "row_or_feature_count": row_or_feature_count,
        "processing": processing,
        "identity_basis": identity_basis or ["BYTE_IDENTITY"],
        "trust": trust,
    }
    return validate_contract("provenance_record", record)
