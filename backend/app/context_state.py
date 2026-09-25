from __future__ import annotations

import hashlib
from typing import Any

from .utils import stable_id, utc_now

CONTEXT_STATE_SCHEMA_VERSION = "mucaro-context-state/1.0"
DEFAULT_RECENT_TURNS = 8
DEFAULT_RECENT_CHARS = 2400
DEFAULT_SUMMARY_CHARS = 4000


class ContextStateError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _normalized_content(turn: dict[str, Any]) -> str:
    return " ".join(str(turn.get("content", "")).split())


def _turn_ref(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_id": turn.get("turn_id"),
        "role": turn.get("role", "unknown"),
        "run_id": turn.get("run_id"),
        "status": turn.get("status", ""),
    }


def _turn_digest(turn: dict[str, Any]) -> str:
    # Bind identity to what the turn says, not just its id: a rewritten turn must not validate.
    payload = "\x1f".join(str(turn.get(key, "")) for key in ("turn_id", "role", "content"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context_identity(conversation: dict[str, Any]) -> str:
    turns = list(conversation.get("turns", []))
    return stable_id(
        "context",
        str(conversation.get("conversation_id", "")),
        str(len(turns)),
        "|".join(_turn_digest(turn) for turn in turns),
        length=16,
    )


def build_context_state(
    conversation: dict[str, Any],
    *,
    recent_turn_limit: int = DEFAULT_RECENT_TURNS,
    recent_char_limit: int = DEFAULT_RECENT_CHARS,
    summary_char_limit: int = DEFAULT_SUMMARY_CHARS,
) -> dict[str, Any]:
    if recent_turn_limit <= 0 or recent_char_limit <= 0 or summary_char_limit <= 0:
        raise ContextStateError("ERROR_CONTEXT_LIMIT", "context limits must be positive")
    turns = list(conversation.get("turns", []))
    split_at = max(0, len(turns) - recent_turn_limit)
    historical = turns[:split_at]
    recent = turns[split_at:]

    recent_context: list[dict[str, Any]] = []
    remaining = recent_char_limit
    for turn in reversed(recent):
        content = _normalized_content(turn)
        if not content:
            continue
        clipped = content[:remaining]
        if not clipped:
            break
        recent_context.append({**_turn_ref(turn), "content": clipped})
        remaining -= len(clipped)
        if remaining <= 0:
            break
    recent_context.reverse()

    # Deterministic extractive compaction: canonical raw turns remain untouched.
    # The compacted state contains bounded excerpts plus references back to raw turn IDs.
    summary_parts: list[str] = []
    summary_refs: list[dict[str, Any]] = []
    remaining_summary = summary_char_limit
    for turn in historical:
        content = _normalized_content(turn)
        if not content:
            continue
        prefix = f"[{turn.get('role', 'unknown')}:{turn.get('turn_id', 'unknown')}] "
        available = max(0, remaining_summary - len(prefix))
        excerpt = content[:available]
        if not excerpt:
            break
        summary_parts.append(prefix + excerpt)
        summary_refs.append(_turn_ref(turn))
        remaining_summary -= len(prefix) + len(excerpt)
        if remaining_summary <= 0:
            break

    now = utc_now()
    return {
        "schema_version": CONTEXT_STATE_SCHEMA_VERSION,
        "context_state_id": _context_identity(conversation),
        "conversation_id": conversation.get("conversation_id"),
        "created_at": now,
        "raw_turn_count": len(turns),
        "historical_turn_count": len(historical),
        "recent_turn_count": len(recent_context),
        "recent_context": recent_context,
        "compacted_summary": "\n".join(summary_parts),
        "compacted_turn_refs": summary_refs,
        "raw_turn_ids": [turn.get("turn_id") for turn in turns],
        "coverage": {
            "represented_historical_turns": len(summary_refs),
            "total_historical_turns": len(historical),
            "ratio": (len(summary_refs) / len(historical)) if historical else 1.0,
        },
        "policy": {
            "recent_turn_limit": recent_turn_limit,
            "recent_char_limit": recent_char_limit,
            "summary_char_limit": summary_char_limit,
            "algorithm": "deterministic_extractive_v1",
            "raw_history_mutated": False,
        },
    }


def validate_context_state(conversation: dict[str, Any], context_state: dict[str, Any]) -> None:
    if context_state.get("schema_version") != CONTEXT_STATE_SCHEMA_VERSION:
        raise ContextStateError("ERROR_CONTEXT_SCHEMA", "unsupported context state schema")
    if context_state.get("conversation_id") != conversation.get("conversation_id"):
        raise ContextStateError("ERROR_CONTEXT_CONVERSATION", "context state belongs to a different conversation")
    expected_turn_ids = [turn.get("turn_id") for turn in conversation.get("turns", [])]
    if context_state.get("raw_turn_ids") != expected_turn_ids:
        raise ContextStateError("ERROR_CONTEXT_STALE", "canonical conversation turns changed after context state creation")
    if context_state.get("context_state_id") != _context_identity(conversation):
        raise ContextStateError("ERROR_CONTEXT_IDENTITY", "context state identity does not match canonical conversation")


def rehydrate_context_state(
    conversation: dict[str, Any],
    context_state: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild a bounded context state from canonical raw turns using the frozen policy.

    The compacted summary is never treated as canonical history. Rehydration fails
    closed if the supplied state is stale or belongs to another conversation.
    """
    validate_context_state(conversation, context_state)
    policy = context_state.get("policy", {})
    rebuilt = build_context_state(
        conversation,
        recent_turn_limit=int(policy.get("recent_turn_limit", DEFAULT_RECENT_TURNS)),
        recent_char_limit=int(policy.get("recent_char_limit", DEFAULT_RECENT_CHARS)),
        summary_char_limit=int(policy.get("summary_char_limit", DEFAULT_SUMMARY_CHARS)),
    )
    return rebuilt


def build_model_context(context_state: dict[str, Any]) -> list[dict[str, Any]]:
    context: list[dict[str, Any]] = []
    summary = str(context_state.get("compacted_summary", "")).strip()
    if summary:
        context.append({
            "role": "system",
            "content": "Compacted prior conversation state (not source evidence):\n" + summary,
            "status": "COMPACTED_CONTEXT",
            "run_id": None,
        })
    context.extend(
        {
            "role": turn.get("role", "unknown"),
            "content": turn.get("content", ""),
            "status": turn.get("status", ""),
            "run_id": turn.get("run_id"),
        }
        for turn in context_state.get("recent_context", [])
    )
    return context
