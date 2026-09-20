from __future__ import annotations

import pytest

from backend.app.model_routing_policy import ModelRoutingError, plan_model_route


def test_default_route_stays_offline_even_if_live_credentials_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    report = plan_model_route(task_type="cited_qa")

    assert report["status"] == "MODEL_ROUTE_READY"
    assert report["selected_provider"] == "mock"
    assert report["selection_reason"] == "SAFE_OFFLINE_DEFAULT"
    assert report["invariants"]["no_implicit_live_escalation"] is True
    assert len(report["route_sha256"]) == 64


def test_live_route_requires_explicit_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    with pytest.raises(ModelRoutingError) as exc:
        plan_model_route(task_type="analysis", require_live=True, allow_live=False)
    assert exc.value.status == "ERROR_MODEL_ROUTING_LIVE_NOT_ALLOWED"


def test_explicit_live_preference_is_ready_only_when_provider_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("backend.app.model_router.importlib.util.find_spec", lambda name: object())

    report = plan_model_route(
        task_type="analysis",
        preferred_provider="anthropic",
        allow_live=True,
        require_live=True,
    )

    assert report["status"] == "MODEL_ROUTE_READY"
    assert report["selected_provider"] == "anthropic"
    assert report["selection_reason"] == "EXPLICIT_PREFERRED_PROVIDER"
    assert all(report["invariants"].values())


def test_unready_preferred_provider_blocks_instead_of_falling_back_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    report = plan_model_route(
        task_type="analysis",
        preferred_provider="anthropic",
        allow_live=True,
    )

    assert report["status"] == "MODEL_ROUTE_BLOCKED"
    assert report["selected_provider"] is None
    assert report["selection_reason"] == "PREFERRED_PROVIDER_NOT_ELIGIBLE"


def test_allowlist_and_constraint_conflicts_fail_closed() -> None:
    with pytest.raises(ModelRoutingError) as conflict:
        plan_model_route(task_type="coding", require_live=True, require_offline=True, allow_live=True)
    assert conflict.value.status == "ERROR_MODEL_ROUTING_CONSTRAINT_CONFLICT"

    with pytest.raises(ModelRoutingError) as allowlist:
        plan_model_route(task_type="coding", preferred_provider="anthropic", allowed_providers=["mock"])
    assert allowlist.value.status == "ERROR_MODEL_ROUTING_PROVIDER_NOT_ALLOWED"

    with pytest.raises(ModelRoutingError) as unknown:
        plan_model_route(task_type="unknown")
    assert unknown.value.status == "ERROR_MODEL_ROUTING_TASK"


def test_live_provider_is_never_implicit_fallback_when_offline_provider_is_unready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUCARO_AI_LIVE_MODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("backend.app.model_router.importlib.util.find_spec", lambda name: object())

    # Restricting the allowlist to a live provider is still not permission to use it.
    report = plan_model_route(
        task_type="analysis",
        allowed_providers=["anthropic"],
        allow_live=False,
    )

    assert report["status"] == "MODEL_ROUTE_BLOCKED"
    assert report["selected_provider"] is None
    assert report["invariants"]["no_implicit_live_escalation"] is True
