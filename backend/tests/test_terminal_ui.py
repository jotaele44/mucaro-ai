from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import work_runs
from backend.app.cli import main
from backend.app.config import Settings
from backend.app.conversations import create_conversation
from backend.app.terminal_projection import build_terminal_snapshot
from backend.app.terminal_ui import TerminalController, TerminalUIError, render_terminal_snapshot
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, ToolRuntimeError
from backend.app.work_runs import get_run
from backend.tests.test_api import make_settings


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


@pytest.fixture(autouse=True)
def inline_runs():
    work_runs.set_execution_mode("inline")
    yield
    work_runs.set_execution_mode("thread")


def github_issues_tool() -> ToolDefinition:
    return ToolDefinition(
        name="github.issues_list",
        description="terminal fixture",
        mutates_state=False,
        requires_approval=True,
        network="remote",
        handler=lambda arguments, cfg: {
            "status": "GITHUB_ISSUES_OK",
            "retrieved_utc": "2026-10-03T22:00:00Z",
            "repository": arguments["repo"],
            "state": arguments.get("state", "open"),
            "page": 1,
            "per_page": 30,
            "returned_count": 1,
            "total_count": 1,
            "incomplete_results": False,
            "search_result_ceiling": 1000,
            "has_more": False,
            "issues": [
                {
                    "repository": arguments["repo"],
                    "number": 7,
                    "title": "Fixture issue",
                    "state": "open",
                }
            ],
            "limitations": [],
        },
        autopilot_success_statuses=("GITHUB_ISSUES_OK",),
    )


def test_rendered_terminal_frame_closes_width_and_height(cfg: Settings) -> None:
    conversation = create_conversation("Terminal frame", cfg)
    snapshot = build_terminal_snapshot(
        conversation_id=conversation["conversation_id"],
        view="current",
        cfg=cfg,
    )

    frame = render_terminal_snapshot(
        snapshot,
        width=92,
        height=28,
        input_buffer="test prompt",
        mode="interactive",
        notice="ready",
    )

    lines = frame.splitlines()
    assert len(lines) == 28
    assert all(len(line) == 92 for line in lines)
    assert "MÚCARO AI" in frame
    assert "Ctrl-C stop/exit" in frame
    assert "> test prompt" in frame


def test_terminal_projection_reads_governed_github_receipt_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Settings,
) -> None:
    monkeypatch.setitem(TOOL_REGISTRY, "github.issues_list", github_issues_tool())
    controller = TerminalController(cfg=cfg)
    controller.ensure_conversation()
    controller.request_github("issues", "owner/repo")

    pending = controller.snapshot()["pending_tool_calls"]
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "github.issues_list"

    controller.approve_latest()
    snapshot = controller.snapshot()

    assert snapshot["view"] == "issues"
    assert snapshot["github"]["retrieved_utc"] == "2026-10-03T22:00:00Z"
    assert snapshot["github"]["result"]["issues"][0]["number"] == 7
    assert snapshot["invariants"]["github_projection_from_governed_tool_receipts"] is True
    assert snapshot["invariants"]["remote_fetch_performed_by_projection"] is False


def test_terminal_controller_exposes_sessions_and_plan_mode(cfg: Settings) -> None:
    first = create_conversation("First", cfg)
    second = create_conversation("Second", cfg)
    controller = TerminalController(
        cfg=cfg,
        conversation_id=second["conversation_id"],
        mode="plan",
    )

    controller.submit("How should this task be approached?")
    snapshot = controller.snapshot()

    assert snapshot["conversation_id"] == second["conversation_id"]
    assert snapshot["current_run"]["mode"] == "plan"
    assert snapshot["current_run"]["outcome"] == "PLAN_ONLY"
    assert isinstance(snapshot["work_plan"], dict)

    controller.set_view("sessions")
    sessions = controller.snapshot()["sessions"]
    assert {row["conversation_id"] for row in sessions} == {
        first["conversation_id"],
        second["conversation_id"],
    }


def test_terminal_autopilot_mode_refuses_unbound_natural_language(cfg: Settings) -> None:
    controller = TerminalController(cfg=cfg, mode="autopilot")
    controller.ensure_conversation()

    with pytest.raises(TerminalUIError) as exc:
        controller.submit("Implement everything automatically")

    assert exc.value.status == "ERROR_TUI_AUTOPILOT_PROMPT_UNBOUND"


def test_terminal_view_commands_are_bounded(cfg: Settings) -> None:
    controller = TerminalController(cfg=cfg)
    controller.ensure_conversation()

    controller.command("view sessions")
    assert controller.view == "sessions"
    controller.command("current")
    assert controller.view == "current"
    controller.command("mode auto")
    assert controller.mode == "auto"

    with pytest.raises(TerminalUIError) as exc:
        controller.command("view impossible")
    assert exc.value.status == "ERROR_TUI_VIEW"


def test_headless_tui_snapshot_cli_needs_no_tty(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    conversation = create_conversation("Headless", cfg)

    code = main(
        [
            "tui",
            "--snapshot",
            "--conversation",
            conversation["conversation_id"],
            "--width",
            "88",
            "--height",
            "24",
        ],
        cfg,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "MÚCARO AI" in captured.out
    assert "Headless" in captured.out
    assert len(captured.out.rstrip("\n").splitlines()) == 24


def test_agent_alias_renders_same_headless_surface(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    conversation = create_conversation("Alias", cfg)

    code = main(
        [
            "agent",
            "--snapshot",
            "--conversation",
            conversation["conversation_id"],
            "--width",
            "88",
            "--height",
            "24",
        ],
        cfg,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "MÚCARO AI" in captured.out
    assert "Alias" in captured.out


def test_terminal_resolves_pending_work_run_question_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Settings,
) -> None:
    attempts = {"count": 0}

    def failing(arguments, settings):
        attempts["count"] += 1
        raise ToolRuntimeError("ERROR_TOOL_FIXTURE", "fixture failure")

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.fail",
        ToolDefinition(
            name="test.fail",
            description="failure fixture",
            mutates_state=False,
            requires_approval=True,
            handler=failing,
        ),
    )
    monkeypatch.setitem(
        work_runs.COMMANDS,
        "/fail",
        {"tool": "test.fail", "usage": "/fail", "arg": None},
    )
    conversation = create_conversation("Interactive", cfg)
    controller = TerminalController(
        cfg=cfg,
        conversation_id=conversation["conversation_id"],
        mode="auto",
    )

    controller.submit("/fail")
    waiting = controller.snapshot()
    assert waiting["current_run"]["state"] == "AWAITING_INPUT"
    interaction = waiting["pending_interaction"]
    assert interaction["status"] == "PENDING"
    assert {row["option_id"] for row in interaction["options"]} == {
        "retry_failed",
        "continue",
        "stop",
    }

    controller.command("answer continue")
    completed = get_run(waiting["current_run"]["run_id"], cfg)
    assert completed["state"] == "COMPLETED"
    assert completed["outcome"] == "ACTIONS_COMPLETED"
    assert controller.snapshot()["pending_interaction"] is None
    assert attempts["count"] == 1
