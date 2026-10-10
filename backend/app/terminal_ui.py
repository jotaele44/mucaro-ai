from __future__ import annotations

import json
import os
import select
import shlex
import shutil
import sys
import textwrap
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from .agent_checkpoints import CheckpointError, get_checkpoint
from .autopilot import AutopilotError, authorize_autopilot, run_autopilot
from .config import Settings, settings
from .conversations import create_conversation, get_conversation
from .permission_leases import (
    PermissionLeaseError,
    authorize_tool_call_with_lease,
    grant_permission_lease,
)
from .response_flow import InteractionError, resolve_interaction
from .terminal_projection import TERMINAL_VIEWS, TerminalProjectionError, build_terminal_snapshot
from .tools import (
    ToolRuntimeError,
    approve_tool_call,
    deny_tool_call,
    request_tool_call,
    run_tool_call,
)
from .work_runs import (
    ACTIVE_STATES,
    RunError,
    get_run,
    resume_waiting_run,
    stop_run,
    submit_message,
)

VIEW_LABELS = {
    "current": "Current",
    "sessions": "Sessions",
    "issues": "Issues",
    "pull_requests": "Pull Requests",
    "gists": "Gists",
}
MODE_MAP = {
    "plan": "plan",
    "interactive": "ask",
    "auto": "auto",
}
CTRL_C = "\x03"
CTRL_G = "\x07"
CTRL_O = "\x0f"
CTRL_L = "\x0c"
CTRL_P = "\x10"
CTRL_R = "\x12"
CTRL_S = "\x13"
ESC = "\x1b"
ENTER = "\r"
LF = "\n"
BACKSPACE = {"\x08", "\x7f"}


class TerminalUIError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _clip(value: Any, width: int) -> str:
    text = " ".join(str(value or "").split())
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _wrap(value: Any, width: int, *, max_lines: int | None = None) -> list[str]:
    text = str(value or "")
    lines: list[str] = []
    for logical in text.splitlines() or [""]:
        wrapped = textwrap.wrap(
            logical,
            width=max(1, width),
            replace_whitespace=False,
            drop_whitespace=True,
        ) or [""]
        lines.extend(wrapped)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = _clip(lines[-1], width)
    return lines


def _metric_text(metric: dict[str, Any] | None) -> str:
    if not isinstance(metric, dict):
        return "?"
    value = metric.get("value")
    if value is None:
        return "?"
    if metric.get("unit") == "ratio":
        return f"{float(value) * 100:.1f}%"
    return str(value)


def _status_line(snapshot: dict[str, Any], mode: str) -> str:
    usage = snapshot.get("context_usage") if isinstance(snapshot.get("context_usage"), dict) else {}
    model = usage.get("model") if isinstance(usage.get("model"), dict) else {}
    metrics = usage.get("metrics") if isinstance(usage.get("metrics"), dict) else {}
    configured = snapshot.get("configured_model") or {}
    provider = (
        model.get("provider")
        or (snapshot.get("checkpoint") or {}).get("provider")
        or configured.get("provider")
        or "no-model"
    )
    name = (
        model.get("name")
        or (snapshot.get("checkpoint") or {}).get("model")
        or configured.get("name")
        or "default"
    )
    consumed = _metric_text(metrics.get("context_consumed_tokens"))
    limit = _metric_text(metrics.get("context_limit_tokens"))
    run = snapshot.get("current_run") or {}
    state = run.get("state") or (snapshot.get("checkpoint") or {}).get("state") or "IDLE"
    repo_status = snapshot.get("repository_status") or {}
    repo_result = repo_status.get("result") if isinstance(repo_status, dict) else {}
    branch = repo_result.get("branch") if isinstance(repo_result, dict) else None
    head_sha = repo_result.get("head_sha") if isinstance(repo_result, dict) else None
    git = f" • {branch or 'detached'}@{str(head_sha)[:8]}" if head_sha else ""
    return f"{provider}/{name} • {mode} • ctx {consumed}/{limit} • {state}{git}"


