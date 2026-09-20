from __future__ import annotations

from copy import deepcopy

from backend.app.context_state import build_context_state, build_model_context


def _conversation(count: int = 12):
    return {
        "conversation_id": "conv-1",
        "turns": [
            {"turn_id": f"turn-{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " + ("x" * 20), "status": "DONE"}
            for i in range(count)
        ],
    }


def test_compaction_preserves_raw_history_and_turn_identity() -> None:
    conversation = _conversation()
    before = deepcopy(conversation)
    state = build_context_state(conversation, recent_turn_limit=4, recent_char_limit=1000, summary_char_limit=1000)

    assert conversation == before
    assert state["raw_turn_count"] == 12
    assert state["historical_turn_count"] == 8
    assert state["recent_turn_count"] == 4
    assert state["raw_turn_ids"] == [f"turn-{i}" for i in range(12)]
    assert [row["turn_id"] for row in state["compacted_turn_refs"]] == [f"turn-{i}" for i in range(8)]
    assert state["policy"]["raw_history_mutated"] is False
    assert state["coverage"] == {
        "represented_historical_turns": 8,
        "total_historical_turns": 8,
        "ratio": 1.0,
    }


def test_model_context_contains_compacted_history_and_recent_turns() -> None:
    state = build_context_state(_conversation(), recent_turn_limit=4, recent_char_limit=1000, summary_char_limit=1000)
    context = build_model_context(state)

    assert context[0]["status"] == "COMPACTED_CONTEXT"
    assert "turn-0" in context[0]["content"]
    assert len(context) == 5
    assert context[-1]["content"].startswith("message 11")


def test_compaction_is_deterministic_except_created_at() -> None:
    conversation = _conversation()
    left = build_context_state(conversation)
    right = build_context_state(conversation)

    assert left["context_state_id"] == right["context_state_id"]
    left.pop("created_at")
    right.pop("created_at")
    assert left == right


def test_summary_limit_does_not_delete_unrepresented_raw_turns() -> None:
    conversation = _conversation(20)
    state = build_context_state(conversation, recent_turn_limit=2, summary_char_limit=40)

    assert len(state["compacted_turn_refs"]) < state["historical_turn_count"]
    assert state["raw_turn_count"] == len(conversation["turns"])
    assert state["raw_turn_ids"] == [turn["turn_id"] for turn in conversation["turns"]]
    assert state["coverage"]["represented_historical_turns"] < state["coverage"]["total_historical_turns"]
    assert 0 <= state["coverage"]["ratio"] < 1
