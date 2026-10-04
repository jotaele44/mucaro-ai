from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.model_failover import FailoverPolicy, run_with_failover
from backend.app.model_router import generate_answer
from backend.app.services import answer_question


CHUNKS = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]


def _cfg(root: Path, channel: str = "sideload") -> Settings:
    base = Settings(
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
    return replace(base, distribution_channel=channel)


def test_permission_denial_is_hard_failover_stop_and_never_reaches_mock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.model_router as router

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        router,
        "_local_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local transport reached")),
    )
    monkeypatch.setattr(
        router,
        "_mock_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mock fallback reached")),
    )

    result = run_with_failover(
        [
            {"provider": "openai_compatible_local", "model": "local"},
            {"provider": "mock", "model": "mock"},
        ],
        lambda provider, model: generate_answer(
            "q",
            CHUNKS,
            model_provider=provider,
            model_name=model,
            cfg=cfg,
        ),
        policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert result["status"] == "FAILOVER_HARD_FAILURE"
    assert result["error_status"] == "ERROR_PERMISSION_DENIED"
    assert result["interruption"] is False
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["provider"] == "openai_compatible_local"


def test_answer_service_passes_exact_cfg_to_every_failover_attempt_and_permission_stops_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.model_router as router
    import backend.app.services as services

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(services, "get_chunks", lambda _cfg: CHUNKS)
    monkeypatch.setattr(
        services,
        "retrieve",
        lambda *args, **kwargs: {"status": "RETRIEVAL_OK", "retrieved_chunks": CHUNKS},
    )
    monkeypatch.setattr(
        services,
        "plan_model_route",
        lambda **kwargs: {
            "status": "MODEL_ROUTE_READY",
            "selected_provider": "openai_compatible_local",
            "selected_model": "local",
            "selection_reason": "TEST",
            "route_sha256": "a" * 64,
            "candidates": [],
            "failover_order": [
                {"provider": "openai_compatible_local", "model": "local"},
                {"provider": "mock", "model": "mock"},
            ],
        },
    )
    monkeypatch.setattr(
        router,
        "_local_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("cfg was not propagated: local transport reached")
        ),
    )
    monkeypatch.setattr(
        router,
        "_mock_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mock fallback reached")),
    )

    result = answer_question(
        "q",
        cfg=cfg,
        persist=False,
        allow_mock_fallback=True,
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert result["status"] == "ERROR_PERMISSION_DENIED"
    assert len(result["model_attempts"]) == 1
    assert result["model_attempts"][0]["provider"] == "openai_compatible_local"


def test_permission_denial_never_pauses_checkpoint_as_provider_outage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services as services

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(services, "get_chunks", lambda _cfg: CHUNKS)
    monkeypatch.setattr(
        services,
        "retrieve",
        lambda *args, **kwargs: {"status": "RETRIEVAL_OK", "retrieved_chunks": CHUNKS},
    )
    monkeypatch.setattr(
        services,
        "plan_model_route",
        lambda **kwargs: {
            "status": "MODEL_ROUTE_READY",
            "selected_provider": "openai_compatible_local",
            "selected_model": "local",
            "selection_reason": "TEST",
            "route_sha256": "a" * 64,
            "candidates": [],
            "failover_order": [{"provider": "openai_compatible_local", "model": "local"}],
        },
    )
    monkeypatch.setattr(
        services,
        "pause_provider_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("permission denial treated as outage")),
    )

    result = answer_question(
        "q",
        cfg=cfg,
        persist=False,
        checkpoint_id="checkpoint-never-used",
        failover_policy=FailoverPolicy(max_attempts_per_provider=1, backoff_seconds=()),
    )

    assert result["status"] == "ERROR_PERMISSION_DENIED"
    assert result["model_attempts"][0]["status"] == "ERROR_PERMISSION_DENIED"


def test_approval_required_status_is_terminal_for_failover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.model_router as router

    cfg = _cfg(tmp_path)
    calls: list[str] = []

    def approval_required(spec, _cfg):
        calls.append(spec.name)
        raise router.ModelProviderError(
            "ERROR_PERMISSION_APPROVAL_REQUIRED",
            "explicit approval required",
        )

    monkeypatch.setattr(router, "_require_model_invoke", approval_required)
    monkeypatch.setattr(
        router,
        "_local_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local transport reached")),
    )
    monkeypatch.setattr(
        router,
        "_mock_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mock fallback reached")),
    )

    result = run_with_failover(
        [
            {"provider": "openai_compatible_local", "model": "local"},
            {"provider": "mock", "model": "mock"},
        ],
        lambda provider, model: generate_answer(
            "q",
            CHUNKS,
            model_provider=provider,
            model_name=model,
            cfg=cfg,
        ),
        policy=FailoverPolicy(max_attempts_per_provider=2, backoff_seconds=(0.0,)),
    )

    assert result["status"] == "FAILOVER_HARD_FAILURE"
    assert result["error_status"] == "ERROR_PERMISSION_APPROVAL_REQUIRED"
    assert result["interruption"] is False
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["provider"] == "openai_compatible_local"
    assert calls == ["openai_compatible_local"]