def _sidebar(snapshot: dict[str, Any], height: int, width: int) -> list[str]:
    view = str(snapshot.get("view") or "current")
    lines = []
    for key in TERMINAL_VIEWS:
        marker = "▶" if key == view else " "
        lines.append(_clip(f"{marker} {VIEW_LABELS[key]}", width))
    lines.append("")
    pending = len(snapshot.get("pending_tool_calls") or [])
    lines.append(_clip(f"Approvals: {pending}", width))
    checkpoint = snapshot.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("checkpoint_id"):
        lines.append(_clip(f"Agent: {checkpoint.get('state')}", width))
    while len(lines) < height:
        lines.append("")
    return lines[:height]


def _render_current(snapshot: dict[str, Any], width: int, height: int) -> list[str]:
    conversation = snapshot.get("conversation") or {}
    turns = list(conversation.get("turns") or [])
    lines = [_clip(str(conversation.get("title") or "No conversation"), width), ""]
    remaining = max(0, height - len(lines))
    rendered: list[str] = []
    for turn in reversed(turns):
        role = str(turn.get("role") or "?").upper()
        content_lines = _wrap(turn.get("content"), max(10, width - 4), max_lines=4)
        block = [f"{role}: {content_lines[0] if content_lines else ''}"]
        block.extend(f"    {line}" for line in content_lines[1:])
        block.append("")
        if len(rendered) + len(block) > remaining:
            break
        rendered = block + rendered
    lines.extend(rendered)
    if not turns:
        lines.append("No turns yet. Type a prompt below.")
    return lines[:height]


def _render_sessions(snapshot: dict[str, Any], width: int, height: int) -> list[str]:
    lines = ["Sessions", ""]
    selected = snapshot.get("conversation_id")
    for session in snapshot.get("sessions") or []:
        marker = "▶" if session.get("conversation_id") == selected else " "
        row = (
            f"{marker} {_clip(session.get('title'), max(8, width - 28))} "
            f"[{session.get('turn_count', 0)}] {session.get('last_status', '')}"
        )
        lines.append(_clip(row, width))
        if len(lines) >= height:
            break
    if len(lines) == 2:
        lines.append("No saved sessions.")
    return lines[:height]


def _github_rows(snapshot: dict[str, Any], width: int, height: int) -> list[str]:
    view = snapshot.get("view")
    github = snapshot.get("github")
    label = VIEW_LABELS.get(str(view), str(view))
    if not isinstance(github, dict):
        return [
            label,
            "",
            "No governed GitHub snapshot yet.",
            "Use :issues OWNER/REPO, :prs OWNER/REPO, or :gists OWNER.",
        ]
    result = github.get("result") or {}
    retrieved = github.get("retrieved_utc") or "unknown"
    lines = [f"{label}  retrieved={retrieved}", ""]
    if view == "issues":
        for row in result.get("issues") or []:
            lines.append(_clip(f"#{row.get('number')} [{row.get('state')}] {row.get('title')}", width))
    elif view == "pull_requests":
        for row in result.get("pull_requests") or []:
            lines.append(
                _clip(
                    f"#{row.get('number')} [{row.get('state')}] {row.get('title')} "
                    f"{row.get('head_ref') or '?'}@{str(row.get('head_sha') or '')[:8]} → {row.get('base_ref') or '?'}",
                    width,
                )
            )
    elif view == "gists":
        for row in result.get("gists") or []:
            lines.append(_clip(f"{row.get('id')} {row.get('description') or '(no description)'}", width))
    if len(lines) == 2:
        lines.append("Snapshot returned no rows.")
    return lines[:height]


