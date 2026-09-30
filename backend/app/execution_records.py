from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import validate_contract
from .utils import sha256_file, stable_id, utc_now


def build_execution_contract(
    *,
    execution_id: str,
    skill_id: str,
    skill_version: str,
    skill_digest: str,
    runtime: str,
    status: str,
    started_at: str,
    finished_at: str | None = None,
    executed_by: str | None = None,
    input_artifacts: list[dict[str, Any]] | None = None,
    output_artifacts: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": "execution-contract/1.0",
        "execution_id": execution_id,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "skill_digest": skill_digest,
        "runtime": runtime,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "executed_by": executed_by,
        "input_artifacts": list(input_artifacts or []),
        "output_artifacts": list(output_artifacts or []),
        "error": error,
    }
    return validate_contract("execution_contract", payload)


def build_run_manifest(
    *,
    run_id: str,
    task_id: str,
    state: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    errors: list[str] | None = None,
    started_at: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "task_id": task_id,
        "state": state,
        "inputs": list(inputs or []),
        "outputs": list(outputs or []),
        "errors": list(errors or []),
        "started_at": started_at,
        "completed_at": completed_at,
    }
    return validate_contract("run_manifest", payload)


def build_artifact_identity(
    artifact_path: Path,
    *,
    artifact_type: str,
    logical_identity: str,
    producer: str,
    producer_version: str,
    parent_artifact_ids: list[str] | None = None,
    schema_id: str | None = None,
    source_path_or_uri: str | None = None,
    created_utc: str | None = None,
) -> dict[str, Any]:
    path = artifact_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    byte_size = path.stat().st_size
    created = created_utc or utc_now()
    payload = {
        "schema_version": "mucaro-artifact/1.0",
        "artifact_id": stable_id("artifact", logical_identity, digest, str(byte_size)),
        "artifact_type": artifact_type,
        "sha256": digest,
        "byte_size": byte_size,
        "logical_identity": logical_identity,
        "producer": producer,
        "producer_version": producer_version,
        "created_utc": created,
        "parent_artifact_ids": list(parent_artifact_ids or []),
        "schema_id": schema_id,
        "source_path_or_uri": source_path_or_uri or str(path),
    }
    return validate_contract("artifact_identity", payload)
