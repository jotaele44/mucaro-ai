from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .db import init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

CHECKPOINT_SCHEMA_VERSION = "mucaro-agent-checkpoint/1.0"
CHECKPOINT_STATES = {
    "PLANNING",
    "READY_FOR_APPROVAL",
    "EXECUTING",
    "PAUSED_PROVIDER",
    "PAUSED_RESOURCE",
    "BLOCKED_PERMISSION",
    "BLOCKED_EXTERNAL",
    "FAILED",
    "COMPLETED",
}
TERMINAL_STATES = {"FAILED", "COMPLETED"}


class CheckpointError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def create_checkpoint(
    *,
    session_id: str,
    run_id: str,
    objective: str,
    source_sha: str,
    state: str = "PLANNING",
    completed_steps: list[dict[str, Any]] | None = None,
    pending_steps: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if state not in CHECKPOINT_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_STATE", f"unsupported checkpoint state: {state}")
    if not session_id or not run_id or not objective.strip():
        raise CheckpointError("ERROR_CHECKPOINT_IDENTITY", "session_id, run_id, and objective are required")
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        raise CheckpointError("ERROR_CHECKPOINT_SOURCE_SHA", "source_sha must be a lowercase 40-character Git SHA")
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    now = utc_now()
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": stable_id("checkpoint", session_id, run_id, source_sha, now, length=16),
        "session_id": session_id,
        "run_id": run_id,
        "objective": objective.strip(),
        "state": state,
        "created_at": now,
        "updated_at": now,
        "source_sha": source_sha,
        "sequence": 0,
        "completed_steps": list(completed_steps or []),
        "pending_steps": list(pending_steps or []),
        "contradictions": list(contradictions or []),
        "pause_reason": None,
        "provider": provider,
        "model": model,
        "metadata": dict(metadata or {}),
    }
    upsert_payload("agent_checkpoints", "checkpoint_id", checkpoint, cfg.sqlite_path)
    return checkpoint


def get_checkpoint(checkpoint_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    for checkpoint in list_payloads("agent_checkpoints", cfg.sqlite_path):
        if checkpoint.get("checkpoint_id") == checkpoint_id:
            return checkpoint
    return None


def list_checkpoints(cfg: Settings = settings) -> list[dict[str, Any]]:
    init_db(cfg.sqlite_path)
    return list_payloads("agent_checkpoints", cfg.sqlite_path)


def transition_checkpoint(
    checkpoint_id: str,
    *,
    state: str,
    pause_reason: str | None = None,
    completed_steps: list[dict[str, Any]] | None = None,
    pending_steps: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if state not in CHECKPOINT_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_STATE", f"unsupported checkpoint state: {state}")
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise CheckpointError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint.get("state") in TERMINAL_STATES:
        raise CheckpointError("ERROR_CHECKPOINT_TERMINAL", "terminal checkpoint cannot transition")
    if state.startswith("PAUSED_") or state.startswith("BLOCKED_"):
        if not (pause_reason or "").strip():
            raise CheckpointError("ERROR_CHECKPOINT_PAUSE_REASON", "paused/blocked states require pause_reason")
    updated = dict(checkpoint)
    updated["state"] = state
    updated["pause_reason"] = pause_reason.strip() if pause_reason else None
    updated["updated_at"] = utc_now()
    updated["sequence"] = int(checkpoint.get("sequence", 0)) + 1
    if completed_steps is not None:
        updated["completed_steps"] = list(completed_steps)
    if pending_steps is not None:
        updated["pending_steps"] = list(pending_steps)
    if contradictions is not None:
        updated["contradictions"] = list(contradictions)
    upsert_payload("agent_checkpoints", "checkpoint_id", updated, cfg.sqlite_path)
    return updated


def resume_checkpoint(checkpoint_id: str, cfg: Settings = settings) -> dict[str, Any]:
    checkpoint = get_checkpoint(checkpoint_id, cfg)
    if checkpoint is None:
        raise CheckpointError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
    if checkpoint.get("state") not in {"PAUSED_PROVIDER", "PAUSED_RESOURCE", "BLOCKED_EXTERNAL"}:
        raise CheckpointError("ERROR_CHECKPOINT_NOT_RESUMABLE", f"state is not resumable: {checkpoint.get('state')}")
    return transition_checkpoint(checkpoint_id, state="EXECUTING", cfg=cfg)