def _render_plan(snapshot: dict[str, Any], width: int, height: int) -> list[str]:
    lines: list[str] = []
    checkpoint = snapshot.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("checkpoint_id"):
        lines.append(_clip(f"Agent • {checkpoint.get('state')} • {checkpoint.get('objective')}", width))
        dag = checkpoint.get("task_dag")
        if isinstance(dag, dict) and isinstance(dag.get("tasks"), list):
            for task in dag["tasks"][: max(0, height - 2)]:
                lines.append(_clip(f"[{task.get('state')}] {task.get('id')}: {task.get('title')}", width))
    if not lines:
        work_plan = snapshot.get("work_plan")
        if isinstance(work_plan, dict):
            lines.append(_clip("Work plan", width))
            for step in (work_plan.get("steps") or [])[: max(0, height - 1)]:
                if isinstance(step, dict):
                    label = step.get("tool") or step.get("description") or step.get("command") or step
                    lines.append(_clip(f"• {label}", width))
    interaction = snapshot.get("pending_interaction")
    if isinstance(interaction, dict) and len(lines) < height:
        lines.append(_clip(f"Question: {interaction.get('question')}", width))
        for option in interaction.get("options") or []:
            if len(lines) >= height:
                break
            if isinstance(option, dict):
                lines.append(
                    _clip(
                        f"  {option.get('option_id')}: {option.get('label')} — {option.get('consequence')}",
                        width,
                    )
                )
    pending = snapshot.get("pending_tool_calls") or []
    if pending and len(lines) < height:
        lines.append(_clip(f"Approval needed: {pending[-1].get('tool_name')} {pending[-1].get('tool_call_id')}", width))
    if not lines:
        lines.append("No active task DAG or approval.")
    while len(lines) < height:
        lines.append("")
    return lines[:height]


