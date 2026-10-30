from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from functools import wraps
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import ContractViolation, validate_contract
from .utils import file_lock, stable_id, utc_now, write_json

ARTIFACT_SCHEMA_VERSION = "mucaro-artifact/1.0"
ARTIFACT_PROVENANCE_SCHEMA_VERSION = "mucaro-artifact-provenance/1.0"
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
    provenance: dict[str, Any] | None = None

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
            "provenance": self.provenance,
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


def _validate_record(record: dict[str, Any]) -> None:
    try:
        validate_contract("artifact_identity", record)
    except ContractViolation as exc:
        raise ArtifactIdentityError(f"artifact contract violation: {'; '.join(exc.errors)}") from exc


def _load_records(processed_dir: Path) -> list[dict[str, Any]]:
    path = artifact_store_path(processed_dir)
    try:
        if path.is_symlink():
            raise ArtifactIdentityError("artifact lineage store cannot be a symbolic link")
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ArtifactIdentityError("artifact lineage store is unavailable") from exc
    try:
        records = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_constant,
            parse_float=_finite_json_float,
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ArtifactIdentityError("artifact lineage store is not unambiguous UTF-8 JSON") from exc
    if not isinstance(records, list):
        raise ArtifactIdentityError("artifact lineage store must contain a list")
    for record in records:
        if not isinstance(record, dict):
            raise ArtifactIdentityError("artifact lineage store contains a non-object record")
        _validate_record(record)
    index = _index_records(records)
    roots = _lineage_roots(index)
    for record in records:
        provenance = record.get("provenance")
        if not isinstance(provenance, dict):
            continue
        if provenance.get("artifact_sha256") != record.get("sha256"):
            raise ArtifactIdentityError(f"artifact provenance digest mismatch: {record.get('artifact_id')}")
        expected_parents = [
            {
                "artifact_id": parent_id,
                "artifact_type": str(index[parent_id].get("artifact_type")),
                "sha256": str(index[parent_id].get("sha256")),
            }
            for parent_id in record.get("parent_artifact_ids", [])
            if parent_id in index
        ]
        if provenance.get("parent_artifacts") != expected_parents:
            raise ArtifactIdentityError(f"artifact provenance parent mismatch: {record.get('artifact_id')}")
        artifact_id = str(record.get("artifact_id"))
        if provenance.get("source_artifact_ids") != roots[artifact_id]:
            raise ArtifactIdentityError(f"artifact provenance source-root mismatch: {artifact_id}")
        if provenance.get("lineage_kind") != (
            "SOURCE_MANIFESTATION" if record["artifact_type"] == "SOURCE" else "DERIVATION"
        ):
            raise ArtifactIdentityError(f"artifact provenance kind mismatch: {artifact_id}")
        expected_transformation = {key: record[key] for key in ("logical_identity", "producer", "producer_version")}
        if (
            provenance.get("transformation") != expected_transformation
            or provenance.get("created_utc") != record["created_utc"]
        ):
            raise ArtifactIdentityError(f"artifact provenance transformation mismatch: {artifact_id}")
    return records


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _finite_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("JSON number overflows finite storage")
    return value


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


