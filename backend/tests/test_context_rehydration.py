from __future__ import annotations

from copy import deepcopy

import pytest

from backend.app.context_state import (
    ContextStateError,
    build_context_state,
    rehydrate_context_state,
    validate_context_state,
)


def _conversation(count: int = 20):
    return {
        "conversation_id": "conv-rehydrate",
        "turns": [
            {
                "turn_id": f"turn-{i}",
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"message {i} " + ("x" * 40),
                "status": "DONE",
                "run_id": f"run-{i}" if i % 2 else None,
            }
            for i in range(count)
        ],
    }


def _without_created_at(state: dict) -> dict:
    result = deepcopy(state)
    result.pop("created_at", None)
    return result


def test_rehydration_rebuilds_lossy_summary_from_canonical_turns() -> None:
    conversation = _conversation()
    state = build_context_state(
        conversation,
        recent_turn_limit=3,
        recent_char_limit=180,
        summary_char_limit=55,
    )
    assert state["coverage"]["ratio"] < 1
    assert len(state["compacted_turn_refs"]) < state["historical_turn_count"]

    rebuilt = rehydrate_context_state(conversation, state)

    assert _without_created_at(rebuilt) == _without_created_at(state)
    assert rebuilt["raw_turn_ids"] == [turn["turn_id"] for turn in conversation["turns"]]
    assert rebuilt["policy"] == state["policy"]


def test_rehydration_rejects_stale_state_after_new_turn() -> None:
    conversation = _conversation()
    state = build_context_state(conversation)
    conversation["turns"].append({
        "turn_id": "turn-new",
        "role": "user",
        "content": "new canonical turn",
        "status": "USER_MESSAGE",
    })

    with pytest.raises(ContextStateError) as exc:
        rehydrate_context_state(conversation, state)
    assert exc.value.status == "ERROR_CONTEXT_STALE"


def test_rehydration_rejects_cross_conversation_state() -> None:
    conversation = _conversation()
    state = build_context_state(conversation)
    other = deepcopy(conversation)
    other["conversation_id"] = "conv-other"

    with pytest.raises(ContextStateError) as exc:
        validate_context_state(other, state)
    assert exc.value.status == "ERROR_CONTEXT_CONVERSATION"


def test_summary_tampering_does_not_become_canonical_history() -> None:
    conversation = _conversation()
    state = build_context_state(conversation, summary_char_limit=80)
    state["compacted_summary"] = "INJECTED NONCANONICAL SUMMARY"

    rebuilt = rehydrate_context_state(conversation, state)

    assert rebuilt["compacted_summary"] != "INJECTED NONCANONICAL SUMMARY"
    assert "message 0" in rebuilt["compacted_summary"]


def test_rehydration_rejects_turn_reordering_even_when_count_is_equal() -> None:
    conversation = _conversation()
    state = build_context_state(conversation)
    conversation["turns"][0], conversation["turns"][1] = conversation["turns"][1], conversation["turns"][0]

    with pytest.raises(ContextStateError) as exc:
        rehydrate_context_state(conversation, state)
    assert exc.value.status == "ERROR_CONTEXT_STALE"


def test_rehydration_rejects_tampered_turn_content_with_unchanged_ids() -> None:
    conversation = _conversation()
    state = build_context_state(conversation)
    conversation["turns"][2]["content"] = "silently rewritten history"
    with pytest.raises(ContextStateError) as exc:
        validate_context_state(conversation, state)
    assert exc.value.status == "ERROR_CONTEXT_IDENTITY"


def test_rehydration_rejects_tampered_turn_role_with_unchanged_ids() -> None:
    conversation = _conversation()
    state = build_context_state(conversation)
    conversation["turns"][1]["role"] = "system"
    with pytest.raises(ContextStateError) as exc:
        rehydrate_context_state(conversation, state)
    assert exc.value.status == "ERROR_CONTEXT_IDENTITY"


def test_context_identity_is_stable_for_unchanged_conversation() -> None:
    conversation = _conversation()
    assert build_context_state(conversation)["context_state_id"] == build_context_state(deepcopy(conversation))["context_state_id"]


def test_context_identity_keeps_turn_fields_apart() -> None:
    # Moving text across the role/content boundary is a rewrite too, even around separator characters.
    shifted, original = _conversation(), _conversation()
    shifted["turns"][1].update(role="assistant\x1fmessage", content="1")
    original["turns"][1].update(role="assistant", content="message\x1f1")
    assert build_context_state(shifted)["context_state_id"] != build_context_state(original)["context_state_id"]
