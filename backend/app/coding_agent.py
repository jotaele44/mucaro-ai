"""Approval-gated coding agent loop.

The model proposes one JSON action per turn: {"tool": name, "args": {...}} or
{"final": text}. Read-only tools run directly; mutating tools require the
injected `approve` callback. Every step is recorded in a hash-bound transcript.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .coding_lifecycle import commit_approved_changes, create_work_branch_transaction
from .coding_mutation import apply_patch_transaction
from .coding_patch import validate_patch_proposal
from .coding_workspace import inspect_repository, inspect_repository_diff, read_repository_file
from .config import Settings
from .mcp_client import call_tool as mcp_call_tool
from .model_router import (
    MODEL_PROVIDER_SPECS,
    ModelProviderError,
    _is_loopback_url,
    _local_opener,
    local_model_settings,
    normalize_model_provider,
)
from .web_browsing import fetch_web_snapshot
from .web_search import search_web_brave

AGENT_SCHEMA_VERSION = "mucaro-coding-agent/1.0"
MAX_STEPS_CEILING = 30
READ_ONLY_TOOLS = frozenset({"status", "read", "diff", "check_patch"})
SUBAGENT_MAX_STEPS = 6
MAX_OBSERVATION_CHARS = 12_000
MUTATING_TOOLS = {"apply_patch", "create_branch", "commit", "web_fetch", "web_search", "mcp_call"}  # all need operator approval

SYSTEM_PROMPT = """You are Mucaro's coding agent working in one Git repository.
Reply with exactly one JSON object per turn and nothing else:
  {"tool": "<name>", "args": {...}}   to act, or   {"final": "<summary>"}   when done.
