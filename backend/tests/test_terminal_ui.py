from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import work_runs
from backend.app.cli import main
from backend.app.config import Settings
from backend.app.conversations import create_conversation
from backend.app.permission_leases import grant_permission_lease, list_permission_leases
from backend.app.terminal_projection import build_terminal_snapshot
from backend.app.terminal_ui import TerminalController, TerminalUIError, render_terminal_snapshot
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, ToolRuntimeError, list_tool_calls
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


def test_terminal_lease_approval_consumes_exact_tui_run_scope(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Settings,
) -> None:
    monkeypatch.setitem(TOOL_REGISTRY, "github.issues_list", github_issues_tool())
    controller = TerminalController(cfg=cfg)
    conversation_id = controller.ensure_conversation()
    lease = grant_permission_lease(
        run_id=f"tui:{conversation_id}",
        scope_type="EXACT_TOOLS",
        tool_names=["github.issues_list"],
        granted_by="operator",
        allow_network=True,
        max_uses=1,
        cfg=cfg,
    )["lease"]

    controller.request_github("issues", "owner/repo")
    controller.approve_latest(use_lease=True)

    call = next(
        row
        for row in list_tool_calls(cfg)["tool_calls"]
        if row["tool_name"] == "github.issues_list"
    )
    assert call["status"] == "EXECUTED"
    assert call["approved_by"] == f"lease:{lease['lease_id']}"
    stored = list_permission_leases(run_id=f"tui:{conversation_id}", cfg=cfg)["leases"][0]
    assert stored["used_call_ids"] == [call["tool_call_id"]]
    assert stored["effective_state"] == "EXHAUSTED"


def test_terminal_can_create_bounded_run_and_exact_tool_leases(cfg: Settings) -> None:
    controller = TerminalController(cfg=cfg)
    conversation_id = controller.ensure_conversation()

    controller.command("allow-run uses=3 ttl=600")
    leases = list_permission_leases(run_id=f"tui:{conversation_id}", cfg=cfg)["leases"]
    assert len(leases) == 1
    assert leases[0]["scope_type"] == "RUN_READ_ONLY"
    assert leases[0]["allow_network"] is False
    assert leases[0]["allow_mutating"] is False
    assert leases[0]["max_uses"] == 3

    controller.command("allow-tool github.issues_list network uses=2 ttl=300")
    leases = list_permission_leases(run_id=f"tui:{conversation_id}", cfg=cfg)["leases"]
    exact = next(row for row in leases if row["scope_type"] == "EXACT_TOOLS")
    assert exact["tool_names"] == ["github.issues_list"]
    assert exact["allow_network"] is True
    assert exact["allow_mutating"] is False
    assert exact["max_uses"] == 2


def test_terminal_run_lease_refuses_network_and_mutation_broadening(cfg: Settings) -> None:
    controller = TerminalController(cfg=cfg)
    controller.ensure_conversation()

    with pytest.raises(TerminalUIError) as network:
        controller.command("allow-run network")
    assert network.value.status == "ERROR_TUI_LEASE_SCOPE"

    with pytest.raises(TerminalUIError) as mutating:
        controller.command("allow-run mutating")
    assert mutating.value.status == "ERROR_TUI_LEASE_SCOPE"


def test_terminal_mutating_exact_lease_requires_workspace_or_argument_binding(cfg: Settings) -> None:
    controller = TerminalController(cfg=cfg)
    controller.ensure_conversation()

    with pytest.raises(TerminalUIError) as unbound:
        controller.command("allow-tool coding.patch_apply mutating")
    assert unbound.value.status == "ERROR_TUI_LEASE_MUTATION_BINDING"

    controller.command(
        "allow-tool coding.patch_apply mutating "
        f"args={'a' * 64} uses=1 ttl=60"
    )
    leases = list_permission_leases(run_id=f"tui:{controller.conversation_id}", cfg=cfg)["leases"]
    exact = next(row for row in leases if row["scope_type"] == "EXACT_TOOLS")
    assert exact["allow_mutating"] is True
    assert exact["arguments_sha256"] == "a" * 64
    assert exact["max_uses"] == 1


def test_generic_terminal_tool_request_never_implies_approval(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Settings,
) -> None:
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.echo",
        ToolDefinition(
            name="test.echo",
            description="echo fixture",
            mutates_state=False,
            requires_approval=True,
            handler=lambda arguments, settings: {
                "status": "TEST_ECHO_OK",
                "arguments": arguments,
            },
            autopilot_success_statuses=("TEST_ECHO_OK",),
        ),
    )
    controller = TerminalController(cfg=cfg)
    conversation_id = controller.ensure_conversation()

    controller.command(r'''tool test.echo '{"value":"alpha","count":2}' ''')

    calls = list_tool_calls(cfg)["tool_calls"]
    assert len(calls) == 1
    call = calls[0]
    assert call["tool_name"] == "test.echo"
    assert call["run_id"] == f"tui:{conversation_id}"
    assert call["arguments"] == {"value": "alpha", "count": 2}
    assert call["status"] == "PENDING_APPROVAL"
    assert call["approved_by"] is None
    assert call["executed_at"] is None

    controller.command("approve")
    executed = list_tool_calls(cfg)["tool_calls"][0]
    assert executed["status"] == "EXECUTED"
    assert executed["result"]["status"] == "TEST_ECHO_OK"


@pytest.mark.parametrize(
    "command,status",
    [
        ("tool", "ERROR_TUI_TOOL_USAGE"),
        ("tool test.echo not-json", "ERROR_TUI_TOOL_JSON"),
        ("tool test.echo '[1,2]'", "ERROR_TUI_TOOL_ARGUMENTS"),
        ("tool test.echo '{}' extra", "ERROR_TUI_TOOL_USAGE"),
    ],
)
def test_generic_terminal_tool_request_fails_closed_before_call_creation(
    monkeypatch: pytest.MonkeyPatch,
    cfg: Settings,
    command: str,
    status: str,
) -> None:
    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.echo",
        ToolDefinition(
            name="test.echo",
            description="echo fixture",
            mutates_state=False,
            requires_approval=True,
            handler=lambda arguments, settings: {"status": "TEST_ECHO_OK"},
        ),
    )
    controller = TerminalController(cfg=cfg)
    controller.ensure_conversation()

    with pytest.raises(TerminalUIError) as exc:
        controller.command(command)

    assert exc.value.status == status
    assert list_tool_calls(cfg)["tool_calls"] == []
