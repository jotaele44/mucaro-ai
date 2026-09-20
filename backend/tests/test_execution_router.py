from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.capability_policy import CapabilityPolicy, evaluate_capabilities
from backend.app.config import Settings
from backend.app.execution_router import ExecutionRouterError, execute_routed, select_execution_route


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


def _decision(runtime_capability: str) -> dict:
    policy = CapabilityPolicy.build(policy_id="test", allowed={runtime_capability})
    return evaluate_capabilities(
        policy=policy,
        declared_capabilities={runtime_capability},
        requested_capabilities={runtime_capability},
    ).as_dict()


def test_in_process_route_requires_explicit_runtime_grant(tmp_path: Path) -> None:
    decision = _decision("runtime.in_process")
    route = select_execution_route("IN_PROCESS", decision)
    assert route == "IN_PROCESS"

    result = execute_routed(
        runtime=route,
        skill_id="test/inline",
        handler=lambda arguments, cfg: {"status": "OK", "value": arguments["value"]},
        arguments={"value": 4},
        cfg=make_settings(tmp_path),
        policy_decision=decision,
    )
    assert result["result"] == {"status": "OK", "value": 4}
    assert result["adapter_receipt"]["route"] == "IN_PROCESS"


def test_router_rejects_runtime_not_present_in_policy_grant() -> None:
    decision = _decision("runtime.in_process")
    with pytest.raises(ExecutionRouterError) as exc:
        select_execution_route("LOCAL_WORKER", decision)
    assert exc.value.status == "ERROR_EXECUTION_ROUTE_POLICY"


def test_router_rejects_unauthorized_policy() -> None:
    with pytest.raises(ExecutionRouterError) as exc:
        select_execution_route("IN_PROCESS", {"status": "DENIED", "granted": []})
    assert exc.value.status == "ERROR_EXECUTION_ROUTE_POLICY"


def test_router_fails_closed_when_container_skill_has_no_binding(tmp_path: Path) -> None:
    decision = _decision("runtime.container")
    with pytest.raises(ExecutionRouterError) as exc:
        execute_routed(
            runtime="CONTAINER",
            skill_id="test/container",
            handler=lambda arguments, cfg: {"status": "SHOULD_NOT_RUN"},
            arguments={},
            cfg=make_settings(tmp_path),
            policy_decision=decision,
        )
    assert exc.value.status == "ERROR_CONTAINER_BINDING_MISSING"


def test_app_store_channel_rejects_worker_route_before_spawn(tmp_path: Path) -> None:
    decision = _decision("runtime.local_worker")
    cfg = make_settings(tmp_path)
    cfg = Settings(**{**cfg.__dict__, "distribution_channel": "mac_app_store"})

    with pytest.raises(ExecutionRouterError) as exc:
        execute_routed(
            runtime="LOCAL_WORKER",
            skill_id="mucaro/local-worker-health",
            handler=lambda arguments, settings: {"status": "SHOULD_NOT_RUN"},
            arguments={},
            cfg=cfg,
            policy_decision=decision,
        )

    assert exc.value.status == "ERROR_DISTRIBUTION_RUNTIME_POLICY"
