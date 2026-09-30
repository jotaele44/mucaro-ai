from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.model_router import ModelProviderError, generate_answer


CHUNKS = [{"chunk_id": "chunk_a", "source_id": "src_a", "text": "Source lineage requires citations."}]


def _cfg(root: Path, channel: str) -> Settings:
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


def test_local_model_permission_denial_happens_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.model_router as router

    monkeypatch.setattr(
        router,
        "_local_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local model transport reached")),
    )

    with pytest.raises(ModelProviderError) as exc:
        generate_answer(
            "q",
            CHUNKS,
            model_provider="openai_compatible_local",
            cfg=_cfg(tmp_path, "sideload"),
        )

    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_anthropic_permission_denial_happens_before_sdk_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.model_router as router

    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        router,
        "_anthropic_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Anthropic transport reached")),
    )

    with pytest.raises(ModelProviderError) as exc:
        generate_answer(
            "q",
            CHUNKS,
            model_provider="anthropic",
            cfg=_cfg(tmp_path, "sideload"),
        )

    assert exc.value.status == "ERROR_PERMISSION_DENIED"


def test_anthropic_config_errors_precede_permission_when_no_transport_can_occur(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ModelProviderError) as exc:
        generate_answer(
            "q",
            CHUNKS,
            model_provider="anthropic",
            cfg=_cfg(tmp_path, "sideload"),
        )

    assert exc.value.status == "ERROR_LIVE_MODE_DISABLED"


def test_mock_synthesis_does_not_require_model_invoke_permission(tmp_path: Path) -> None:
    result = generate_answer("q", CHUNKS, model_provider="mock", cfg=_cfg(tmp_path, "sideload"))
    assert result["model_provider"] == "mock"
    assert result["answer"]