Tools: status {}, read {"path"}, diff {"base"?}, check_patch {"patch"},
apply_patch {"patch"} (needs operator approval), create_branch {"name"} (approval),
commit {"paths": [...], "message"} (approval), web_fetch {"url"} (approval; public pages only, robots respected), web_search {"query"} (approval; requires live-search opt-in and a Brave key),
mcp_call {"server","tool","args"?} (approval; server must be declared in data/mcp_servers.json),
delegate {"goal"} runs a read-only sub-agent (status/read/diff/check_patch only, 6 steps) and returns its findings.
Patches must be unified diffs with `diff --git` headers. Never claim success without evidence from a tool result."""

Chat = Callable[[list[dict[str, str]]], str]
Approve = Callable[[str, dict[str, Any]], bool]


class AgentError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def parse_action(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    try:
        action = json.loads(candidate)
    except ValueError as exc:
        raise AgentError("ERROR_AGENT_ACTION_INVALID", f"model reply is not a JSON object: {exc}") from exc
    if not isinstance(action, dict) or not (isinstance(action.get("final"), str) or isinstance(action.get("tool"), str)):
        raise AgentError("ERROR_AGENT_ACTION_INVALID", "action needs a string 'tool' or 'final'")
    if "args" in action and not isinstance(action["args"], dict):
        raise AgentError("ERROR_AGENT_ACTION_INVALID", "args must be an object")
    return action


def _require(args: dict[str, Any], key: str, kind: type = str) -> Any:
    value = args.get(key)
    if not isinstance(value, kind) or (kind is str and not value):
        raise AgentError("ERROR_AGENT_ARGUMENT_INVALID", f"'{key}' is required")
    return value


def _dispatch(name: str, args: dict[str, Any], repo: Path, cfg: Settings, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    if name == "status":
        return inspect_repository(repo, cfg=cfg)
    if name == "read":
        return read_repository_file(repo, _require(args, "path"), cfg=cfg)
    if name == "diff":
        return inspect_repository_diff(repo, cfg=cfg, base=args.get("base") or "HEAD")
    if name in {"check_patch", "apply_patch"}:
        patch = _require(args, "patch")
        proposal = validate_patch_proposal(repo, patch, cfg=cfg)
        if name == "check_patch":
            return proposal
        if not proposal["applicable"]:
            raise AgentError("ERROR_CODING_PATCH_NOT_APPLICABLE", proposal.get("git_apply_detail") or "patch rejected")
        return apply_patch_transaction(
            repo,
            patch,
            expected_head_sha=proposal["head_sha"],
            expected_patch_sha256=proposal["patch_sha256"],
            expected_preimages=proposal["preimages"],
            cfg=cfg,
        )
    if name == "web_fetch":
        return fetch_web_snapshot(_require(args, "url"), cfg=cfg)
    if name == "web_search":
        return search_web_brave(_require(args, "query"), cfg=cfg, allow_live_search=args.get("allow_live_search") is True)
    if name == "mcp_call":
        arguments = args.get("args") or {}
        return mcp_call_tool(cfg, _require(args, "server"), _require(args, "tool"), arguments)
    if name == "delegate":
        if not ctx or ctx.get("depth", 0) >= 1:
            raise AgentError("ERROR_AGENT_DELEGATION_DEPTH", "sub-agents cannot delegate further")
        sub = run_agent(
            _require(args, "goal"), repo, chat=ctx["chat"], approve=lambda *_: False, cfg=cfg,
            max_steps=SUBAGENT_MAX_STEPS, allowed_tools=READ_ONLY_TOOLS, _depth=ctx["depth"] + 1,
        )
        return {"status": f"SUBAGENT_{sub['status']}", "final": sub["final"], "steps": sub["step_count"], "transcript_sha256": sub["transcript_sha256"]}
    if name == "create_branch":
        head = inspect_repository(repo, cfg=cfg)["head_sha"]
        return create_work_branch_transaction(repo, branch_name=_require(args, "name"), expected_head_sha=head, cfg=cfg)
    if name == "commit":
        state = inspect_repository(repo, cfg=cfg)
        return commit_approved_changes(
            repo,
            expected_head_sha=state["head_sha"],
            expected_branch=state["branch"],
            approved_paths=_require(args, "paths", list),
            message=_require(args, "message"),
            cfg=cfg,
        )
    raise AgentError("ERROR_AGENT_TOOL_UNKNOWN", f"unknown tool: {name}")


def _observe(result: dict[str, Any]) -> str:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_OBSERVATION_CHARS:
        return text[:MAX_OBSERVATION_CHARS] + f'... [truncated {len(text) - MAX_OBSERVATION_CHARS} chars]'
    return text


def run_agent(
    goal: str,
    repo: str | Path,
    *,
    chat: Chat,
    approve: Approve,
    cfg: Settings,
    max_steps: int = 12,
    history: list[dict[str, str]] | None = None,
    allowed_tools: frozenset[str] | None = None,
    _depth: int = 0,
) -> dict[str, Any]:
    if not goal.strip():
        raise AgentError("ERROR_AGENT_GOAL_REQUIRED", "goal must be non-empty")
    if not 1 <= max_steps <= MAX_STEPS_CEILING:
        raise AgentError("ERROR_AGENT_STEPS_INVALID", f"max_steps must be 1..{MAX_STEPS_CEILING}")
    repo_path = Path(repo)
    messages = list(history) if history else [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": goal})
    steps: list[dict[str, Any]] = []
    final: str | None = None
    status = "AGENT_STEP_LIMIT_REACHED"

    for index in range(max_steps):
        reply = chat(messages)
        messages.append({"role": "assistant", "content": reply})
        try:
            action = parse_action(reply)
        except AgentError as exc:
            observation = {"status": exc.status, "detail": exc.detail}
            steps.append({"step": index + 1, "reply": reply, "outcome": observation})
            messages.append({"role": "user", "content": _observe(observation)})
            continue
        if "final" in action and "tool" not in action:
            final, status = action["final"], "AGENT_COMPLETED"
            steps.append({"step": index + 1, "final": final})
            break
        name, args = action["tool"], action.get("args") or {}
        record: dict[str, Any] = {"step": index + 1, "tool": name, "args": args}
        try:
            if allowed_tools is not None and name not in allowed_tools:
                raise AgentError("ERROR_AGENT_TOOL_NOT_ALLOWED", f"{name} is not available to this agent")
            if name in MUTATING_TOOLS and not approve(name, args):
                outcome = {"status": "ERROR_AGENT_APPROVAL_DENIED", "detail": "operator declined; propose an alternative or finish"}
                record["approved"] = False
            else:
                if name in MUTATING_TOOLS:
                    record["approved"] = True
                outcome = _dispatch(name, args, repo_path, cfg, {"chat": chat, "depth": _depth})
        except Exception as exc:  # noqa: BLE001 - tool failures are fed back to the model, never hidden
            outcome = {"status": getattr(exc, "status", "ERROR_AGENT_TOOL_FAILED"), "detail": getattr(exc, "detail", str(exc))}
        record["outcome_status"] = outcome.get("status")
        steps.append(record)
        messages.append({"role": "user", "content": _observe(outcome)})

    body = {"goal": goal, "repo": str(repo_path), "steps": steps, "final": final}
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "status": status,
        **body,
        "step_count": len(steps),
        "messages": messages,
        "transcript_sha256": hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(),
        "limitations": ["model claims in `final` are not verified; rely on tool outcome statuses in `steps`"],
    }


SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _session_path(cfg: Settings, session_id: str) -> Path:
    if not SESSION_ID.fullmatch(session_id):
        raise AgentError("ERROR_AGENT_SESSION_ID_INVALID", "session id must be 1-64 letters, digits, '-' or '_'")
    return cfg.data_dir / "agent_sessions" / f"{session_id}.json"


def load_session(cfg: Settings, session_id: str) -> dict[str, Any]:
    path = _session_path(cfg, session_id)
    if not path.is_file():
        raise AgentError("ERROR_AGENT_SESSION_NOT_FOUND", session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("messages"), list) or not isinstance(data.get("repo"), str):
            raise KeyError("missing messages/repo")
    except (ValueError, KeyError) as exc:
        raise AgentError("ERROR_AGENT_SESSION_CORRUPT", f"{session_id}: {exc}") from exc
    return data


def save_session(cfg: Settings, session_id: str, repo: str, result: dict[str, Any]) -> Path:
    path = _session_path(cfg, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"turns": []}
    turns = [*previous.get("turns", []), {"goal": result["goal"], "status": result["status"], "final": result["final"], "transcript_sha256": result["transcript_sha256"]}]
    payload = {"session_id": session_id, "repo": repo, "messages": result["messages"], "turns": turns}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def list_sessions(cfg: Settings) -> list[dict[str, Any]]:
    root = cfg.data_dir / "agent_sessions"
    rows = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append({"session_id": path.stem, "repo": data.get("repo"), "turns": len(data.get("turns", []))})
        except ValueError:
            rows.append({"session_id": path.stem, "repo": None, "turns": 0, "corrupt": True})
    return rows


def scripted_chat(replies: list[str]) -> Chat:
    queue = list(replies)

    def chat(_: list[dict[str, str]]) -> str:
        if not queue:
            raise AgentError("ERROR_AGENT_SCRIPT_EXHAUSTED", "script has no more replies")
        return queue.pop(0)

    return chat


def provider_chat(provider: str | None, model_name: str | None = None) -> Chat:
    name = normalize_model_provider(provider)
    if name == "local":
        cfg = local_model_settings()
        if not _is_loopback_url(cfg["base_url"]):
            raise ModelProviderError("ERROR_LOCAL_MODEL_URL_NOT_LOOPBACK", cfg["base_url"])
        model = model_name or cfg["model"]

        def chat(messages: list[dict[str, str]]) -> str:
            body = json.dumps({"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 2000}).encode()
            request = urllib.request.Request(
                f"{cfg['base_url']}/chat/completions", data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with _local_opener().open(request, timeout=cfg["timeout_seconds"]) as response:
                    payload = json.loads(response.read(4_000_000).decode("utf-8", errors="replace"))
                return str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise ModelProviderError("ERROR_LOCAL_MODEL_UNAVAILABLE", f"Local model request failed: {exc}") from exc

        return chat
    if name == "anthropic":
        try:
            from anthropic import Anthropic  # type: ignore
        except Exception as exc:
            raise ModelProviderError("ERROR_MODEL_SDK_MISSING", 'python -m pip install -e ".[anthropic]"') from exc
        client = Anthropic()
        model = model_name or os.getenv("MUCARO_AI_MODEL", MODEL_PROVIDER_SPECS["anthropic"].default_model)

        def chat(messages: list[dict[str, str]]) -> str:
            response = client.messages.create(
                model=model, max_tokens=2000, system=messages[0]["content"], messages=messages[1:]
            )
            return "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text")

        return chat
    raise ModelProviderError("ERROR_AGENT_PROVIDER_UNSUPPORTED", "use --provider local or anthropic, or --script for deterministic replays")
