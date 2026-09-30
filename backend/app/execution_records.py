from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings, settings
from .contracts import validate_contract
from .utils import sha256_file, stable_id, utc_now, write_json

RECORD_DIR_NAME = "contracts"


def _record_dir(cfg: Settings) -> Path:
    cfg.ensure_dirs()
    path = cfg.processed_dir / RECORD_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _persist(kind: str, record_id: str, payload: dict[str, Any], cfg: Settings) -> Path:
    validated = validate_contract(kind, payload)
    path = _record_dir(cfg) / f"{kind}.{record_id}.json"
    write_json(path, validated)
    return path


def write_execution_contract(
    *,
    skill_id: str,
    skill_version: str,
    skill_digest: str,
    runtime: str,
    status: str = "OPEN",
    started_at: str | None = None,
    finished_at: str | None = None,
    executed_by: str | None = None,
    input_artifacts: list[dict[str, Any]] | None = None,
    output_artifacts: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    started = started_at or utc_now()
    execution_id = stable_id("execution", skill_id, skill_version, skill_digest, runtime, started)
    payload = {
        "schema": "execution-contract/1.0",
        "execution_id": execution_id,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "skill_digest": skill_digest,
        "runtime": runtime,
        "status": status,
        "started_at": started,
        "finished_at": finished_at,
        "executed_by": executed_by,
        "input_artifacts": list(input_artifacts or []),
        "output_artifacts": list(output_artifacts or []),
        "error": error,
    }
    path = _persist("execution_contract", execution_id, payload, cfg)
    return {"status": "EXECUTION_CONTRACT_WRITTEN", "execution_contract": payload, "path": str(path)}


def write_run_manifest(
    *,
    task_id: str,
    state: str,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    errors: list[str] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    run_id: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    started = started_at or utc_now()
    resolved_run_id = run_id or stable_id("run", task_id, started)
    payload = {
        "run_id": resolved_run_id,
        "task_id": task_id,
        "state": state,
        "inputs": list(inputs or []),
        "outputs": list(outputs or []),
        "errors": list(errors or []),
        "started_at": started,
        "completed_at": completed_at,
    }
    path = _persist("run_manifest", resolved_run_id, payload, cfg)
    return {"status": "RUN_MANIFEST_WRITTEN", "run_manifest": payload, "path": str(path)}


def write_agent_checkpoint(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    state: str,
    source_sha: str,
    sequence: int,
    completed_steps: list[dict[str, Any]] | None = None,
    pending_steps: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    pause_reason: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    checkpoint_id: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    created = created_at or utc_now()
    updated = updated_at or created
    resolved_id = checkpoint_id or stable_id("checkpoint", session_id, run_id, str(sequence), source_sha)
    payload = {
        "schema_version": "mucaro-agent-checkpoint/1.0",
        "checkpoint_id": resolved_id,
        "session_id": session_id,
        "run_id": run_id,
        "objective": objective,
        "state": state,
        "created_at": created,
        "updated_at": updated,
        "source_sha": source_sha,
        "sequence": sequence,
        "completed_steps": list(completed_steps or []),
        "pending_steps": list(pending_steps or []),
        "contradictions": list(contradictions or []),
        "pause_reason": pause_reason,
        "provider": provider,
        "model": model,
        "metadata": dict(metadata or {}),
    }
    path = _persist("agent_checkpoint", resolved_id, payload, cfg)
    return {"status": "AGENT_CHECKPOINT_WRITTEN", "agent_checkpoint": payload, "path": str(path)}


def write_artifact_identity(
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
    cfg: Settings = settings,
) -> dict[str, Any]:
    path = artifact_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    byte_size = path.stat().st_size
    created = created_utc or utc_now()
    artifact_id = stable_id("artifact", logical_identity, digest, str(byte_size))
    payload = {
        "schema_version": "mucaro-artifact/1.0",
        "artifact_id": artifact_id,
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
    record_path = _persist("artifact_identity", artifact_id, payload, cfg)
    return {"status": "ARTIFACT_IDENTITY_WRITTEN", "artifact_identity": payload, "path": str(record_path)}
