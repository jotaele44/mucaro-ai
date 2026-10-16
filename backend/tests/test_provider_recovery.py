from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent_checkpoints import create_checkpoint, get_checkpoint
from backend.app.config import Settings
from backend.app.model_router import ModelProviderError
from backend.app.provider_recovery import execute_with_provider_checkpoint, resume_with_provider


SHA = "b" * 40


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


def test_provider_rate_limit_pauses_without_destroying_identity(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="session-1", run_id="run-1", objective="Continue research", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def limited():
        raise ModelProviderError("ERROR_MODEL_PROVIDER_RATE_LIMIT", "quota")

    result = execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="test-model", operation=limited, cfg=cfg)
    paused = result["checkpoint"]
    assert result["status"] == "PAUSED_PROVIDER"
    assert (paused["session_id"], paused["run_id"], paused["checkpoint_id"]) == ("session-1", "run-1", cp["checkpoint_id"])
    assert paused["provider"] == "anthropic"


def test_provider_switch_preserves_session_run_checkpoint_identity(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="session-1", run_id="run-1", objective="Continue", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def unavailable():
        raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "outage")

    execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="a", operation=unavailable, cfg=cfg)
    resumed = resume_with_provider(cp["checkpoint_id"], provider="mock", model="mock", cfg=cfg)

    assert resumed["state"] == "EXECUTING"
    assert (resumed["session_id"], resumed["run_id"], resumed["checkpoint_id"]) == ("session-1", "run-1", cp["checkpoint_id"])
    assert resumed["provider"] == "mock"
    assert get_checkpoint(cp["checkpoint_id"], cfg)["provider"] == "mock"


def test_non_provider_interruption_is_not_silently_converted_to_pause(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="session-1", run_id="run-1", objective="Continue", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def bad_request():
        raise ModelProviderError("ERROR_INVALID_MODEL_REQUEST", "bad")

    with pytest.raises(ModelProviderError) as exc:
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="a", operation=bad_request, cfg=cfg)
    assert exc.value.status == "ERROR_INVALID_MODEL_REQUEST"
    assert get_checkpoint(cp["checkpoint_id"], cfg)["state"] == "EXECUTING"


def test_local_provider_unavailable_pauses_and_persists_provider_model(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(
        session_id="session-local",
        run_id="run-local",
        objective="Continue local inference",
        source_sha=SHA,
        state="EXECUTING",
        cfg=cfg,
    )

    def unavailable():
        raise ModelProviderError("ERROR_LOCAL_MODEL_UNAVAILABLE", "loopback server unavailable")

    result = execute_with_provider_checkpoint(
        cp["checkpoint_id"],
        provider="openai_compatible_local",
        model="local-model",
        operation=unavailable,
        cfg=cfg,
    )

    stored = get_checkpoint(cp["checkpoint_id"], cfg)
    assert result["status"] == "PAUSED_PROVIDER"
    assert stored["state"] == "PAUSED_PROVIDER"
    assert stored["provider"] == "openai_compatible_local"
    assert stored["model"] == "local-model"
    assert stored["checkpoint_id"] == cp["checkpoint_id"]
