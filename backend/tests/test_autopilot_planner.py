from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.autopilot_planner as planner
from backend.app.agent_checkpoints import transition_checkpoint
from backend.app.autopilot import plan_autopilot
from backend.app.autopilot_planner import AutopilotPlannerError, generate_autopilot_plan, replan_autopilot
from backend.app.config import Settings

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


def model_response(task_id: str = "inspect") -> dict:
    return {
        "model_provider": "test-provider",
        "model_name": "test-model",
        "answer": (
            '{"tasks":[{"id":"' + task_id
            + '","title":"Inspect status","description":"Read local source status",'
            '"depends_on":[],"metadata":{"action":{"tool_name":"source_library.status","arguments":{}}}}],'
            '"rationale":"Use the smallest read-only plan."}'
        ),
        "model_usage": {
            "input_tokens": {"value": 100, "classification": "PROVIDER_REPORTED"},
            "output_tokens": {"value": 50, "classification": "PROVIDER_REPORTED"},
        },
    }


def test_model_plan_builds_canonical_dag_but_does_not_authorize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(planner, "generate_answer", lambda *args, **kwargs: model_response())
    result = generate_autopilot_plan(
        session_id="session",
        run_id="run",
        objective="Inspect status",
        source_sha=SHA,
        budgets={"max_replans": 1},
        provider="mock",
        model="mock",
        cfg=cfg,
    )
    assert result["status"] == "AUTOPILOT_MODEL_PLAN_READY"
    assert result["task_dag"]["topological_order"] == ["inspect"]
    assert result["checkpoint"]["metadata"]["autopilot"]["authorization_state"] == "PENDING"
    assert result["plan_receipt"]["source_sha"] == SHA
    assert len(result["plan_receipt"]["receipt_sha256"]) == 64


def test_replan_requires_new_trigger_and_preserves_budget_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    parent = plan_autopilot(
        session_id="session",
        run_id="run",
        objective="Inspect status",
        source_sha=SHA,
        task_definitions=[{
            "id": "inspect",
            "title": "Inspect",
            "metadata": {"action": {"tool_name": "source_library.status", "arguments": {}}},
        }],
        budgets={"max_replans": 1, "max_tool_calls": 4},
        provider="mock",
        model="mock",
        cfg=cfg,
    )["checkpoint"]
    parent = transition_checkpoint(
        parent["checkpoint_id"],
        state="BLOCKED_EXTERNAL",
        pause_reason="new external evidence required",
        expected_sequence=parent["sequence"],
        cfg=cfg,
    )
    monkeypatch.setattr(planner, "generate_answer", lambda *args, **kwargs: model_response("inspect-v2"))
    result = replan_autopilot(
        parent["checkpoint_id"],
        trigger={
            "kind": "NEW_EVIDENCE",
            "detail": "A new authoritative receipt changed the next read step.",
            "evidence": [{"receipt_sha256": "b" * 64}],
        },
        cfg=cfg,
    )
    successor = result["checkpoint"]
    assert result["status"] == "AUTOPILOT_REPLAN_READY"
    assert successor["checkpoint_id"] != parent["checkpoint_id"]
    assert successor["run_id"] == parent["run_id"]
    assert successor["source_sha"] == parent["source_sha"]
    assert successor["metadata"]["autopilot"]["authorization_state"] == "PENDING"
    assert successor["metadata"]["autopilot"]["counters"]["replans"] == 1
    assert successor["metadata"]["autopilot_replan"]["parent_checkpoint_id"] == parent["checkpoint_id"]
    assert successor["metadata"]["autopilot_replan"]["usage_offset"]["tool_calls"] == 0
    assert result["task_dag"]["topological_order"] == ["inspect-v2"]


def test_replan_budget_is_finite_and_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    parent = plan_autopilot(
        session_id="session",
        run_id="run",
        objective="Inspect status",
        source_sha=SHA,
        task_definitions=[{
            "id": "inspect",
            "title": "Inspect",
            "metadata": {"action": {"tool_name": "source_library.status", "arguments": {}}},
        }],
        budgets={"max_replans": 0},
        cfg=cfg,
    )["checkpoint"]
    parent = transition_checkpoint(
        parent["checkpoint_id"],
        state="PAUSED_RESOURCE",
        pause_reason="bounded pause",
        expected_sequence=parent["sequence"],
        cfg=cfg,
    )
    monkeypatch.setattr(planner, "generate_answer", lambda *args, **kwargs: model_response("inspect-v2"))
    with pytest.raises(AutopilotPlannerError) as exc:
        replan_autopilot(
            parent["checkpoint_id"],
            trigger={"kind": "TASK_FAILURE", "detail": "The bounded step failed.", "evidence": []},
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_REPLAN_BUDGET"


def test_model_plan_rejects_unknown_top_level_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setattr(
        planner,
        "generate_answer",
        lambda *args, **kwargs: {
            "model_provider": "test",
            "model_name": "test",
            "answer": '{"tasks":[{"id":"x","title":"X","metadata":{}}],"extra":"unexpected"}',
        },
    )
    with pytest.raises(AutopilotPlannerError) as exc:
        generate_autopilot_plan(
            session_id="session",
            run_id="run",
            objective="Reject unknown planner fields",
            source_sha=SHA,
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_MODEL_PLAN_FIELD"
