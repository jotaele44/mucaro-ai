from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.app.autopilot_planner as planner
from backend.app.agent_checkpoints import get_checkpoint
from backend.app.autopilot import authorize_autopilot, run_autopilot
from backend.app.autopilot_planner import (
    AutopilotPlannerError,
    plan_autopilot_from_model,
    replan_autopilot_from_model,
)
from backend.app.config import Settings
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, list_tool_calls
from backend.app.utils import canonical_sha256

SHA = "a" * 40


def make_settings(root: Path) -> Settings:
    cfg = Settings(
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
    cfg.ensure_dirs()
    return cfg


def context(sha: str = SHA) -> dict:
    return {
        "repository": {
            "repo_root": "/repo",
            "head_sha": sha,
            "branch": "feature",
            "dirty_status": "CLEAN",
            "dirty_paths": [],
            "status_tool_call_id": "tool_status",
            "status_executed_at": "2026-10-13T12:00:00Z",
        }
    }


def proposal(
    objective: str,
    *,
    kind: str = "INITIAL",
    tool_name: str | None = "source_library.status",
    arguments: dict | None = None,
    depends_on: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "mucaro-autopilot-plan-proposal/1.0",
        "kind": kind,
        "objective": objective,
        "rationale": "Bounded fixture plan.",
        "tasks": [
            {
                "id": "step",
                "title": "Bounded step",
                "description": "Execute one governed action.",
                "depends_on": list(depends_on or []),
                "action": None if tool_name is None else {
                    "tool_name": tool_name,
                    "arguments": dict(arguments or {}),
                },
            }
        ],
    }


def install_model_fixture(monkeypatch: pytest.MonkeyPatch, responses: list[dict]) -> None:
    queue = [json.dumps(item) for item in responses]

    monkeypatch.setattr(
        planner,
        "plan_model_route",
        lambda **kwargs: {
            "status": "MODEL_ROUTE_READY",
            "selected_provider": "openai_compatible_local",
            "selected_model": "fixture-local",
            "route_sha256": "b" * 64,
            "selection_reason": "TEST",
        },
    )

    def generate_text(*args, **kwargs):
        assert queue, "unexpected planner model invocation"
        return {
            "model_provider": "openai_compatible_local",
            "model_name": "fixture-local",
            "text": queue.pop(0),
            "model_usage": {
                "input_tokens": {
                    "value": 20,
                    "classification": "PROVIDER_REPORTED",
                    "source": "fixture",
                    "basis": [],
                }
            },
        }

    monkeypatch.setattr(planner, "generate_text", generate_text)


def test_model_plan_is_validated_persisted_and_requires_authorization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Inspect the local source library."
    install_model_fixture(monkeypatch, [proposal(objective)])

    planned = plan_autopilot_from_model(
        session_id="session",
        run_id="model-run",
        objective=objective,
        source_sha=SHA,
        context=context(),
        cfg=cfg,
    )

    checkpoint = planned["checkpoint"]
    assert planned["status"] == "AUTOPILOT_MODEL_PLAN_READY"
    assert checkpoint["state"] == "READY_FOR_APPROVAL"
    assert checkpoint["metadata"]["autopilot"]["authorization_state"] == "PENDING"
    assert checkpoint["metadata"]["autopilot"]["budgets"]["max_replans"] == 2
    assert list_tool_calls(cfg)["tool_calls"] == []

    receipt = planned["planning_receipt"]
    probe = dict(receipt)
    digest = probe.pop("receipt_sha256")
    assert digest == canonical_sha256(probe)
    assert receipt["kind"] == "INITIAL"
    assert receipt["source_sha"] == SHA
    assert receipt["dag_definition_sha256"] == planned["task_dag"]["definition_sha256"]
    assert all(receipt["invariants"].values())

    not_authorized = run_autopilot(checkpoint["checkpoint_id"], cfg=cfg)
    assert not_authorized["status"] == "AUTOPILOT_NOT_AUTHORIZED"

    authorized = authorize_autopilot(
        checkpoint["checkpoint_id"],
        approved_by="operator",
        expected_sequence=checkpoint["sequence"],
        cfg=cfg,
    )["checkpoint"]
    finished = run_autopilot(authorized["checkpoint_id"], cfg=cfg)
    assert finished["status"] == "AUTOPILOT_COMPLETED"
    assert len(list_tool_calls(cfg)["tool_calls"]) == 1


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        ("not-json", "ERROR_AUTOPILOT_PLAN_JSON"),
        (
            json.dumps({
                "schema_version": "mucaro-autopilot-plan-proposal/1.0",
                "kind": "INITIAL",
                "objective": "changed",
                "rationale": "",
                "tasks": [{
                    "id": "step",
                    "title": "x",
                    "description": "",
                    "depends_on": [],
                    "action": None,
                }],
            }),
            "ERROR_AUTOPILOT_PLAN_OBJECTIVE_DRIFT",
        ),
    ],
)
def test_model_plan_rejects_untrusted_output_before_checkpoint_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw: str,
    status: str,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Keep this objective exact."
    monkeypatch.setattr(
        planner,
        "plan_model_route",
        lambda **kwargs: {
            "status": "MODEL_ROUTE_READY",
            "selected_provider": "openai_compatible_local",
            "selected_model": "fixture",
            "route_sha256": "b" * 64,
        },
    )
    monkeypatch.setattr(
        planner,
        "generate_text",
        lambda *args, **kwargs: {
            "model_provider": "openai_compatible_local",
            "model_name": "fixture",
            "text": raw,
        },
    )

    with pytest.raises(AutopilotPlannerError) as exc:
        plan_autopilot_from_model(
            session_id="s",
            run_id="r",
            objective=objective,
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert exc.value.status == status
    assert get_checkpoint("anything", cfg) is None


def test_model_plan_rejects_unknown_tool_and_cyclic_dag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Use only admissible tools."
    unknown = proposal(objective, tool_name="unknown.tool")
    install_model_fixture(monkeypatch, [unknown])
    with pytest.raises(AutopilotPlannerError) as exc:
        plan_autopilot_from_model(
            session_id="s",
            run_id="unknown",
            objective=objective,
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_PLAN_TOOL_UNAVAILABLE"

    cyclic = {
        "schema_version": "mucaro-autopilot-plan-proposal/1.0",
        "kind": "INITIAL",
        "objective": objective,
        "rationale": "",
        "tasks": [
            {
                "id": "a",
                "title": "A",
                "description": "",
                "depends_on": ["b"],
                "action": None,
            },
            {
                "id": "b",
                "title": "B",
                "description": "",
                "depends_on": ["a"],
                "action": None,
            },
        ],
    }
    install_model_fixture(monkeypatch, [cyclic])
    with pytest.raises(AutopilotPlannerError) as cycle:
        plan_autopilot_from_model(
            session_id="s",
            run_id="cycle",
            objective=objective,
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert cycle.value.status == "ERROR_TASK_DAG_CYCLE"


def test_model_plan_rejects_tool_without_autopilot_success_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Fail closed."
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.no_contract",
        ToolDefinition(
            name="test.no_contract",
            description="fixture",
            mutates_state=False,
            requires_approval=True,
            handler=lambda arguments, cfg: {"status": "OK"},
        ),
    )
    install_model_fixture(monkeypatch, [proposal(objective, tool_name="test.no_contract")])

    with pytest.raises(AutopilotPlannerError) as exc:
        plan_autopilot_from_model(
            session_id="s",
            run_id="r",
            objective=objective,
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_PLAN_TOOL_SUCCESS_CONTRACT"


def test_planner_requires_governed_repository_identity_and_exact_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Inspect."
    install_model_fixture(monkeypatch, [proposal(objective)])

    with pytest.raises(AutopilotPlannerError) as absent:
        plan_autopilot_from_model(
            session_id="s",
            run_id="r",
            objective=objective,
            source_sha=SHA,
            context={},
            cfg=cfg,
        )
    assert absent.value.status == "ERROR_AUTOPILOT_PLANNER_REPOSITORY_CONTEXT"

    with pytest.raises(AutopilotPlannerError) as drift:
        plan_autopilot_from_model(
            session_id="s",
            run_id="r",
            objective=objective,
            source_sha=SHA,
            context=context("c" * 40),
            cfg=cfg,
        )
    assert drift.value.status == "ERROR_AUTOPILOT_PLANNER_SOURCE_DRIFT"


def test_replan_creates_new_unauthorized_successor_and_preserves_predecessor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Recover after a failed bounded action."

    def fail_handler(arguments, settings):
        return {"status": "NOT_OK"}

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.planner_fail",
        ToolDefinition(
            name="test.planner_fail",
            description="fixture failing action",
            mutates_state=False,
            requires_approval=True,
            handler=fail_handler,
            autopilot_success_statuses=("OK",),
        ),
    )
    install_model_fixture(
        monkeypatch,
        [
            proposal(objective, tool_name="test.planner_fail"),
            proposal(objective, kind="REPLAN", tool_name="source_library.status"),
        ],
    )

    initial = plan_autopilot_from_model(
        session_id="session",
        run_id="initial",
        objective=objective,
        source_sha=SHA,
        context=context(),
        cfg=cfg,
    )
    first = initial["checkpoint"]
    authorized = authorize_autopilot(
        first["checkpoint_id"],
        approved_by="operator",
        expected_sequence=first["sequence"],
        cfg=cfg,
    )["checkpoint"]
    stopped = run_autopilot(authorized["checkpoint_id"], cfg=cfg)
    assert stopped["status"] == "AUTOPILOT_STOPPED"
    assert stopped["reason"] == "VECTOR_EXHAUSTED"
    predecessor = get_checkpoint(first["checkpoint_id"], cfg)
    assert predecessor["state"] == "FAILED"

    replanned = replan_autopilot_from_model(
        first["checkpoint_id"],
        source_sha=SHA,
        context=context(),
        cfg=cfg,
    )
    successor = replanned["checkpoint"]
    assert replanned["status"] == "AUTOPILOT_MODEL_REPLAN_READY"
    assert successor["checkpoint_id"] != first["checkpoint_id"]
    assert successor["run_id"] != first["run_id"]
    assert successor["state"] == "READY_FOR_APPROVAL"
    assert successor["metadata"]["autopilot"]["authorization_state"] == "PENDING"
    assert successor["metadata"]["autopilot"]["counters"]["replans"] == 1
    planning = successor["metadata"]["autopilot_planning"]
    assert planning["predecessor_checkpoint_id"] == first["checkpoint_id"]
    assert len(planning["history"]) == 2
    assert planning["history"][0]["receipt_sha256"] == initial["planning_receipt"]["receipt_sha256"]
    assert get_checkpoint(first["checkpoint_id"], cfg)["state"] == "FAILED"

    with pytest.raises(AutopilotPlannerError) as duplicate:
        replan_autopilot_from_model(
            first["checkpoint_id"],
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert duplicate.value.status == "ERROR_AUTOPILOT_REPLAN_DUPLICATE"


def test_replan_budget_zero_blocks_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    objective = "Do not exceed replan budget."
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.planner_fail_zero",
        ToolDefinition(
            name="test.planner_fail_zero",
            description="fixture failing action",
            mutates_state=False,
            requires_approval=True,
            handler=lambda arguments, settings: {"status": "NO"},
            autopilot_success_statuses=("YES",),
        ),
    )
    install_model_fixture(monkeypatch, [proposal(objective, tool_name="test.planner_fail_zero")])
    initial = plan_autopilot_from_model(
        session_id="s",
        run_id="r",
        objective=objective,
        source_sha=SHA,
        context=context(),
        budgets={"max_replans": 0},
        cfg=cfg,
    )
    cp = authorize_autopilot(
        initial["checkpoint"]["checkpoint_id"],
        approved_by="operator",
        expected_sequence=initial["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]
    assert run_autopilot(cp["checkpoint_id"], cfg=cfg)["reason"] == "VECTOR_EXHAUSTED"

    with pytest.raises(AutopilotPlannerError) as exc:
        replan_autopilot_from_model(
            cp["checkpoint_id"],
            source_sha=SHA,
            context=context(),
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_REPLAN_BUDGET"
