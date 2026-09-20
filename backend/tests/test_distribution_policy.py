from __future__ import annotations

import pytest

from backend.app.distribution_policy import (
    DistributionPolicyError,
    evaluate_distribution_runtime_policy,
    require_distribution_runtime_authorized,
)


def test_local_distribution_preserves_existing_runtime_routes() -> None:
    decision = evaluate_distribution_runtime_policy(
        distribution_channel="local",
        runtime="CONTAINER",
        granted_capabilities={"runtime.container", "subprocess.execute"},
        skill_id="test/container",
    )

    assert decision.authorized is True
    assert decision.denied_capabilities == ()


@pytest.mark.parametrize(
    ("runtime", "capabilities"),
    [
        ("LOCAL_WORKER", {"runtime.local_worker"}),
        ("CONTAINER", {"runtime.container"}),
        ("IN_PROCESS", {"runtime.in_process", "subprocess.execute"}),
    ],
)
def test_app_store_distribution_denies_executable_boundaries(runtime: str, capabilities: set[str]) -> None:
    decision = evaluate_distribution_runtime_policy(
        distribution_channel="mac_app_store",
        runtime=runtime,
        granted_capabilities=capabilities,
        skill_id="mucaro/intelligence-plane-health",
    )

    assert decision.authorized is False
    assert decision.reasons


def test_app_store_distribution_allows_bounded_in_process_runtime() -> None:
    decision = require_distribution_runtime_authorized(
        distribution_channel="mac_app_store",
        runtime="IN_PROCESS",
        granted_capabilities={"runtime.in_process", "filesystem.read"},
        skill_id="mucaro/intelligence-plane-health",
    )

    assert decision.authorized is True


def test_app_store_distribution_denies_unreviewed_in_process_skill() -> None:
    decision = evaluate_distribution_runtime_policy(
        distribution_channel="mac_app_store",
        runtime="IN_PROCESS",
        granted_capabilities={"runtime.in_process"},
        skill_id="third-party/unreviewed",
    )

    assert decision.authorized is False
    assert decision.reasons == (
        "skill not reviewed for Mac App Store distribution: third-party/unreviewed",
    )


def test_unknown_distribution_channel_fails_closed() -> None:
    with pytest.raises(DistributionPolicyError) as exc:
        require_distribution_runtime_authorized(
            distribution_channel="unknown",
            runtime="IN_PROCESS",
            skill_id="mucaro/intelligence-plane-health",
        )

    assert exc.value.status == "ERROR_DISTRIBUTION_RUNTIME_POLICY"