def render_terminal_snapshot(
    snapshot: dict[str, Any],
    *,
    width: int = 120,
    height: int = 40,
    input_buffer: str = "",
    mode: str = "interactive",
    notice: str = "",
) -> str:
    width = max(72, int(width))
    height = max(20, int(height))
    inner_width = width - 2
    sidebar_width = min(20, max(16, inner_width // 6))
    body_width = inner_width - sidebar_width - 1
    plan_height = min(8, max(4, height // 5))
    body_height = max(1, height - plan_height - 9)

    header = _clip(f"MÚCARO AI  {_status_line(snapshot, mode)}", inner_width)
    title_rule = "─" * inner_width
    side = _sidebar(snapshot, body_height, sidebar_width)
    view = snapshot.get("view")
    if view == "current":
        body = _render_current(snapshot, body_width, body_height)
    elif view == "sessions":
        body = _render_sessions(snapshot, body_width, body_height)
    else:
        body = _github_rows(snapshot, body_width, body_height)
    while len(body) < body_height:
        body.append("")

    rows = [f"┌{'─' * inner_width}┐", f"│{header.ljust(inner_width)}│", f"│{title_rule}│"]
    for idx in range(body_height):
        left = _clip(side[idx], sidebar_width).ljust(sidebar_width)
        right = _clip(body[idx], body_width).ljust(body_width)
        rows.append(f"│{left}│{right}│")
    rows.append(f"├{'─' * inner_width}┤")
    for line in _render_plan(snapshot, inner_width, plan_height):
        rows.append(f"│{_clip(line, inner_width).ljust(inner_width)}│")
    rows.append(f"├{'─' * inner_width}┤")
    prompt = _clip(f"> {input_buffer}", inner_width)
    rows.append(f"│{prompt.ljust(inner_width)}│")
    help_text = (
        "Tab views • Ctrl-L current • Ctrl-S sessions • Ctrl-O issues • "
        "Ctrl-R PRs • Ctrl-G gists • Ctrl-P palette • Ctrl-C stop/exit"
    )
    rows.append(f"│{_clip(help_text, inner_width).ljust(inner_width)}│")
    rows.append(f"│{_clip(notice, inner_width).ljust(inner_width)}│")
    rows.append(f"└{'─' * inner_width}┘")
    return "\n".join(rows)


@dataclass
class TerminalController:
    cfg: Settings = settings
    conversation_id: str | None = None
    view: str = "current"
    mode: str = "interactive"
    input_buffer: str = ""
    notice: str = ""
    github_repo: str | None = None
    github_owner: str | None = None

    def ensure_conversation(self) -> str:
        if self.conversation_id and get_conversation(self.conversation_id, self.cfg):
            return self.conversation_id
        created = create_conversation("Terminal session", self.cfg)
        self.conversation_id = str(created["conversation_id"])
        return self.conversation_id

    def snapshot(self) -> dict[str, Any]:
        return build_terminal_snapshot(
            conversation_id=self.conversation_id,
            view=self.view,
            cfg=self.cfg,
        )

    def set_view(self, view: str) -> None:
        if view not in TERMINAL_VIEWS:
            raise TerminalUIError("ERROR_TUI_VIEW", view)
        self.view = view
        self.notice = f"View: {VIEW_LABELS[view]}"

    def cycle_view(self) -> None:
        index = TERMINAL_VIEWS.index(self.view)
        self.set_view(TERMINAL_VIEWS[(index + 1) % len(TERMINAL_VIEWS)])

    def _latest_pending(self) -> dict[str, Any]:
        pending = self.snapshot().get("pending_tool_calls") or []
        if not pending:
            raise TerminalUIError("ERROR_TUI_NO_PENDING_APPROVAL", "No pending tool call in this terminal session.")
        return pending[-1]

    def _resume_work_run_for_call(self, call: dict[str, Any]) -> None:
        run_id = call.get("run_id")
        if not isinstance(run_id, str) or run_id.startswith("tui:"):
            return
        try:
            run = get_run(run_id, self.cfg)
        except RunError:
            return
        if run.get("state") in {"AWAITING_APPROVAL", "AWAITING_INPUT"}:
            resume_waiting_run(run_id, self.cfg)

    def approve_latest(self, *, use_lease: bool = False) -> None:
        call = self._latest_pending()
        try:
            if use_lease:
                approved = authorize_tool_call_with_lease(call["tool_call_id"], cfg=self.cfg)
                call = approved["tool_call"]
            else:
                approved = approve_tool_call(
                    call["tool_call_id"],
                    cfg=self.cfg,
                    approved_by="terminal",
                    reason="terminal allow once",
                    expected_arguments_sha256=call.get("arguments_sha256"),
                )
                call = approved["tool_call"]
            try:
                executed = run_tool_call(call["tool_call_id"], cfg=self.cfg, executed_by="terminal")
                self.notice = f"Executed {call['tool_name']}: {(executed.get('result') or {}).get('status')}"
            except ToolRuntimeError as exc:
                self.notice = f"{exc.status}: {exc.detail}"
            self._resume_work_run_for_call(call)
        except (ToolRuntimeError, PermissionLeaseError) as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc

    def deny_latest(self) -> None:
        call = self._latest_pending()
        try:
            denied = deny_tool_call(
                call["tool_call_id"],
                cfg=self.cfg,
                denied_by="terminal",
                reason="terminal denial",
                expected_arguments_sha256=call.get("arguments_sha256"),
            )["tool_call"]
        except ToolRuntimeError as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc
        self._resume_work_run_for_call(denied)
        self.notice = f"Denied {denied['tool_name']}"

    def request_github(self, view: str, target: str) -> None:
        conversation_id = self.ensure_conversation()
        if view == "issues":
            tool_name = "github.issues_list"
            arguments = {"repo": target, "state": "open", "page": 1, "per_page": 30}
            self.github_repo = target
        elif view == "pull_requests":
            tool_name = "github.prs_list"
            arguments = {"repo": target, "state": "open", "page": 1, "per_page": 30}
            self.github_repo = target
        elif view == "gists":
            tool_name = "github.gists_list"
            arguments = {"owner": target or None, "page": 1, "per_page": 30}
            self.github_owner = target or None
        else:
            raise TerminalUIError("ERROR_TUI_GITHUB_VIEW", view)
        try:
            requested = request_tool_call(
                tool_name,
                arguments,
                cfg=self.cfg,
                requested_by="terminal",
                reason=f"terminal:{view}:refresh",
                run_id=f"tui:{conversation_id}",
            )["tool_call"]
        except ToolRuntimeError as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc
        self.view = view
        self.notice = f"Approval required: {requested['tool_call_id']} ({tool_name})"

    def stop_active(self) -> bool:
        snapshot = self.snapshot()
        run = snapshot.get("current_run")
        if isinstance(run, dict) and run.get("state") in ACTIVE_STATES:
            try:
                stop_run(str(run["run_id"]), reason="TERMINAL_CTRL_C", cfg=self.cfg)
            except RunError as exc:
                raise TerminalUIError(exc.status, exc.detail) from exc
            self.notice = f"Stop requested for {run['run_id']}"
            return True
        return False

    def submit(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if stripped.startswith(":"):
            self.command(stripped[1:])
            return
        if self.mode == "autopilot":
            raise TerminalUIError(
                "ERROR_TUI_AUTOPILOT_PROMPT_UNBOUND",
                "Natural-language Autopilot plan generation is not yet certified; "
                "use :autopilot authorize/run with an existing checkpoint.",
            )
        conversation_id = self.ensure_conversation()
        run_mode = MODE_MAP[self.mode]
        try:
            submitted = submit_message(
                conversation_id,
                stripped,
                mode=run_mode,
                cfg=self.cfg,
            )
        except RunError as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc
        self.view = "current"
        self.notice = f"Run {submitted['run']['run_id']} submitted ({self.mode})"

    def _lease_target_run_id(self) -> str:
        snapshot = self.snapshot()
        current_run = snapshot.get("current_run")
        if isinstance(current_run, dict) and current_run.get("state") in ACTIVE_STATES:
            return str(current_run["run_id"])
        checkpoint = snapshot.get("checkpoint")
        if (
            isinstance(checkpoint, dict)
            and checkpoint.get("run_id")
            and checkpoint.get("state") not in {"COMPLETED", "FAILED"}
        ):
            return str(checkpoint["run_id"])
        return f"tui:{self.ensure_conversation()}"

    def _lease_options(self, tokens: list[str]) -> dict[str, Any]:
        options: dict[str, Any] = {
            "allow_network": False,
            "allow_mutating": False,
            "max_uses": 64,
            "ttl_seconds": 3600,
            "workspace_root": None,
            "arguments_sha256": None,
        }
        for token in tokens:
            if token == "network":
                options["allow_network"] = True
            elif token == "mutating":
                options["allow_mutating"] = True
            elif token.startswith("uses="):
                try:
                    options["max_uses"] = int(token.split("=", 1)[1])
                except ValueError as exc:
                    raise TerminalUIError("ERROR_TUI_LEASE_USES", token) from exc
            elif token.startswith("ttl="):
                raw = token.split("=", 1)[1]
                if raw.lower() == "none":
                    options["ttl_seconds"] = None
                else:
                    try:
                        options["ttl_seconds"] = int(raw)
                    except ValueError as exc:
                        raise TerminalUIError("ERROR_TUI_LEASE_TTL", token) from exc
            elif token.startswith("workspace="):
                options["workspace_root"] = token.split("=", 1)[1]
            elif token.startswith("args="):
                options["arguments_sha256"] = token.split("=", 1)[1]
            else:
                raise TerminalUIError("ERROR_TUI_LEASE_OPTION", token)
        return options

    def _grant_terminal_lease(
        self,
        *,
        scope_type: str,
        tool_names: list[str] | None = None,
        option_tokens: list[str] | None = None,
    ) -> None:
        options = self._lease_options(list(option_tokens or []))
        if scope_type == "RUN_READ_ONLY" and (
            options["allow_mutating"] or options["allow_network"]
        ):
            raise TerminalUIError(
                "ERROR_TUI_LEASE_SCOPE",
                "RUN_READ_ONLY remains local/read-only; use :allow-tool TOOL with explicit network/mutating authority.",
            )
        if (
            options["allow_mutating"]
            and not options["workspace_root"]
            and not options["arguments_sha256"]
        ):
            raise TerminalUIError(
                "ERROR_TUI_LEASE_MUTATION_BINDING",
                "Mutating terminal leases require workspace=PATH or args=SHA256.",
            )
        try:
            granted = grant_permission_lease(
                run_id=self._lease_target_run_id(),
                scope_type=scope_type,
                tool_names=tool_names,
                granted_by="terminal",
                workspace_root=options["workspace_root"],
                arguments_sha256=options["arguments_sha256"],
                allow_mutating=bool(options["allow_mutating"]),
                allow_network=bool(options["allow_network"]),
                ttl_seconds=options["ttl_seconds"],
                max_uses=int(options["max_uses"]),
                cfg=self.cfg,
            )
        except PermissionLeaseError as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc
        lease = granted["lease"]
        authority = []
        if lease.get("allow_network"):
            authority.append("network")
        if lease.get("allow_mutating"):
            authority.append("mutating")
        self.notice = (
            f"Granted {lease['lease_id']} to {lease['run_id']} "
            f"scope={lease['scope_type']} authority={','.join(authority) or 'local-readonly'}"
        )

    def request_governed_tool(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise TerminalUIError(
                "ERROR_TUI_TOOL_ARGUMENTS",
                "Tool arguments must be a JSON object.",
            )
        try:
            requested = request_tool_call(
                tool_name,
                arguments,
                cfg=self.cfg,
                requested_by="terminal",
                reason="terminal generic governed tool request",
                run_id=self._lease_target_run_id(),
            )["tool_call"]
        except ToolRuntimeError as exc:
            raise TerminalUIError(exc.status, exc.detail) from exc
        self.notice = (
            f"Approval required: {requested['tool_call_id']} "
            f"{requested['tool_name']} sha256={requested['arguments_sha256'][:12]}…"
        )

    def command(self, command: str) -> None:
        try:
            parts = shlex.split(command.strip())
        except ValueError as exc:
            raise TerminalUIError("ERROR_TUI_COMMAND_PARSE", str(exc)) from exc
        if not parts:
            return
        name = parts[0].lower()
        args = parts[1:]
        if name in {"current", "sessions", "issues", "prs", "gists"} and not args:
            self.set_view("pull_requests" if name == "prs" else name)
            return
        if name == "view" and len(args) == 1:
            target = "pull_requests" if args[0] == "prs" else args[0]
            self.set_view(target)
            return
        if name == "mode" and len(args) == 1:
            target = args[0].lower()
            if target not in {"plan", "interactive", "auto", "autopilot"}:
                raise TerminalUIError("ERROR_TUI_MODE", target)
            self.mode = target
            self.notice = f"Mode: {target}"
            return
        if name == "new":
            title = " ".join(args) or "Terminal session"
            created = create_conversation(title, self.cfg)
            self.conversation_id = str(created["conversation_id"])
            self.view = "current"
            self.notice = f"Created {self.conversation_id}"
            return
        if name == "open" and len(args) == 1:
            if get_conversation(args[0], self.cfg) is None:
                raise TerminalUIError("ERROR_CONVERSATION_NOT_FOUND", args[0])
            self.conversation_id = args[0]
            self.view = "current"
            self.notice = f"Opened {args[0]}"
            return
        if name == "issues" and len(args) == 1:
            self.request_github("issues", args[0])
            return
        if name == "prs" and len(args) == 1:
            self.request_github("pull_requests", args[0])
            return
        if name == "gists" and len(args) <= 1:
            self.request_github("gists", args[0] if args else "")
            return
        if name == "tool":
            if not args or len(args) > 2:
                raise TerminalUIError(
                    "ERROR_TUI_TOOL_USAGE",
                    'Use :tool TOOL_NAME \'{"key":"value"}\' or :tool TOOL_NAME for {}.',
                )
            tool_name = args[0]
            if len(args) == 1:
                arguments = {}
            else:
                try:
                    arguments = json.loads(args[1])
                except json.JSONDecodeError as exc:
                    raise TerminalUIError("ERROR_TUI_TOOL_JSON", str(exc)) from exc
                if not isinstance(arguments, dict):
                    raise TerminalUIError(
                        "ERROR_TUI_TOOL_ARGUMENTS",
                        "Tool arguments must be a JSON object.",
                    )
            self.request_governed_tool(tool_name, arguments)
            return
        if name == "allow-run":
            self._grant_terminal_lease(
                scope_type="RUN_READ_ONLY",
                option_tokens=args,
            )
            return
        if name == "allow-tool" and args:
            self._grant_terminal_lease(
                scope_type="EXACT_TOOLS",
                tool_names=[args[0]],
                option_tokens=args[1:],
            )
            return
        if name == "approve":
            self.approve_latest(use_lease=bool(args and args[0].lower() == "lease"))
            return
        if name == "deny":
            self.deny_latest()
            return
        if name in {"answer", "answer-text"}:
            snapshot = self.snapshot()
            interaction = snapshot.get("pending_interaction")
            if not isinstance(interaction, dict):
                raise TerminalUIError("ERROR_TUI_NO_PENDING_INTERACTION", "No pending operator question.")
            if name == "answer" and len(args) == 1:
                option_id = args[0]
                freeform = None
            elif name == "answer-text" and args:
                option_id = None
                freeform = " ".join(args)
            else:
                raise TerminalUIError(
                    "ERROR_TUI_INTERACTION_RESPONSE",
                    "Use :answer OPTION_ID or :answer-text FREEFORM.",
                )
            try:
                resolved = resolve_interaction(
                    str(interaction["interaction_id"]),
                    expected_version=int(interaction["version"]),
                    resolved_by="terminal",
                    option_id=option_id,
                    freeform_text=freeform,
                    cfg=self.cfg,
                )
            except InteractionError as exc:
                raise TerminalUIError(exc.status, exc.detail) from exc
            current_run = snapshot.get("current_run")
            if isinstance(current_run, dict) and current_run.get("state") == "AWAITING_INPUT":
                resume_waiting_run(str(current_run["run_id"]), self.cfg)
            resolution = resolved.get("resolution") or {}
            self.notice = f"Resolved question: {resolution.get('option_id') or resolution.get('freeform_text')}"
            return
        if name == "stop":
            if not self.stop_active():
                self.notice = "No active run to stop."
            return
        if name == "autopilot" and len(args) == 2 and args[0] in {"authorize", "run"}:
            checkpoint_id = args[1]
            try:
                checkpoint = get_checkpoint(checkpoint_id, self.cfg)
            except CheckpointError as exc:
                raise TerminalUIError(exc.status, exc.detail) from exc
            if checkpoint is None:
                raise TerminalUIError("ERROR_CHECKPOINT_NOT_FOUND", checkpoint_id)
            try:
                if args[0] == "authorize":
                    result = authorize_autopilot(
                        checkpoint_id,
                        approved_by="terminal",
                        expected_sequence=int(checkpoint["sequence"]),
                        cfg=self.cfg,
                    )
                else:
                    result = run_autopilot(checkpoint_id, cfg=self.cfg)
            except (AutopilotError, CheckpointError) as exc:
                raise TerminalUIError(exc.status, exc.detail) from exc
            self.notice = f"{result['status']} {result.get('reason') or ''}".strip()
            return
        if name == "help":
            self.notice = (
                ":mode plan|interactive|auto|autopilot • :new • :open ID • "
                ":issues REPO • :prs REPO • :gists [OWNER] • :tool TOOL JSON • "
                ":allow-run • :allow-tool TOOL [network] [mutating] [uses=N] [ttl=N] • "
                ":approve [lease] • :deny • "
                ":answer OPTION • :answer-text TEXT • :stop"
            )
            return
        raise TerminalUIError("ERROR_TUI_COMMAND", f"Unknown or invalid command: {command}")


class _PosixInput(AbstractContextManager["_PosixInput"]):
    def __init__(self) -> None:
        import termios

        self.termios = termios
        self.fd = sys.stdin.fileno()
        self.previous: list[Any] | None = None

    def __enter__(self) -> "_PosixInput":
        import tty

        self.previous = self.termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        current = self.termios.tcgetattr(self.fd)
        current[0] &= ~(self.termios.IXON | self.termios.IXOFF)
        current[3] &= ~(self.termios.ISIG | self.termios.IEXTEN)
        self.termios.tcsetattr(self.fd, self.termios.TCSANOW, current)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.previous is not None:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.previous)

    def read_key(self, timeout: float) -> str | None:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        first = sys.stdin.read(1)
        if first != ESC:
            return first
        sequence = first
        while True:
            ready, _, _ = select.select([sys.stdin], [], [], 0.005)
            if not ready:
                break
            sequence += sys.stdin.read(1)
            if len(sequence) >= 8:
                break
        return {
            "\x1b[A": "UP",
            "\x1b[B": "DOWN",
            "\x1b[C": "RIGHT",
            "\x1b[D": "LEFT",
        }.get(sequence, ESC)


class _WindowsInput(AbstractContextManager["_WindowsInput"]):
    def __enter__(self) -> "_WindowsInput":
        self._enable_vt()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    @staticmethod
    def _enable_vt() -> None:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass

    def read_key(self, timeout: float) -> str | None:
        import msvcrt
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key in {"\x00", "\xe0"}:
                    special = msvcrt.getwch()
                    return {"H": "UP", "P": "DOWN", "M": "RIGHT", "K": "LEFT"}.get(special)
                return key
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        return None


def _input_adapter() -> AbstractContextManager[Any]:
    if os.name == "nt":
        return _WindowsInput()
    return _PosixInput()


def run_terminal_ui(
    *,
    cfg: Settings = settings,
    conversation_id: str | None = None,
    view: str = "current",
    mode: str = "interactive",
    refresh_seconds: float = 0.20,
) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TerminalUIError("ERROR_TUI_NOT_TTY", "Full-screen terminal mode requires interactive stdin/stdout.")
    if view not in TERMINAL_VIEWS:
        raise TerminalUIError("ERROR_TUI_VIEW", view)
    if mode not in {"plan", "interactive", "auto", "autopilot"}:
        raise TerminalUIError("ERROR_TUI_MODE", mode)

    controller = TerminalController(
        cfg=cfg,
        conversation_id=conversation_id,
        view=view,
        mode=mode,
    )
    controller.ensure_conversation()
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    sys.stdout.flush()
    try:
        with _input_adapter() as input_adapter:
            while True:
                size = shutil.get_terminal_size((120, 40))
                try:
                    snapshot = controller.snapshot()
                    frame = render_terminal_snapshot(
                        snapshot,
                        width=size.columns,
                        height=size.lines,
                        input_buffer=controller.input_buffer,
                        mode=controller.mode,
                        notice=controller.notice,
                    )
                except (TerminalProjectionError, TerminalUIError) as exc:
                    frame = f"MÚCARO AI\n{exc.status}: {exc.detail}"
                sys.stdout.write("\x1b[H\x1b[2J" + frame)
                sys.stdout.flush()

                key = input_adapter.read_key(refresh_seconds)
                if key is None:
                    continue
                if key == CTRL_C:
                    try:
                        if controller.stop_active():
                            continue
                    except TerminalUIError as exc:
                        controller.notice = f"{exc.status}: {exc.detail}"
                        continue
                    return 0
                if key == ESC:
                    controller.input_buffer = ""
                    controller.notice = ""
                    continue
                if key == CTRL_L:
                    controller.set_view("current")
                    continue
                if key == CTRL_S:
                    controller.set_view("sessions")
                    continue
                if key == CTRL_O:
                    controller.set_view("issues")
                    continue
                if key == CTRL_R:
                    controller.set_view("pull_requests")
                    continue
                if key == CTRL_G:
                    controller.set_view("gists")
                    continue
                if key == CTRL_P:
                    controller.input_buffer = ":"
                    controller.notice = "Command palette"
                    continue
                if key == "\t":
                    controller.cycle_view()
                    continue
                if key in {ENTER, LF}:
                    submitted = controller.input_buffer
                    controller.input_buffer = ""
                    try:
                        controller.submit(submitted)
                    except TerminalUIError as exc:
                        controller.notice = f"{exc.status}: {exc.detail}"
                    continue
                if key in BACKSPACE:
                    controller.input_buffer = controller.input_buffer[:-1]
                    continue
                if isinstance(key, str) and len(key) == 1 and key.isprintable():
                    controller.input_buffer += key
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
