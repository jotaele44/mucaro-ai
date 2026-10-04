"""`mucaro code` command group: read-only coding workspace operations.

Every command is permission-gated by the coding workspace layer and never
mutates the repository; patch proposals are validated, not applied.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from .coding_agent import AgentError, list_sessions, load_session, provider_chat, run_agent, save_session, scripted_chat
from .coding_checks import CodingCheck, CodingCheckError, build_coding_check_suite_plan, execute_coding_check_suite
from .coding_lifecycle import (
    CodingLifecycleError,
    build_pull_request_request,
    commit_approved_changes,
    create_work_branch_transaction,
)
from .coding_mutation import CodingMutationError, apply_patch_transaction
from .coding_patch import validate_patch_proposal
from .coding_remote import CodingRemoteError, create_pull_request, load_request, push_branch
from .coding_review import CodingReviewError, build_code_review_receipt
from .coding_workspace import (
    CodingWorkspaceError,
    inspect_repository,
    inspect_repository_diff,
    read_repository_file,
)
from .config import Settings
from .event_stream import EventSink, EventSinkError
from .model_router import ModelProviderError
from .operator_audit import CLI_ACTOR, ApprovalDecision, OperatorAuditError, approval_mode, audit_decision, run_audited
from .plugins import PluginError, activate, plugin_profile

HANDLED_ERRORS = (
    CodingWorkspaceError,
    CodingReviewError,
    CodingMutationError,
    CodingLifecycleError,
    CodingCheckError,
    CodingRemoteError,
    AgentError,
    ModelProviderError,
    OperatorAuditError,
    PluginError,
)


def _emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for key, value in payload.items():
        if key in {"content", "diff"} and isinstance(value, str):
            print(f"{key}:\n{value}")
        elif not isinstance(value, (dict, list)):
            print(f"{key}: {value}")


def _fail(exc: Exception, output_format: str) -> int:
    status = getattr(exc, "status", "ERROR_CODING")
    detail = getattr(exc, "detail", str(exc))
    payload = {"status": status, "detail": detail}
    if output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"{status} {detail}", file=sys.stderr)
    return 2


def _streaming_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "stream", False)) and args.format == "json"


def _render_event(event: dict[str, Any]) -> str | None:
    """One progress line for a person, or None for events the readable view does not show."""
    kind, step = event["event"], event.get("step")
    if kind == "tool_call":
        return f"[step {step}] {event['tool']} {event['summary']}"
    if kind == "approval":
        return f"[step {step}]   {'approved' if event['approved'] else 'declined'} ({event['mode']})"
    if kind == "tool_result":
        signals = f"  (injection signals: {', '.join(event['injection_signals'])})" if event.get("injection_signals") else ""
        return f"[step {step}]   -> {event['status']}{signals}"
    if kind == "action_invalid":
        return f"[step {step}] model reply rejected: {event['status']}"
    if kind == "check_start":
        return f"[check {event['check_id']}] {' '.join(event['command'])}"
    if kind == "check_result":
        return f"[check {event['check_id']}]   -> {event['state']}"
    return None


def _event_sink(args: argparse.Namespace, *, human: bool = False) -> EventSink | None:
    """The consumer for --stream: NDJSON on stdout for --format json, readable lines on stderr otherwise."""
    if not getattr(args, "stream", False):
        return None
    if args.format == "json" and not human:
        return lambda event: print(json.dumps(event, ensure_ascii=False), flush=True)

    def show(event: dict[str, Any]) -> None:
        line = _render_event(event)
        if line:
            print(line, file=sys.stderr, flush=True)

    return show


def _closed_pipe_exit() -> int:
    """A reader that went away (for example `| head`) is not worth a traceback; stop quietly, but not with success."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # so the interpreter's exit flush stays quiet
    except (OSError, ValueError, AttributeError):
        pass  # stdout is not a real file descriptor (captured or embedded); there is nothing more to detach
    return 1


