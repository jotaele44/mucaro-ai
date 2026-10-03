from __future__ import annotations

from pathlib import Path

import pytest

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


def _attempt(cfg: Settings, checkpoint_id: str, attempt_id: str) -> dict:
    return get_checkpoint(checkpoint_id, cfg)["metadata"]["provider_attempts"][attempt_id]


def test_started_attempt_is_not_run_by_a_second_executor_until_reclaimed(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import record_provider_attempt
    from backend.app.provider_recovery import ProviderRecoveryError, provider_attempt_id

    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    attempt_id = provider_attempt_id(cp, "answer:turn-9")
    # Another executor claimed the attempt and has not recorded an outcome (still running, or it crashed).
    record_provider_attempt(cp["checkpoint_id"], attempt_id=attempt_id, record={"operation_key": "answer:turn-9"}, claim=True, cfg=cfg)
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        return {"answer": "once"}

    with pytest.raises(ProviderRecoveryError) as busy:
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model="m", operation=operation, operation_key="answer:turn-9", cfg=cfg)
    assert busy.value.status == "ERROR_CHECKPOINT_ATTEMPT_IN_FLIGHT"
    assert calls["count"] == 0

    reclaimed = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="mock", model="m", operation=operation, operation_key="answer:turn-9", reclaim_started=True, cfg=cfg
    )
    assert reclaimed["status"] == "PROVIDER_EXECUTION_OK"
    assert calls["count"] == 1
    assert _attempt(cfg, cp["checkpoint_id"], attempt_id)["attempts"] == 2


def test_interrupted_operation_is_retried_after_resume_on_another_provider(tmp_path: Path) -> None:
    from backend.app.provider_recovery import resume_with_provider

    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def outage():
        raise ModelProviderError("ERROR_MODEL_PROVIDER_UNAVAILABLE", "503")

    paused = execute_with_provider_checkpoint(
        cp["checkpoint_id"], provider="anthropic", model="a", operation=outage, operation_key="answer:turn-10", cfg=cfg
    )
    assert paused["status"] == "PAUSED_PROVIDER"
    resume_with_provider(cp["checkpoint_id"], provider="openai_compatible_local", model="local", cfg=cfg)

    retried = execute_with_provider_checkpoint(
        cp["checkpoint_id"],
        provider="openai_compatible_local",
        model="local",
        operation=lambda: {"answer": "from fallback"},
        operation_key="answer:turn-10",
        cfg=cfg,
    )
    attempt = _attempt(cfg, cp["checkpoint_id"], retried["attempt_id"])
    assert retried["attempt_id"] == paused["attempt_id"]  # same logical operation, new provider
    assert attempt["state"] == "ACCEPTED"
    assert attempt["attempts"] == 2
    assert attempt["provider"] == "openai_compatible_local"
    assert attempt["result"] == {"answer": "from fallback"}


def test_non_interruption_failure_is_recorded_and_not_converted_to_pause(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def bad_request():
        raise ModelProviderError("ERROR_INVALID_MODEL_REQUEST", "400")

    with pytest.raises(ModelProviderError):
        execute_with_provider_checkpoint(
            cp["checkpoint_id"], provider="anthropic", model="a", operation=bad_request, operation_key="answer:turn-11", cfg=cfg
        )
    stored = get_checkpoint(cp["checkpoint_id"], cfg)
    attempt = next(iter(stored["metadata"]["provider_attempts"].values()))
    assert stored["state"] == "EXECUTING"
    assert attempt["state"] == "FAILED"
    assert attempt["provider_error"] == "ERROR_INVALID_MODEL_REQUEST"


def test_unexpected_crash_is_unknown_not_accepted(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)

    def crash():
        raise RuntimeError("socket closed mid-response")

    with pytest.raises(RuntimeError):
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="anthropic", model="a", operation=crash, operation_key="op-crash", cfg=cfg)
    attempt = next(iter(get_checkpoint(cp["checkpoint_id"], cfg)["metadata"]["provider_attempts"].values()))
    assert attempt["state"] == "INTERRUPTED_UNKNOWN"
    assert attempt["provider_error"] == "RuntimeError"


def test_terminal_checkpoint_refuses_new_provider_attempts(tmp_path: Path) -> None:
    from backend.app.agent_checkpoints import transition_checkpoint
    from backend.app.provider_recovery import ProviderRecoveryError

    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    transition_checkpoint(cp["checkpoint_id"], state="FAILED", cfg=cfg)
    with pytest.raises(ProviderRecoveryError) as exc:
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model="m", operation=lambda: {}, operation_key="late", cfg=cfg)
    assert exc.value.status == "ERROR_CHECKPOINT_TERMINAL"


def test_empty_operation_key_is_rejected(tmp_path: Path) -> None:
    from backend.app.provider_recovery import ProviderRecoveryError

    cfg = make_settings(tmp_path)
    cp = create_checkpoint(session_id="s", run_id="r", objective="answer", source_sha=SHA, state="EXECUTING", cfg=cfg)
    with pytest.raises(ProviderRecoveryError) as exc:
        execute_with_provider_checkpoint(cp["checkpoint_id"], provider="mock", model="m", operation=lambda: {}, operation_key="  ", cfg=cfg)
    assert exc.value.status == "ERROR_PROVIDER_OPERATION_KEY"
