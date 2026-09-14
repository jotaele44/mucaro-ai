from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.artifact_identity import (
    ArtifactIdentityError,
    create_artifact_record,
    list_artifacts,
    sha256_bytes,
    trace_ancestors,
)


def test_source_to_export_lineage_closes_with_stable_byte_identity(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    source = create_artifact_record(
        processed_dir=processed,
        artifact_type="SOURCE",
        payload=b"raw-source",
        logical_identity="source:test",
        producer="test",
        producer_version="1",
        source_path_or_uri="file:///tmp/source.txt",
    )
    parsed = create_artifact_record(
        processed_dir=processed,
        artifact_type="PARSED",
        payload=b"parsed",
        logical_identity="parsed:test",
        producer="parser",
        producer_version="1",
        parent_artifact_ids=[source.artifact_id],
    )
    intermediate = create_artifact_record(
        processed_dir=processed,
        artifact_type="INTERMEDIATE",
        payload=b"intermediate",
        logical_identity="intermediate:test",
        producer="worker",
        producer_version="1",
        parent_artifact_ids=[parsed.artifact_id],
    )
    derived = create_artifact_record(
        processed_dir=processed,
        artifact_type="DERIVED",
        payload=b"derived",
        logical_identity="derived:test",
        producer="analysis",
        producer_version="1",
        parent_artifact_ids=[intermediate.artifact_id],
    )
    export = create_artifact_record(
        processed_dir=processed,
        artifact_type="EXPORT",
        payload=b"export",
        logical_identity="export:test",
        producer="exporter",
        producer_version="1",
        parent_artifact_ids=[derived.artifact_id],
    )

    assert source.sha256 == sha256_bytes(b"raw-source")
    assert export.byte_size == len(b"export")
    ancestors = trace_ancestors(processed, export.artifact_id)
    assert [item["artifact_id"] for item in ancestors] == [
        source.artifact_id,
        parsed.artifact_id,
        intermediate.artifact_id,
        derived.artifact_id,
    ]
    assert len(list_artifacts(processed)) == 5


def test_identical_artifact_registration_is_idempotent(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    first = create_artifact_record(
        processed_dir=processed,
        artifact_type="SOURCE",
        payload=b"same",
        logical_identity="source:same",
        producer="test",
        producer_version="1",
    )
    second = create_artifact_record(
        processed_dir=processed,
        artifact_type="SOURCE",
        payload=b"same",
        logical_identity="source:same",
        producer="test",
        producer_version="1",
    )
    assert first.artifact_id == second.artifact_id
    assert len(list_artifacts(processed)) == 1


def test_non_source_artifact_requires_parent(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIdentityError, match="require at least one parent"):
        create_artifact_record(
            processed_dir=tmp_path / "processed",
            artifact_type="DERIVED",
            payload=b"x",
            logical_identity="derived:x",
            producer="test",
            producer_version="1",
        )


def test_unknown_parent_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIdentityError, match="unknown parent"):
        create_artifact_record(
            processed_dir=tmp_path / "processed",
            artifact_type="PARSED",
            payload=b"x",
            logical_identity="parsed:x",
            producer="test",
            producer_version="1",
            parent_artifact_ids=["artifact-missing"],
        )


def test_invalid_lineage_transition_fails_closed(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    source = create_artifact_record(
        processed_dir=processed,
        artifact_type="SOURCE",
        payload=b"source",
        logical_identity="source:x",
        producer="test",
        producer_version="1",
    )
    with pytest.raises(ArtifactIdentityError, match="invalid parent types"):
        create_artifact_record(
            processed_dir=processed,
            artifact_type="EXPORT",
            payload=b"export",
            logical_identity="export:x",
            producer="test",
            producer_version="1",
            parent_artifact_ids=[source.artifact_id],
        )
