from __future__ import annotations

import re
from typing import Any

from .config import Settings, settings

AGENT_PLAN_SCHEMA = "mucaro-agent-plan/1.0"
AGENT_PLANNER_ID = "mucaro-local-intent-v1"
MAX_AGENT_ACTIONS = 4
_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def _action(tool: str, arguments: dict[str, Any], reason: str, confidence: str = "MEDIUM") -> dict[str, Any]:
    return {"tool": tool, "arguments": arguments, "reason": reason, "confidence": confidence}


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

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in actions:
        key = (candidate["tool"], repr(sorted(candidate["arguments"].items())))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(candidate)
    bounded = deduplicated[:MAX_AGENT_ACTIONS]
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
        },
        "limitations": [
            "This local planner recognizes only explicit, bounded intent patterns; it does not infer hidden permissions or autonomously broaden scope.",
            "Every selected action still enters the existing approval-gated tool runtime.",
            "Model-native tool planning and durable multi-agent delegation remain separate future capabilities.",
        ],
    }
