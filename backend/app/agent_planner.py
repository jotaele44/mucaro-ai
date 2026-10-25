from __future__ import annotations

import re
from typing import Any

from .agent_workflow import AgentWorkflowError, normalize_agent_actions
from .config import Settings, settings

AGENT_PLAN_SCHEMA = "mucaro-agent-plan/1.0"
AGENT_PLANNER_ID = "mucaro-local-intent-v1"
MAX_AGENT_ACTIONS = 4
_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_REPO_PATH_RE = re.compile(
    r"`([^`]+)`|(?<![\w./-])((?:backend|frontend|scripts|docs|tests|schemas|_architecture)/[\w./-]+)"
)
_CODING_INTENT_TERMS = (
    "implement",
    "fix",
    "refactor",
    "debug",
    "edit",
    "modify",
    "change the code",
    "write code",
    "run tests",
    "run the tests",
    "lint",
    "typecheck",
    "pytest",
    "coding task",
)


def _action(
    tool: str,
    arguments: dict[str, Any],
    reason: str,
    confidence: str = "MEDIUM",
    *,
    step_id: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {"tool": tool, "arguments": arguments, "reason": reason, "confidence": confidence}
    if step_id is not None:
        action["id"] = step_id
    if depends_on is not None:
        action["depends_on"] = depends_on
    return action


def _explicit_repo_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in _REPO_PATH_RE.finditer(text):
        candidate = match.group(1) or match.group(2)
        if not candidate or candidate.startswith("/") or ".." in candidate.split("/"):
            continue
        if candidate not in paths:
            paths.append(candidate)
    return paths[:MAX_AGENT_ACTIONS]


def _has_coding_intent(lowered: str, paths: list[str]) -> bool:
    return bool(paths) or any(term in lowered for term in _CODING_INTENT_TERMS)


def build_agent_plan(prompt: str, *, cfg: Settings = settings) -> dict[str, Any]:
    """Create a bounded local plan from explicit user intent.

    This planner never executes a tool and never interprets source/model text as
    instructions. It is deliberately conservative: an unrecognized request
    produces no action and remains a normal cited-answer request.
    """
    text = " ".join(prompt.split())
    lowered = text.casefold()
    actions: list[dict[str, Any]] = []
    urls = _URL_RE.findall(text)
    repo_paths = _explicit_repo_paths(text)

    if any(term in lowered for term in ("source status", "source library", "ingested sources", "index status", "how many sources")):
        actions.append(_action("source_library.status", {}, "Inspect the local source library and index before answering.", "HIGH"))
    if any(term in lowered for term in ("readiness", "product audit", "audit the app", "release readiness")):
        actions.append(_action("product.audit", {}, "Inspect current local product-readiness evidence before answering.", "HIGH"))
    if any(term in lowered for term in ("my memory", "remembered preferences", "memory context")):
        actions.append(_action("memory.list", {"include_disabled": False}, "Inspect enabled local memory before answering.", "HIGH"))
    if any(term in lowered for term in ("workspace profiles", "available workspaces")):
        actions.append(_action("workspace_profiles.list", {}, "Inspect available local workspace profiles.", "HIGH"))

    if urls and any(term in lowered for term in ("fetch", "open", "read", "retrieve", "visit")):
        actions.extend(_action("web.fetch", {"url": url}, "Fetch the explicitly supplied URL through governed web policy.", "HIGH") for url in urls[:MAX_AGENT_ACTIONS])
    if any(term in lowered for term in ("web search", "search the web", "look online", "browse for")):
        query = re.sub(r"https?://[^\s<>()]+", "", text, flags=re.IGNORECASE).strip()
        if query:
            actions.append(_action("web.search", {"query": query, "allow_live_search": True}, "Search the web only because the request explicitly asks for web discovery.", "MEDIUM"))

    if any(term in lowered for term in ("git status", "repository status", "repo status")):
        actions.append(_action("coding.repo_status", {"repo_root": str(cfg.project_root)}, "Inspect the configured repository without mutation.", "MEDIUM"))

    if _has_coding_intent(lowered, repo_paths):
        actions.append(
            _action(
                "coding.repo_status",
                {"repo_root": str(cfg.project_root)},
                "Establish the exact repository HEAD and dirty candidate set before any coding inspection.",
                "HIGH",
                step_id="coding-status",
            )
        )
        if any(term in lowered for term in ("diff", "changes", "changed files", "review the code", "review changes")):
            actions.append(
                _action(
                    "coding.diff",
                    {"repo_root": str(cfg.project_root), "base": "HEAD"},
                    "Inspect current staged, unstaged, and untracked changes without mutation.",
                    "HIGH",
                    step_id="coding-diff",
                    depends_on=["coding-status"],
                )
            )
        if any(term in lowered for term in ("read", "open", "show", "inspect")):
            for index, path in enumerate(repo_paths[:2], start=1):
                actions.append(
                    _action(
                        "coding.read_file",
                        {"repo_root": str(cfg.project_root), "path": path},
                        "Read the explicitly named repository file as bounded, hash-bound evidence.",
                        "HIGH",
                        step_id=f"coding-read-{index}",
                        depends_on=["coding-status"],
                    )
                )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in actions:
        key = (candidate["tool"], repr(sorted(candidate["arguments"].items())))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(candidate)
    try:
        bounded = normalize_agent_actions(deduplicated[:MAX_AGENT_ACTIONS], max_actions=MAX_AGENT_ACTIONS)
    except AgentWorkflowError as exc:
        return {
            "schema_version": AGENT_PLAN_SCHEMA,
            "planner": AGENT_PLANNER_ID,
            "status": "AGENT_PLAN_BLOCKED",
            "prompt": text,
            "actions": [],
            "counts": {"candidate_actions": len(deduplicated), "selected_actions": 0, "max_actions": MAX_AGENT_ACTIONS},
            "error": {"status": exc.status, "detail": exc.detail},
            "invariants": {"planner_does_not_execute": True, "blocked_fail_closed": True},
        }
    return {
        "schema_version": AGENT_PLAN_SCHEMA,
        "planner": AGENT_PLANNER_ID,
        "status": "AGENT_PLAN_READY" if bounded else "AGENT_NO_SAFE_ACTIONS",
        "prompt": text,
        "actions": bounded,
        "counts": {"candidate_actions": len(deduplicated), "selected_actions": len(bounded), "max_actions": MAX_AGENT_ACTIONS},
        "invariants": {
            "planner_does_not_execute": True,
            "actions_are_bounded": len(bounded) <= MAX_AGENT_ACTIONS,
            "duplicate_actions_removed": len(bounded) == len({(row["tool"], repr(sorted(row["arguments"].items()))) for row in bounded}),
            "unrecognized_intent_fails_closed": True,
            "workflow_is_topologically_ordered": True,
        },
        "limitations": [
            "This local planner recognizes only explicit, bounded intent patterns; it does not infer hidden permissions or autonomously broaden scope.",
            "Coding intent adds read-only repository preflight, diff, and explicitly named file inspection only; it never proposes patch application or execution by itself.",
            "Every selected action still enters the existing approval-gated tool runtime.",
            "Model-native tool planning and durable multi-agent delegation remain separate future capabilities.",
        ],
    }
