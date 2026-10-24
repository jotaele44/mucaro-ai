from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.cli import main
from backend.app.goals import GoalError, create_goal, get_goal, list_goal_events, list_goals, update_goal
from backend.tests.test_api import make_settings


def test_goal_is_durable_current_and_event_backed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    created = create_goal(
        "Finish the Mucaro AI agent workflow",
        active_vector="goal_control",
        assumptions=["local-first default"],
        gaps=["hosted governance"],
        variables=["provider policy"],
        goal_id="goal-agent",
        cfg=cfg,
    )
    assert created["status"] == "GOAL_CREATED"
    assert created["goal"]["is_current"] is True
    assert created["events"][0]["event_type"] == "GOAL_CREATED"

    second = create_goal("A secondary objective", goal_id="goal-secondary", cfg=cfg)
    assert second["goal"]["is_current"] is True
    goals = list_goals(cfg)
    assert goals["current_goal_id"] == "goal-secondary"
    assert [goal["goal_id"] for goal in goals["goals"] if goal["is_current"]] == ["goal-secondary"]
    assert get_goal("goal-agent", cfg)["is_current"] is False

    updated = update_goal("goal-secondary", status="BLOCKED", gaps=["Need a user decision"], cfg=cfg)
    assert updated["goal"]["status"] == "BLOCKED"
    assert updated["goal"]["is_current"] is True
    events = list_goal_events("goal-secondary", cfg)["events"]
    assert [event["seq"] for event in events] == [1, 2]
    assert events[1]["event_type"] == "GOAL_STATUS_CHANGED"


def test_goal_transitions_fail_closed_and_terminal_goals_leave_current_focus(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    create_goal("Terminal objective", goal_id="goal-terminal", cfg=cfg)
    update_goal("goal-terminal", status="COMPLETE", cfg=cfg)
    goal = get_goal("goal-terminal", cfg)
    assert goal["status"] == "COMPLETE"
    assert goal["is_current"] is False
    with pytest.raises(GoalError) as exc:
        update_goal("goal-terminal", status="ACTIVE", cfg=cfg)
    assert exc.value.status == "ERROR_GOAL_TRANSITION"


def test_goal_cli_set_show_and_update_use_same_persistence_contract(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    assert main(
        [
            "goal",
            "set",
            "Ship",
            "the",
            "agent",
            "workflow",
            "--goal-id",
            "goal-cli",
            "--active-vector",
            "product_parity",
            "--format",
            "json",
        ],
        cfg=cfg,
    ) == 0
    assert '"status": "GOAL_CREATED"' in capsys.readouterr().out
    assert main(["goal", "update", "goal-cli", "--status", "PAUSED", "--format", "json"], cfg=cfg) == 0
    assert '"status": "GOAL_UPDATED"' in capsys.readouterr().out
    assert main(["goal", "show", "goal-cli", "--format", "json"], cfg=cfg) == 0
    assert '"status": "PAUSED"' in capsys.readouterr().out