def _lineage_roots(index: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Validate the entire DAG, including legacy rows, without recursive walks."""
    graph: dict[str, list[str]] = {}
    for artifact_id, record in index.items():
        parents = record["parent_artifact_ids"]
        kind = record["artifact_type"]
        if (kind == "SOURCE" and parents) or (kind != "SOURCE" and not parents):
            raise ArtifactIdentityError(f"invalid lineage parent count: {artifact_id}")
        for parent_id in parents:
            if parent_id not in index:
                raise ArtifactIdentityError(f"broken lineage parent: {parent_id}")
            if index[parent_id]["artifact_type"] not in ALLOWED_PARENT_TYPES[kind]:
                raise ArtifactIdentityError(f"invalid lineage parent type: {artifact_id}")
        graph[artifact_id] = parents
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ArtifactIdentityError("artifact lineage contains a cycle") from exc
    roots: dict[str, list[str]] = {}
    for artifact_id in order:
        roots[artifact_id] = (
            [artifact_id]
            if index[artifact_id]["artifact_type"] == "SOURCE"
            else list(dict.fromkeys(root for parent_id in graph[artifact_id] for root in roots[parent_id]))
        )
    return roots


def _build_provenance(
    *,
    index: dict[str, dict[str, Any]],
    artifact_id: str,
    artifact_type: str,
    digest: str,
    logical_identity: str,
    producer: str,
    producer_version: str,
    created_utc: str,
    parent_artifact_ids: tuple[str, ...],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parent_artifacts = [
        {
            "artifact_id": parent_id,
            "artifact_type": str(index[parent_id].get("artifact_type")),
            "sha256": str(index[parent_id].get("sha256")),
        }
        for parent_id in parent_artifact_ids
    ]
    source_roots = _lineage_roots(index)
    roots = (
        [artifact_id]
        if artifact_type == "SOURCE"
        else [root for parent_id in parent_artifact_ids for root in source_roots[parent_id]]
    )
    provenance: dict[str, Any] = {
        "schema_version": ARTIFACT_PROVENANCE_SCHEMA_VERSION,
        "lineage_kind": "SOURCE_MANIFESTATION" if artifact_type == "SOURCE" else "DERIVATION",
        "source_artifact_ids": list(dict.fromkeys(roots)),
        "parent_artifacts": parent_artifacts,
        "transformation": {
            "logical_identity": logical_identity,
            "producer": producer,
            "producer_version": producer_version,
        },
        "artifact_sha256": digest,
        "created_utc": created_utc,
    }
    if parameters:
        provenance["parameters"] = parameters
    return provenance


def _serialize_artifact_creation(operation: Callable[..., ArtifactRecord]) -> Callable[..., ArtifactRecord]:
    @wraps(operation)
    def locked(*args: Any, **kwargs: Any) -> ArtifactRecord:
        with file_lock(artifact_store_path(kwargs["processed_dir"])):
            return operation(*args, **kwargs)

    return locked


@_serialize_artifact_creation
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
    provenance_parameters: dict[str, Any] | None = None,
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
        raise ArtifactIdentityError(f"invalid parent types for {artifact_type}: {','.join(invalid_parent_types)}")

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
    candidate_created_utc = str(existing.get("created_utc") or created_utc) if existing is not None else created_utc
    candidate_provenance = _build_provenance(
        index=index,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        digest=digest,
        logical_identity=logical_identity,
        producer=producer,
        producer_version=producer_version,
        created_utc=candidate_created_utc,
        parent_artifact_ids=parents,
        parameters=provenance_parameters,
    )
    if existing is not None:
        if "provenance" not in existing:
            existing["provenance"] = candidate_provenance
            write_json(artifact_store_path(processed_dir), records)
        canonical_existing = {key: value for key, value in existing.items() if key != "created_utc"}
        candidate = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            sha256=digest,
            byte_size=len(payload),
            logical_identity=logical_identity,
            producer=producer,
            producer_version=producer_version,
            created_utc=candidate_created_utc,
            parent_artifact_ids=parents,
            schema_id=schema_id,
            source_path_or_uri=source_path_or_uri,
            provenance=existing.get("provenance") or candidate_provenance,
        )
        canonical_candidate = {
            key: value for key, value in candidate.as_dict().items() if key not in {"created_utc", "schema_version"}
        }
        canonical_existing.pop("schema_version", None)
        if canonical_existing != canonical_candidate:
            raise ArtifactIdentityError(f"artifact identity collision: {artifact_id}")
        _validate_record(candidate.as_dict())
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
        provenance=candidate_provenance,
    )
    _validate_record(record.as_dict())
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

    stack = [(artifact_id, False)]
    while stack:
        current_id, expanded = stack.pop()
        if current_id in visited:
            continue
        if expanded:
            visited.add(current_id)
            if current_id != artifact_id:
                ordered.append(index[current_id])
        else:
            stack.append((current_id, True))
            stack.extend((parent_id, False) for parent_id in reversed(index[current_id]["parent_artifact_ids"]))
    return ordered
