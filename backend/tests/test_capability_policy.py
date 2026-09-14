from __future__ import annotations

import pytest

from backend.app.capability_policy import CapabilityPolicy, evaluate_capabilities, require_authorized


def test_policy_authorizes_only_declared_and_allowed_capabilities() -> None:
    policy = CapabilityPolicy.build(
        policy_id="local-gis",
        allowed={"filesystem.read", "filesystem.write", "runtime.local_worker"},
    )
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"filesystem.read", "filesystem.write", "runtime.local_worker"},
        requested_capabilities={"filesystem.read", "runtime.local_worker"},
    )
    assert decision.authorized is True
    assert set(decision.granted) == {"filesystem.read", "runtime.local_worker"}
    assert decision.denied == ()
    require_authorized(decision)


def test_policy_denies_undeclared_self_escalation() -> None:
    policy = CapabilityPolicy.build(policy_id="read-only", allowed={"filesystem.read", "network.public_http"})
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"filesystem.read"},
        requested_capabilities={"filesystem.read", "network.public_http"},
    )
    assert decision.authorized is False
    assert decision.granted == ()
    assert "network.public_http" in decision.denied
    assert any("exceeds declared" in reason for reason in decision.reasons)


def test_policy_denies_capability_outside_allowlist() -> None:
    policy = CapabilityPolicy.build(policy_id="no-subprocess", allowed={"filesystem.read"})
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"filesystem.read", "subprocess.execute"},
        requested_capabilities={"subprocess.execute"},
    )
    assert decision.authorized is False
    assert any("exceeds policy allowlist" in reason for reason in decision.reasons)


def test_policy_explicit_deny_overrides_allow() -> None:
    policy = CapabilityPolicy.build(
        policy_id="deny-network",
        allowed={"filesystem.read", "network.public_http"},
        denied={"network.public_http"},
    )
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"network.public_http"},
        requested_capabilities={"network.public_http"},
    )
    assert decision.authorized is False
    assert any("explicitly denied" in reason for reason in decision.reasons)


def test_policy_denies_unknown_capability_names() -> None:
    policy = CapabilityPolicy.build(policy_id="unknown", allowed={"filesystem.read", "root.shell"})
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"root.shell"},
        requested_capabilities={"root.shell"},
    )
    assert decision.authorized is False
    assert decision.granted == ()
    assert any("unknown capabilities" in reason for reason in decision.reasons)


def test_empty_request_is_authorized_without_ambient_capabilities() -> None:
    policy = CapabilityPolicy.build(policy_id="default-deny")
    decision = evaluate_capabilities(policy=policy, declared_capabilities=set(), requested_capabilities=set())
    assert decision.authorized is True
    assert decision.granted == ()


def test_denied_decision_raises_at_enforcement_boundary() -> None:
    policy = CapabilityPolicy.build(policy_id="read-only", allowed={"filesystem.read"})
    decision = evaluate_capabilities(
        policy=policy,
        declared_capabilities={"filesystem.write"},
        requested_capabilities={"filesystem.write"},
    )
    with pytest.raises(PermissionError):
        require_authorized(decision)
