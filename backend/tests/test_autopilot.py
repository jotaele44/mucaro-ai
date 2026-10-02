from __future__ import annotations

from pathlib import Path

import pytest

import backend.app.autopilot as autopilot
from backend.app.autopilot import (
    AutopilotError,
    advance_autopilot,
    authorize_autopilot,
    classify_progress,
    plan_autopilot,
    run_autopilot,
    update_autopilot_budgets,
)
from backend.app.agent_checkpoints import list_checkpoints
from backend.app.config import Settings
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, approve_tool_call, list_tool_calls

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


def action_task(task_id: str, tool_name: str, *, depends_on=None, arguments=None) -> dict:
    return {
        "id": task_id,
        "title": task_id,
        "depends_on": list(depends_on or []),
        "metadata": {
            "action": {
                "tool_name": tool_name,
                "arguments": dict(arguments or {}),
            }
        },
    }


def test_authorized_autopilot_executes_local_read_only_tool_and_completes(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Read source status",
        source_sha=SHA,
        task_definitions=[action_task("status", "source_library.status")],
        cfg=cfg,
    )
    checkpoint = planned["checkpoint"]
    authorized = authorize_autopilot(
        checkpoint["checkpoint_id"],
        approved_by="operator",
        expected_sequence=checkpoint["sequence"],
        cfg=cfg,
    )["checkpoint"]

    result = run_autopilot(authorized["checkpoint_id"], cfg=cfg)

    assert result["status"] == "AUTOPILOT_COMPLETED"
    assert result["reason"] == "OBJECTIVE_SATISFIED"
    calls = list_tool_calls(cfg)["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "source_library.status"
    assert calls[0]["status"] == "EXECUTED"
    assert calls[0]["approved_by"] == "autopilot:local-readonly-policy"


def test_mutating_tool_waits_for_explicit_approval_then_resumes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    executed = {"count": 0}

    def mutate(arguments, settings):
        executed["count"] += 1
        return {"status": "MUTATE_OK", "value": arguments["value"]}

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.mutate",
        ToolDefinition(
            name="test.mutate",
            description="mutation test",
            mutates_state=True,
            requires_approval=True,
            handler=mutate,
            autopilot_success_statuses=("MUTATE_OK",),
        ),
    )
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Mutate only after approval",
        source_sha=SHA,
        task_definitions=[action_task("mutate", "test.mutate", arguments={"value": 3})],
        cfg=cfg,
    )
    checkpoint = authorize_autopilot(
        planned["checkpoint"]["checkpoint_id"],
        approved_by="operator",
        expected_sequence=planned["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]

    waiting = advance_autopilot(checkpoint["checkpoint_id"], cfg=cfg)
    assert waiting["status"] == "AUTOPILOT_WAITING_APPROVAL"
    assert executed["count"] == 0

    call = list_tool_calls(cfg)["tool_calls"][0]
    approve_tool_call(
        call["tool_call_id"],
        cfg=cfg,
        approved_by="operator",
        expected_arguments_sha256=call["arguments_sha256"],
    )
    result = run_autopilot(checkpoint["checkpoint_id"], cfg=cfg)

    assert result["status"] == "AUTOPILOT_COMPLETED"
    assert executed["count"] == 1


def test_tool_call_budget_pauses_before_second_request_and_can_resume(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Two reads",
        source_sha=SHA,
        task_definitions=[
            action_task("a", "source_library.status"),
            action_task("b", "source_library.status", depends_on=["a"]),
        ],
        budgets={"max_tool_calls": 1},
        cfg=cfg,
    )
    checkpoint = authorize_autopilot(
        planned["checkpoint"]["checkpoint_id"],
        approved_by="operator",
        expected_sequence=planned["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]

    paused = run_autopilot(checkpoint["checkpoint_id"], cfg=cfg)
    assert paused["status"] == "AUTOPILOT_STOPPED"
    assert paused["reason"] == "BUDGET_EXHAUSTED_TOOL_CALLS"
    assert paused["checkpoint"]["state"] == "PAUSED_RESOURCE"
    assert len(list_tool_calls(cfg)["tool_calls"]) == 1

    updated = update_autopilot_budgets(
        checkpoint["checkpoint_id"],
        budgets={"max_tool_calls": 2},
        expected_sequence=paused["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]
    resumed = authorize_autopilot(
        checkpoint["checkpoint_id"],
        approved_by="operator",
        expected_sequence=updated["sequence"],
        cfg=cfg,
    )["checkpoint"]
    finished = run_autopilot(resumed["checkpoint_id"], cfg=cfg)

    assert finished["status"] == "AUTOPILOT_COMPLETED"
    assert len(list_tool_calls(cfg)["tool_calls"]) == 2


def test_unmetered_model_budget_fails_closed_before_tool_execution(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Budgeted read",
        source_sha=SHA,
        task_definitions=[action_task("a", "source_library.status")],
        budgets={"max_model_tokens": 100},
        cfg=cfg,
    )
    checkpoint = authorize_autopilot(
        planned["checkpoint"]["checkpoint_id"],
        approved_by="operator",
        expected_sequence=planned["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]

    result = advance_autopilot(checkpoint["checkpoint_id"], cfg=cfg)

    assert result["status"] == "AUTOPILOT_STOPPED"
    assert result["reason"] == "BUDGET_UNVERIFIABLE_MODEL_TOKENS"
    assert result["checkpoint"]["state"] == "PAUSED_RESOURCE"
    assert list_tool_calls(cfg)["tool_calls"] == []


def test_progress_classifier_detects_no_progress_and_nonconsecutive_loops() -> None:
    a = "a" * 64
    b = "b" * 64
    history, streak, reason = classify_progress(
        history=[],
        no_progress_streak=1,
        before=a,
        after=a,
        max_no_progress=2,
        max_fingerprint_occurrences=3,
    )
    assert history == [a]
    assert streak == 2
    assert reason == "NO_PROGRESS"

    history, streak, reason = classify_progress(
        history=[a, b, a, b],
        no_progress_streak=0,
        before=b,
        after=a,
        max_no_progress=3,
        max_fingerprint_occurrences=3,
    )
    assert history[-1] == a
    assert streak == 0
    assert reason == "LOOP_DETECTED"


def test_task_call_artifact_is_not_identity_without_independent_bindings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    checkpoint = {
        "checkpoint_id": "checkpoint-1",
        "run_id": "run-1",
    }
    action = {
        "tool_name": "source_library.status",
        "arguments": {},
    }
    task = {
        "id": "status",
        "artifacts": [
            {
                "kind": "tool_call",
                "tool_call_id": "call-1",
                "tool_name": "source_library.status",
                "arguments_sha256": autopilot.arguments_digest({}),
            }
        ],
    }
    monkeypatch.setattr(
        autopilot,
        "list_tool_calls",
        lambda cfg: {
            "tool_calls": [
                {
                    "tool_call_id": "call-1",
                    "tool_name": "source_library.status",
                    "arguments_sha256": autopilot.arguments_digest({}),
                    "run_id": "different-run",
                    "requested_by": "autopilot",
                    "reason": "different-reason",
                }
            ]
        },
    )

    with pytest.raises(AutopilotError) as exc:
        autopilot._task_call(checkpoint, task, action, cfg)
    assert exc.value.status == "ERROR_AUTOPILOT_TOOL_CALL_IDENTITY"


def test_invalid_budget_update_does_not_silently_reset_existing_limits(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Budget preservation",
        source_sha=SHA,
        task_definitions=[action_task("a", "source_library.status")],
        budgets={"max_steps": 7, "max_tool_calls": 3},
        cfg=cfg,
    )
    checkpoint = planned["checkpoint"]
    # Budget changes are allowed while still planned and merge with the existing complete budget object.
    updated = update_autopilot_budgets(
        checkpoint["checkpoint_id"],
        budgets={"max_tool_calls": 4},
        expected_sequence=checkpoint["sequence"],
        cfg=cfg,
    )["checkpoint"]
    state = updated["metadata"]["autopilot"]
    assert state["budgets"]["max_steps"] == 7
    assert state["budgets"]["max_tool_calls"] == 4


def test_invalid_autopilot_plan_writes_no_orphan_checkpoint(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(AutopilotError) as exc:
        plan_autopilot(
            session_id="s",
            run_id="r",
            objective="Invalid cycle",
            source_sha=SHA,
            task_definitions=[
                {"id": "a", "title": "A", "depends_on": ["b"]},
                {"id": "b", "title": "B", "depends_on": ["a"]},
            ],
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_TASK_DAG_CYCLE"
    assert list_checkpoints(cfg) == []


def test_plan_cannot_redefine_tool_failure_as_success(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(AutopilotError) as exc:
        plan_autopilot(
            session_id="s",
            run_id="r",
            objective="Do not redefine failure",
            source_sha=SHA,
            task_definitions=[
                {
                    "id": "ci",
                    "title": "Check CI",
                    "metadata": {
                        "action": {
                            "tool_name": "github.ci_status",
                            "arguments": {"repo": "owner/repo"},
                            "success_statuses": ["GITHUB_CI_STATUS_OK"],
                        }
                    },
                }
            ],
            cfg=cfg,
        )
    # Unknown action fields are rejected before checkpoint persistence; success semantics belong to ToolDefinition.
    assert exc.value.status == "ERROR_AUTOPILOT_ACTION"
    assert list_checkpoints(cfg) == []


def test_tool_owned_success_contract_rejects_ci_and_regression_failure_receipts() -> None:
    ci = TOOL_REGISTRY["github.ci_status"]
    regression = TOOL_REGISTRY["coding.regression_run"]

    assert ci.autopilot_result_is_success({"status": "GITHUB_CI_STATUS_OK", "state": "PASS"}) is True
    assert ci.autopilot_result_is_success({"status": "GITHUB_CI_STATUS_OK", "state": "FAIL"}) is False
    assert regression.autopilot_result_is_success(
        {"status": "CODING_CHECK_SUITE_EXECUTED_PASS", "execution_status": "PASS"}
    ) is True
    assert regression.autopilot_result_is_success(
        {"status": "CODING_CHECK_SUITE_EXECUTED_FAIL", "execution_status": "FAIL"}
    ) is False


def test_tool_without_autopilot_success_contract_is_rejected_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.no_contract",
        ToolDefinition(
            name="test.no_contract",
            description="no autonomous success semantics",
            mutates_state=False,
            requires_approval=True,
            handler=lambda arguments, settings: {"status": "OK"},
        ),
    )

    with pytest.raises(AutopilotError) as exc:
        plan_autopilot(
            session_id="s",
            run_id="r",
            objective="Must fail closed",
            source_sha=SHA,
            task_definitions=[action_task("probe", "test.no_contract")],
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_AUTOPILOT_SUCCESS_CONTRACT_MISSING"
    assert list_checkpoints(cfg) == []
