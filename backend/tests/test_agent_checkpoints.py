from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent_checkpoints import CheckpointError, create_checkpoint, get_checkpoint, resume_checkpoint, transition_checkpoint
from backend.app.config import Settings


SHA = "a" * 40


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_checkpoint_persists_and_resumes_after_new_read(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit repositories", source_sha=SHA, state="EXECUTING", pending_steps=[{"id": "next"}], cfg=cfg)
    paused = transition_checkpoint(created["checkpoint_id"], state="PAUSED_PROVIDER", pause_reason="provider quota exhausted", cfg=cfg)

    reloaded = get_checkpoint(created["checkpoint_id"], cfg)
    assert reloaded == paused
    resumed = resume_checkpoint(created["checkpoint_id"], cfg)
    assert resumed["state"] == "EXECUTING"
    assert resumed["sequence"] == 2
    assert resumed["pending_steps"] == [{"id": "next"}]
    assert resumed["pause_reason"] is None


def test_blocked_and_paused_states_require_reason(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, cfg=cfg)
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="BLOCKED_EXTERNAL", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_PAUSE_REASON"


def test_terminal_checkpoint_fails_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha=SHA, cfg=cfg)
    done = transition_checkpoint(created["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert done["state"] == "COMPLETED"
    with pytest.raises(CheckpointError) as exc:
        transition_checkpoint(created["checkpoint_id"], state="EXECUTING", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_TERMINAL"


def test_checkpoint_rejects_noncanonical_source_sha(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(CheckpointError) as exc:
        create_checkpoint(session_id="session-1", run_id="run-1", objective="Audit", source_sha="main", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_SOURCE_SHA"