def _run(action: Any, args: argparse.Namespace) -> int:
    try:
        payload = action()
        if payload is not None:
            _emit(payload, args.format)
        return 0
    except EventSinkError as exc:
        if isinstance(exc.cause, BrokenPipeError):
            return _closed_pipe_exit()
        raise exc.cause from None
    except BrokenPipeError:
        return _closed_pipe_exit()
    except HANDLED_ERRORS as exc:
        if _streaming_json(args):
            try:  # a stdout reader of the event stream should see how the run ended
                print(json.dumps({"event": "error", "status": getattr(exc, "status", "ERROR_CODING"), "detail": getattr(exc, "detail", str(exc))}, ensure_ascii=False), flush=True)
            except BrokenPipeError:
                pass
        return _fail(exc, args.format)


def run_code_status(args: argparse.Namespace, cfg: Settings) -> int:
    return _run(lambda: inspect_repository(args.repo, cfg=cfg), args)


def run_code_read(args: argparse.Namespace, cfg: Settings) -> int:
    return _run(lambda: read_repository_file(args.repo, args.path, cfg=cfg, max_bytes=args.max_bytes), args)


def run_code_diff(args: argparse.Namespace, cfg: Settings) -> int:
    return _run(lambda: inspect_repository_diff(args.repo, cfg=cfg, base=args.base, max_bytes=args.max_bytes), args)


def _read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def run_code_check_patch(args: argparse.Namespace, cfg: Settings) -> int:
    return _run(lambda: validate_patch_proposal(args.repo, _read_text(args.patch_file), cfg=cfg), args)


