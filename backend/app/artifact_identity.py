from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .utils import read_json, stable_id, utc_now, write_json

ARTIFACT_SCHEMA_VERSION = "mucaro-artifact/1.0"
ARTIFACT_STORE_NAME = "artifact_lineage.json"
ARTIFACT_TYPES = {"SOURCE", "PARSED", "INTERMEDIATE", "DERIVED", "EXPORT", "RECEIPT"}
ALLOWED_PARENT_TYPES = {
    "SOURCE": set(),
    "PARSED": {"SOURCE"},
    "INTERMEDIATE": {"SOURCE", "PARSED", "INTERMEDIATE"},
    "DERIVED": {"SOURCE", "PARSED", "INTERMEDIATE", "DERIVED"},
    "EXPORT": {"PARSED", "INTERMEDIATE", "DERIVED"},
    "RECEIPT": {"SOURCE", "PARSED", "INTERMEDIATE", "DERIVED", "EXPORT", "RECEIPT"},
}


class ArtifactIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    sha256: str
    byte_size: int
    logical_identity: str
    producer: str
    producer_version: str
    created_utc: str
    parent_artifact_ids: tuple[str, ...]
    schema_id: str | None = None
    source_path_or_uri: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "logical_identity": self.logical_identity,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "created_utc": self.created_utc,
            "parent_artifact_ids": list(self.parent_artifact_ids),
            "schema_id": self.schema_id,
            "source_path_or_uri": self.source_path_or_uri,
        }


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_store_path(processed_dir: Path) -> Path:
    return processed_dir / ARTIFACT_STORE_NAME


def _load_records(processed_dir: Path) -> list[dict[str, Any]]:
    records = read_json(artifact_store_path(processed_dir), [])
    if not isinstance(records, list):
        raise ArtifactIdentityError("artifact lineage store must contain a list")
    return records


def list_artifacts(processed_dir: Path) -> list[dict[str, Any]]:
    return _load_records(processed_dir)


def _index_records(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in records:
        artifact_id = record.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ArtifactIdentityError("artifact store contains a record without artifact_id")
        if artifact_id in index:
            raise ArtifactIdentityError(f"duplicate artifact_id in store: {artifact_id}")
        index[artifact_id] = record
    return index


def create_artifact_record(
    *,
    processed_dir: Path,
    artifact_type: str,
    payload: bytes,
    logical_identity: str,
    producer: str,
    producer_version: str,
    parent_artifact_ids: Iterable[str] = (),
    schema_id: str | None = None,
    source_path_or_uri: str | None = None,
) -> ArtifactRecord:
    if artifact_type not in ARTIFACT_TYPES:
        raise ArtifactIdentityError(f"unsupported artifact_type: {artifact_type}")
    if not logical_identity:
        raise ArtifactIdentityError("logical_identity is required")
    if not producer or not producer_version:
        raise ArtifactIdentityError("producer and producer_version are required")

    records = _load_records(processed_dir)
    index = _index_records(records)
    parents = tuple(dict.fromkeys(parent_artifact_ids))
    if artifact_type == "SOURCE" and parents:
        raise ArtifactIdentityError("SOURCE artifacts cannot have parents")
    if artifact_type != "SOURCE" and not parents:
        raise ArtifactIdentityError(f"{artifact_type} artifacts require at least one parent")

    parent_types: list[str] = []
    for parent_id in parents:
        parent = index.get(parent_id)
        if parent is None:
            raise ArtifactIdentityError(f"unknown parent artifact_id: {parent_id}")
        parent_type = parent.get("artifact_type")
        if parent_type not in ARTIFACT_TYPES:
            raise ArtifactIdentityError(f"parent {parent_id} has invalid artifact_type: {parent_type}")
        parent_types.append(str(parent_type))
    allowed_parent_types = ALLOWED_PARENT_TYPES[artifact_type]
    invalid_parent_types = sorted(set(parent_types) - allowed_parent_types)
    if invalid_parent_types:
        raise ArtifactIdentityError(
            f"invalid parent types for {artifact_type}: {','.join(invalid_parent_types)}"
        )

    created_utc = utc_now()
    digest = sha256_bytes(payload)
    artifact_id = stable_id(
        "artifact",
        artifact_type,
        digest,
        logical_identity,
        producer,
        producer_version,
        json.dumps(sorted(parents)),
    )
    existing = index.get(artifact_id)
    if existing is not None:
        canonical_existing = {key: value for key, value in existing.items() if key != "created_utc"}
        candidate = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            sha256=digest,
            byte_size=len(payload),
            logical_identity=logical_identity,
            producer=producer,
            producer_version=producer_version,
            created_utc=str(existing.get("created_utc") or created_utc),
            parent_artifact_ids=parents,
            schema_id=schema_id,
            source_path_or_uri=source_path_or_uri,
        )
        canonical_candidate = {key: value for key, value in candidate.as_dict().items() if key not in {"created_utc", "schema_version"}}
        canonical_existing.pop("schema_version", None)
        if canonical_existing != canonical_candidate:
            raise ArtifactIdentityError(f"artifact identity collision: {artifact_id}")
        return candidate

    record = ArtifactRecord(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        sha256=digest,
        byte_size=len(payload),
        logical_identity=logical_identity,
        producer=producer,
        producer_version=producer_version,
        created_utc=created_utc,
        parent_artifact_ids=parents,
        schema_id=schema_id,
        source_path_or_uri=source_path_or_uri,
    )
    records.append(record.as_dict())
    write_json(artifact_store_path(processed_dir), records)
    return record


def trace_ancestors(processed_dir: Path, artifact_id: str) -> list[dict[str, Any]]:
    records = _load_records(processed_dir)
    index = _index_records(records)
    if artifact_id not in index:
        raise ArtifactIdentityError(f"unknown artifact_id: {artifact_id}")

    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()

    def visit(current_id: str) -> None:
        current = index[current_id]
        for parent_id in current.get("parent_artifact_ids", []):
            if parent_id in visited:
                continue
            if parent_id not in index:
                raise ArtifactIdentityError(f"broken lineage parent: {parent_id}")
            visit(parent_id)
            visited.add(parent_id)
            ordered.append(index[parent_id])

    visit(artifact_id)
    return ordered
