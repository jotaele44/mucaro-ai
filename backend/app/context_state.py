from __future__ import annotations

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
        "context_state_id": stable_id(
            "context",
            str(conversation.get("conversation_id", "")),
            str(len(turns)),
            "|".join(str(turn.get("turn_id", "")) for turn in turns),
            length=16,
        ),
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
