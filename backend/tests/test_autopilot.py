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
from backend.app.permission_leases import grant_permission_lease
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

    assert ci.autopilot_result_is_success({"status": "GITHUB_CI_STATUS_OK", "ci_state": "PASS"}) is True
    assert ci.autopilot_result_is_success({"status": "GITHUB_CI_STATUS_OK", "ci_state": "FAIL"}) is False
    assert ci.autopilot_result_is_success({"status": "GITHUB_CI_STATUS_OK", "state": "PASS"}) is False
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


def test_mutating_autopilot_action_can_use_exact_run_permission_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = make_settings(tmp_path)
    executed = {"count": 0}

    def mutate(arguments, settings):
        executed["count"] += 1
        return {"status": "LEASE_MUTATE_OK"}

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.lease_mutate",
        ToolDefinition(
            name="test.lease_mutate",
            description="lease integration probe",
            mutates_state=True,
            requires_approval=True,
            handler=mutate,
            autopilot_success_statuses=("LEASE_MUTATE_OK",),
        ),
    )
    planned = plan_autopilot(
        session_id="s",
        run_id="run-lease",
        objective="Execute bounded leased mutation",
        source_sha=SHA,
        task_definitions=[action_task("mutate", "test.lease_mutate")],
        cfg=cfg,
    )
    checkpoint = planned["checkpoint"]
    grant = grant_permission_lease(
        run_id="run-lease",
        scope_type="EXACT_TOOLS",
        tool_names=["test.lease_mutate"],
        allow_mutating=True,
        granted_by="operator",
        max_uses=1,
        cfg=cfg,
    )["lease"]
    authorized = authorize_autopilot(
        checkpoint["checkpoint_id"],
        approved_by="operator",
        expected_sequence=checkpoint["sequence"],
        cfg=cfg,
    )["checkpoint"]

    result = run_autopilot(authorized["checkpoint_id"], cfg=cfg)

    assert result["status"] == "AUTOPILOT_COMPLETED"
    assert executed["count"] == 1
    calls = list_tool_calls(cfg)["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["approved_by"] == f"lease:{grant['lease_id']}"


def test_github_ci_autopilot_success_contract_binds_ci_state() -> None:
    tool = TOOL_REGISTRY["github.ci_status"]

    assert tool.autopilot_result_is_success(
        {"status": "GITHUB_CI_STATUS_OK", "ci_state": "PASS"}
    ) is True
    assert tool.autopilot_result_is_success(
        {"status": "GITHUB_CI_STATUS_OK", "ci_state": "FAIL"}
    ) is False
    assert tool.autopilot_result_is_success(
        {"status": "GITHUB_CI_STATUS_OK", "state": "PASS"}
    ) is False


COMMAND_EXECUTING_TOOLS = ("coding.exec", "coding.regression_run")


@pytest.mark.parametrize("tool_name", COMMAND_EXECUTING_TOOLS)
def test_plan_cannot_get_a_command_executed_without_explicit_per_call_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tool_name: str
) -> None:
    cfg = make_settings(tmp_path)
    executed = {"count": 0}
    real = TOOL_REGISTRY[tool_name]
    # The real registration flags are kept (non-mutating, local): only the handler is replaced, so the test would
    # execute the "command" if Autopilot approved its own call the way it does for read-only inspection tools.
    assert real.mutates_state is False and real.network == "none" and real.requires_approval is True

    def handler(arguments, settings):
        executed["count"] += 1
        return {"status": real.autopilot_success_statuses[0], "execution_status": "PASS"}

    monkeypatch.setitem(
        TOOL_REGISTRY,
        tool_name,
        ToolDefinition(
            name=real.name,
            description=real.description,
            mutates_state=real.mutates_state,
            requires_approval=real.requires_approval,
            network=real.network,
            handler=handler,
            autopilot_success_statuses=real.autopilot_success_statuses,
            autopilot_success_fields=real.autopilot_success_fields,
        ),
    )
    planned = plan_autopilot(
        session_id="s",
        run_id="r",
        objective="Run a command",
        source_sha=SHA,
        task_definitions=[action_task("run", tool_name, arguments={"command": ["true"]})],
        cfg=cfg,
    )
    checkpoint = authorize_autopilot(
        planned["checkpoint"]["checkpoint_id"],
        approved_by="operator",
        expected_sequence=planned["checkpoint"]["sequence"],
        cfg=cfg,
    )["checkpoint"]

    assert autopilot.autopilot_may_self_approve(tool_name) is False
    # Authorizing the plan is not approval of the call: however often Autopilot is advanced, nothing runs.
    for _ in range(3):
        waiting = run_autopilot(checkpoint["checkpoint_id"], cfg=cfg)
        assert waiting["status"] == "AUTOPILOT_WAITING_APPROVAL"
    assert executed["count"] == 0
    calls = list_tool_calls(cfg)["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "PENDING_APPROVAL"
    assert calls[0].get("approved_by") in (None, "")

    approve_tool_call(
        calls[0]["tool_call_id"],
        cfg=cfg,
        approved_by="operator",
        expected_arguments_sha256=calls[0]["arguments_sha256"],
    )
    result = run_autopilot(checkpoint["checkpoint_id"], cfg=cfg)
    assert result["status"] == "AUTOPILOT_COMPLETED"
    assert executed["count"] == 1
    assert list_tool_calls(cfg)["tool_calls"][0]["approved_by"] == "operator"


def test_self_approval_allowlist_contains_only_read_only_local_tools() -> None:
    for name in autopilot.AUTOPILOT_SELF_APPROVABLE_TOOLS:
        tool = TOOL_REGISTRY[name]
        assert tool.mutates_state is False and tool.network == "none", name
    for name, tool in TOOL_REGISTRY.items():
        if tool.mutates_state or tool.network != "none" or name in COMMAND_EXECUTING_TOOLS:
            assert autopilot.autopilot_may_self_approve(name) is False, name
    assert autopilot.autopilot_may_self_approve("ghost.tool") is False


def test_allowlisting_a_mutating_tool_still_does_not_make_it_self_approvable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        autopilot,
        "AUTOPILOT_SELF_APPROVABLE_TOOLS",
        autopilot.AUTOPILOT_SELF_APPROVABLE_TOOLS | {"coding.commit", "github.pr_open_update"},
    )
    assert autopilot.autopilot_may_self_approve("coding.commit") is False
    assert autopilot.autopilot_may_self_approve("github.pr_open_update") is False