def run_code_review(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        findings = json.loads(_read_text(args.findings_file))
        return build_code_review_receipt(args.repo, findings, cfg=cfg)

    return _run(action, args)


def _confirm(args: argparse.Namespace, summary: str) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        raise CodingWorkspaceError("ERROR_CODING_CONFIRMATION_REQUIRED", f"{summary}. Re-run with --yes after reviewing it.")
    print(f"{summary}. Proceed? [y/N] ", end="", file=sys.stderr)
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _require_approval(args: argparse.Namespace, cfg: Settings, tool_name: str, tool_args: dict[str, Any], summary: str) -> str:
    """Ask the operator, audit the decision (including a refusal) in the hash-chained tool log, and return the call id."""
    mode = approval_mode(args.yes)
    try:
        approved = _confirm(args, summary)
    except CodingWorkspaceError:
        audit_decision(cfg, tool_name, tool_args, approved=False, requested_by=CLI_ACTOR, mode="denied_non_tty")
        raise
    call_id = audit_decision(cfg, tool_name, tool_args, approved=approved, requested_by=CLI_ACTOR, mode=mode)
    if not approved:
        raise CodingWorkspaceError("ERROR_CODING_APPROVAL_DENIED", "Operator declined the change.")
    return call_id


def run_code_apply(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        patch = Path(args.patch_file).read_text(encoding="utf-8")
        proposal = validate_patch_proposal(args.repo, patch, cfg=cfg)
        if not proposal["applicable"]:
            raise CodingWorkspaceError("ERROR_CODING_PATCH_NOT_APPLICABLE", proposal.get("git_apply_detail") or "patch rejected")
        summary = f"Apply patch touching {', '.join(proposal['patch_paths'])}"
        call_id = _require_approval(args, cfg, "coding.patch_apply", {"patch": patch}, summary)
        return run_audited(
            cfg,
            "coding.patch_apply",
            call_id,
            CLI_ACTOR,
            lambda: apply_patch_transaction(
                args.repo,
                patch,
                expected_head_sha=proposal["head_sha"],
                expected_patch_sha256=proposal["patch_sha256"],
                expected_preimages=proposal["preimages"],
                cfg=cfg,
            ),
        )

    return _run(action, args)


def run_code_branch(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        head = inspect_repository(args.repo, cfg=cfg)["head_sha"]
        call_id = _require_approval(args, cfg, "coding.create_branch", {"name": args.name}, f"Create branch {args.name} at {head[:12]}")
        return run_audited(
            cfg,
            "coding.create_branch",
            call_id,
            CLI_ACTOR,
            lambda: create_work_branch_transaction(args.repo, branch_name=args.name, expected_head_sha=head, cfg=cfg),
        )

    return _run(action, args)


def run_code_commit(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        state = inspect_repository(args.repo, cfg=cfg)
        summary = f"Commit {', '.join(args.path)} on {state['branch']}"
        call_id = _require_approval(args, cfg, "coding.commit", {"paths": args.path, "message": args.message}, summary)
        return run_audited(
            cfg,
            "coding.commit",
            call_id,
            CLI_ACTOR,
            lambda: commit_approved_changes(
                args.repo,
                expected_head_sha=state["head_sha"],
                expected_branch=state["branch"],
                approved_paths=args.path,
                message=args.message,
                cfg=cfg,
            ),
        )

    return _run(action, args)


def _parse_checks(specs: list[str]) -> list[CodingCheck]:
    checks = []
    for spec in specs:
        head, sep, command = spec.partition("=")
        kind, _, check_id = head.partition(":")
        if not sep or not check_id or not command.strip():
            raise CodingCheckError("ERROR_CODING_CHECK_SPEC", f"expected kind:id=command, got {spec!r}")
        checks.append(CodingCheck(check_id=check_id, kind=kind, command=tuple(shlex.split(command))))
    return checks


def _checks_target(args: argparse.Namespace, cfg: Settings) -> tuple[str, list[str], list[CodingCheck], dict[str, str] | None]:
    """(image, check specs, parsed checks, plugin provenance) from --profile, or from --image with --check."""
    if args.profile:
        if args.image or args.check:
            raise CodingCheckError("ERROR_CODING_CHECK_SPEC", "--profile replaces --image and --check; give one or the other")
        plugin, separator, profile = args.profile.partition(":")
        if not separator or not profile:
            raise CodingCheckError("ERROR_CODING_CHECK_SPEC", "--profile looks like PLUGIN:PROFILE")
        image, checks, provenance = plugin_profile(cfg, plugin, profile)
        return image, [f"{check.kind}:{check.check_id}={' '.join(check.command)}" for check in checks], checks, provenance
    if not args.image or not args.check:
        raise CodingCheckError("ERROR_CODING_CHECK_SPEC", "give --image and at least one --check, or --profile PLUGIN:PROFILE")
    return args.image, args.check, _parse_checks(args.check), None


# Exit status for the check suite, so `code checks --run && git push` cannot mistake a failed or blocked suite for success.
CHECK_SUITE_EXIT_CODES = {"PASS": 0, "FAIL": 1, "BLOCKED": 3}


def _checks_exit_code(payload: dict[str, Any]) -> int:
    """0 only for a suite that passed (or a plan that is ready); 1 for a failing check; 3 when infrastructure blocked it."""
    execution = payload.get("execution_status")
    if execution != "NOT_EXECUTED":  # a run: anything that is not a recognised outcome is not a pass
        return CHECK_SUITE_EXIT_CODES.get(str(execution), 1)
    return 3 if payload.get("status") == "CODING_CHECK_SUITE_PLAN_BLOCKED" else 0


def run_code_checks(args: argparse.Namespace, cfg: Settings) -> int:
    outcome: dict[str, Any] = {}

    def action() -> dict[str, Any]:
        image, specs, checks, provenance = _checks_target(args, cfg)
        if not args.run:
            outcome["payload"] = build_coding_check_suite_plan(args.repo, image=image, checks=checks, cfg=cfg, engine=args.engine)
            return outcome["payload"]
        via = f" with engine {args.engine}" if args.engine else ""
        source = f", from plugin {provenance['plugin']} {provenance['version']}" if provenance else ""
        summary = f"Run {len(checks)} check(s) in container image {image}{via}{source} (network disabled)"
        tool_args = {"image": image, "checks": specs, "engine": args.engine, "plugin": provenance}
        call_id = _require_approval(args, cfg, "coding.checks_run", tool_args, summary)
        sink = _event_sink(args)
        payload = run_audited(
            cfg,
            "coding.checks_run",
            call_id,
            CLI_ACTOR,
            lambda: execute_coding_check_suite(args.repo, image=image, checks=checks, cfg=cfg, on_event=sink, engine=args.engine),
        )
        outcome["payload"] = payload
        if _streaming_json(args):
            print(json.dumps({"event": "result", **payload}, ensure_ascii=False), flush=True)
            return None
        return payload

    code = _run(action, args)
    return _checks_exit_code(outcome["payload"]) if code == 0 and "payload" in outcome else code


def run_code_pr_request(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        state = inspect_repository(args.repo, cfg=cfg)
        request = build_pull_request_request(
            args.repo,
            expected_head_sha=state["head_sha"],
            head_branch=state["branch"],
            base_branch=args.base,
            title=args.title,
            body=args.body,
            cfg=cfg,
        )
        if args.output:  # preserved byte for byte as printed: its request_sha256 is what push and pr-create bind to
            target = Path(args.output)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        return request

    return _run(action, args)


def run_code_push(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        request = load_request(args.request)
        plan = push_branch(args.repo, request, cfg=cfg, dry_run=True)  # every local check runs before anyone is asked
        if args.dry_run:
            return plan
        summary = f"Push {plan['branch']} ({plan['head_sha'][:12]}) to github.com/{plan['repo_slug']} with `{' '.join(plan['argv'])}` (never forced)"
        tool_args = {"request_sha256": request["request_sha256"], "repo_slug": plan["repo_slug"], "branch": plan["branch"]}
        call_id = _require_approval(args, cfg, "coding.push", tool_args, summary)
        return run_audited(cfg, "coding.push", call_id, CLI_ACTOR, lambda: push_branch(args.repo, request, cfg=cfg))

    return _run(action, args)


def run_code_pr_create(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        request = load_request(args.request)
        draft = not args.ready
        plan = create_pull_request(args.repo, request, cfg=cfg, draft=draft, dry_run=True)
        if args.dry_run:
            return plan
        kind = "draft pull request" if draft else "pull request"
        summary = (
            f"Open a {kind} on github.com/{plan['repo_slug']}: {request['head_branch']} -> {request['base_branch']}, "
            f"titled {request['title']!r} with a {len(request['body'].encode('utf-8'))}-byte body, using your gh login (head {plan['head_sha'][:12]})"
        )
        tool_args = {"request_sha256": request["request_sha256"], "repo_slug": plan["repo_slug"], "draft": draft}
        call_id = _require_approval(args, cfg, "coding.pr_create", tool_args, summary)
        return run_audited(cfg, "coding.pr_create", call_id, CLI_ACTOR, lambda: create_pull_request(args.repo, request, cfg=cfg, draft=draft))

    return _run(action, args)


def _agent_approver(args: argparse.Namespace) -> Any:
    def approve(tool: str, tool_args: dict[str, Any], description: str) -> ApprovalDecision:
        if args.yes:
            return ApprovalDecision(True, "blanket_yes")
        if not sys.stdin.isatty():
            return ApprovalDecision(False, "denied_non_tty")
        while True:
            print(f"Approve {description}? [y/N, v = view the full arguments] ", end="", file=sys.stderr)
            answer = sys.stdin.readline().strip().lower()
            if answer == "v":
                print(json.dumps(tool_args, indent=2, ensure_ascii=False), file=sys.stderr)
                continue
            return ApprovalDecision(answer in {"y", "yes"}, "interactive")

    return approve


def _agent_chat(args: argparse.Namespace) -> Any:
    if args.script:
        replies = json.loads(Path(args.script).read_text(encoding="utf-8"))
        return scripted_chat([item if isinstance(item, str) else json.dumps(item) for item in replies])
    return provider_chat(args.provider, args.model)


def _agent_turn(
    args: argparse.Namespace,
    cfg: Settings,
    goal: str,
    chat: Any,
    approve: Any,
    repo: str,
    history: Any,
    on_event: EventSink | None = None,
    guidance: str = "",
    plugins: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    result = run_agent(
        goal, repo, chat=chat, approve=approve, cfg=cfg, max_steps=args.max_steps, history=history,
        allow_live_search=args.allow_live_search, on_event=on_event, guidance=guidance, active_plugins=plugins,
    )
    if args.session:
        result["session_path"] = str(save_session(cfg, args.session, repo, result))
    return result


def _agent_resume(args: argparse.Namespace, cfg: Settings) -> tuple[str, Any]:
    if not args.session:
        return args.repo, None
    try:
        saved = load_session(cfg, args.session)
    except AgentError as exc:
        if exc.status != "ERROR_AGENT_SESSION_NOT_FOUND":
            raise
        return args.repo, None
    return saved["repo"], saved["messages"]


def run_code_agent(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        repo, history = _agent_resume(args, cfg)
        guidance, plugins = activate(cfg, args.plugin)  # verified before anything runs; a tampered plugin stops the run
        result = _agent_turn(
            args, cfg, args.goal, _agent_chat(args), _agent_approver(args), repo, history,
            on_event=_event_sink(args), guidance=guidance, plugins=plugins,
        )
        result["resumed"] = history is not None
        if args.format == "json":
            result.pop("messages", None)
        if _streaming_json(args):
            print(json.dumps({"event": "result", **result}, ensure_ascii=False), flush=True)
            return None
        return result

    return _run(action, args)


def run_code_chat(args: argparse.Namespace, cfg: Settings) -> int:
    try:
        repo, history = _agent_resume(args, cfg)
        chat, approve = _agent_chat(args), _agent_approver(args)
        guidance, plugins = activate(cfg, args.plugin)
    except HANDLED_ERRORS as exc:
        return _fail(exc, args.format)
    print("mucaro code chat - enter a goal per line; /exit to quit.", file=sys.stderr)
    sink = _event_sink(args, human=True)  # the REPL is for people: --stream shows progress lines whatever --format says
    for line in sys.stdin:
        goal = line.strip()
        if goal in {"/exit", "/quit"}:
            break
        if not goal:
            continue
        try:
            result = _agent_turn(args, cfg, goal, chat, approve, repo, history, on_event=sink, guidance=guidance, plugins=plugins)
            guidance = ""  # given once per run of the REPL; the history carries it from then on
            history = result["messages"]
            print(result["final"] or f"[{result['status']}]")
        except EventSinkError as exc:
            if isinstance(exc.cause, BrokenPipeError):
                return _closed_pipe_exit()
            raise exc.cause from None
        except BrokenPipeError:
            return _closed_pipe_exit()
        except HANDLED_ERRORS as exc:
            _fail(exc, args.format)
    return 0


def run_code_sessions(args: argparse.Namespace, cfg: Settings) -> int:
    return _run(lambda: {"status": "AGENT_SESSIONS_OK", "sessions": list_sessions(cfg)}, args)


def register_code_commands(subcommands: Any) -> None:
    code = subcommands.add_parser("code", help="Inspect, diff, review, and validate patches in a permitted Git repository.")
    sub = code.add_subparsers(dest="code_command", required=True)

    def add(name: str, help_text: str, handler: Any) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("--repo", default=".", help="Repository path (requires a directory permission grant).")
        parser.add_argument("--format", choices=["json", "text"], default="json", help="Output format.")
        parser.set_defaults(handler=handler)
        return parser

    add("status", "Report Git HEAD, branch, and dirty state.", run_code_status)

    read = add("read", "Read a repository file within size limits.", run_code_read)
    read.add_argument("path", help="Repository-relative file path.")
    read.add_argument("--max-bytes", type=int, default=None, help="Bounded read ceiling.")

    diff = add("diff", "Show the working-tree diff against a base ref.", run_code_diff)
    diff.add_argument("--base", default="HEAD", help="Local Git ref or commit.")
    diff.add_argument("--max-bytes", type=int, default=None, help="Bounded diff ceiling.")

    check = add("check-patch", "Validate a unified diff without applying it.", run_code_check_patch)
    check.add_argument("patch_file", help="Patch file path, or - for stdin.")

    review = add("review", "Build a review receipt from a JSON findings file.", run_code_review)
    review.add_argument("findings_file", help="JSON list of findings, or - for stdin.")

    def confirmed(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--yes", action="store_true", help="Approve this change without an interactive prompt.")

    apply = add("apply", "Apply a validated patch transactionally after approval.", run_code_apply)
    apply.add_argument("patch_file", help="Patch file path.")
    confirmed(apply)

    branch = add("branch", "Create a work branch from a clean HEAD after approval.", run_code_branch)
    branch.add_argument("name", help="New branch name.")
    confirmed(branch)

    commit = add("commit", "Commit explicitly approved paths after approval.", run_code_commit)
    commit.add_argument("--path", action="append", required=True, help="Repository-relative path to commit; repeatable.")
    commit.add_argument("-m", "--message", required=True, help="Commit message.")
    confirmed(commit)

    agent = add("agent", "Run an approval-gated model-driven coding loop toward a goal.", run_code_agent)
    agent.add_argument("goal", help="What the agent should accomplish.")
    agent.add_argument("--provider", default="local", help="Model provider: local or anthropic.")
    agent.add_argument("--model", default=None, help="Model name override.")
    agent.add_argument("--script", help="JSON list of model replies for deterministic replay (no network).")
    agent.add_argument("--max-steps", type=int, default=12, help="Maximum tool turns (1-30).")
    agent.add_argument("--session", help="Session id: resume it if present, and save progress to it.")
    agent.add_argument(
        "--allow-live-search", action="store_true", help="Let the agent ask to use live Brave web search (each call still needs approval)."
    )
    agent.add_argument(
        "--stream",
        action="store_true",
        help="Emit step events as they happen: NDJSON on stdout for --format json (ending with a result event), readable lines on stderr for --format text.",
    )
    agent.add_argument(
        "--plugin", action="append", default=[], metavar="NAME",
        help="Activate an installed plugin for this run: its guidance reaches the model and its MCP servers become callable (repeatable). Never grants tools or approvals.",
    )
    confirmed(agent)

    chat = add("chat", "Interactive multi-turn coding loop reading goals from stdin.", run_code_chat)
    for flag, default, help_text in (("--provider", "local", "Model provider: local or anthropic."), ("--model", None, "Model name override."), ("--script", None, "JSON list of model replies (offline replay).")):
        chat.add_argument(flag, default=default, help=help_text)
    chat.add_argument("--max-steps", type=int, default=12, help="Maximum tool turns per goal (1-30).")
    chat.add_argument("--session", help="Session id to resume and persist.")
    chat.add_argument(
        "--allow-live-search", action="store_true", help="Let the agent ask to use live Brave web search (each call still needs approval)."
    )
    chat.add_argument("--stream", action="store_true", help="Show each step as it happens (progress lines on stderr).")
    chat.add_argument("--plugin", action="append", default=[], metavar="NAME", help="Activate an installed plugin for this session (repeatable).")
    confirmed(chat)

    checks = add("checks", "Plan (default) or run (--run) containerized checks with network disabled.", run_code_checks)
    checks.add_argument("--check", action="append", help="kind:id=command, e.g. test:unit='pytest -q'; repeatable. Needs --image.")
    checks.add_argument("--image", help="Pinned container image. Needs --check.")
    checks.add_argument("--profile", metavar="PLUGIN:PROFILE", help="Use an installed plugin's check profile (its pinned image and checks) instead of --image and --check.")
    checks.add_argument(
        "--engine",
        help="Container engine binary (docker or podman), for example /Applications/Docker.app/Contents/Resources/bin/docker when it is not on PATH.",
    )
    checks.add_argument("--run", action="store_true", help="Execute the checks instead of only planning them.")
    checks.add_argument("--stream", action="store_true", help="With --run, emit check_start and check_result events as each check finishes (same formats as the agent).")
    confirmed(checks)

    pr = add("pr-request", "Emit a governed pull-request request artifact for the current clean branch (no network).", run_code_pr_request)
    pr.add_argument("--base", default="main", help="Base branch.")
    pr.add_argument("--title", required=True, help="PR title.")
    pr.add_argument("--body", default="", help="PR body.")
    pr.add_argument("--output", help="Also write the request artifact to this file, for `code push` and `code pr-create` to bind to.")

    push = add("push", "Push the reviewed work branch to origin (approval; bound to a pr-request artifact; never forced).", run_code_push)
    push.add_argument("--request", required=True, help="Artifact written by `code pr-request --output`.")
    push.add_argument("--dry-run", action="store_true", help="Run every local check and show the exact command without pushing.")
    confirmed(push)

    pr_create = add(
        "pr-create",
        "Open a draft pull request for a pushed branch with your gh login (approval; bound to a pr-request artifact).",
        run_code_pr_create,
    )
    pr_create.add_argument("--request", required=True, help="Artifact written by `code pr-request --output`.")
    pr_create.add_argument("--ready", action="store_true", help="Open it ready for review instead of as a draft.")
    pr_create.add_argument("--dry-run", action="store_true", help="Run every local check and show the exact command without creating anything.")
    confirmed(pr_create)

    add("sessions", "List saved agent sessions.", run_code_sessions)
