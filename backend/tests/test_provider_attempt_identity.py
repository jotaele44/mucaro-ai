from __future__ import annotations

from pathlib import Path

from backend.app.agent_checkpoints import create_checkpoint, get_checkpoint
from backend.app.config import Settings
from backend.app.model_router import ModelProviderError
from backend.app.provider_recovery import execute_with_provider_checkpoint


SHA = "c" * 40


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


def test_accepted_operation_is_not_executed_twice(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        return {"answer": "accepted"}

    first = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="mock", model="m", operation=operation, operation_key="answer:turn-7", cfg=cfg
    )
    second = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="mock", model="m", operation=operation, operation_key="answer:turn-7", cfg=cfg
    )

    assert first["status"] == "PROVIDER_EXECUTION_OK"
    assert second["status"] == "PROVIDER_EXECUTION_ALREADY_ACCEPTED"
    assert first["attempt_id"] == second["attempt_id"]
    assert second["result"] == {"answer": "accepted"}
    assert calls["count"] == 1


def test_interruption_is_recorded_as_unknown_not_accepted(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def timeout():
        raise ModelProviderError("ERROR_MODEL_PROVIDER_TIMEOUT", "ambiguous timeout")

    result = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="anthropic", model="a", operation=timeout, operation_key="answer:turn-8", cfg=cfg
    )

    persisted = get_checkpoint(cp["checkpoint_id"], cfg)
    attempt = persisted["metadata"]["provider_attempts"][result["attempt_id"]]
    assert result["status"] == "PAUSED_PROVIDER"
    assert attempt["state"] == "INTERRUPTED_UNKNOWN"
    assert attempt["provider_error"] == "ERROR_MODEL_PROVIDER_TIMEOUT"


def test_operation_identity_is_separate_from_provider_identity(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)

    one = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="mock", model="model-a", operation=lambda: {"value": 1}, operation_key="op-1", cfg=cfg
    )
    two = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="mock", model="model-b", operation=lambda: {"value": 2}, operation_key="op-2", cfg=cfg
    )

    assert one["attempt_id"] != two["attempt_id"]
    persisted = get_checkpoint(cp["checkpoint_id"], cfg)
    assert set(persisted["metadata"]["provider_attempts"]) == {one["attempt_id"], two["attempt_id"]}
