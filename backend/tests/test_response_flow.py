from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.conversations import append_answer_turn, create_conversation
from backend.app.response_flow import (
    ENVELOPE_SCHEMA_VERSION,
    InteractionError,
    ResponseEnvelopeError,
    build_response_envelope,
    create_interaction,
    dismiss_interaction,
    envelope_for_conversation,
    get_pending_interaction,
    list_interactions,
    make_inert_code_part,
    make_question_part,
    make_text_part,
    resolve_interaction,
    validate_response_envelope,
)


def _cfg(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        project_root=tmp_path,
        data_dir=data_dir,
        input_dir=data_dir / "input",
        processed_dir=data_dir / "processed",
        export_dir=data_dir / "exports",
        index_dir=data_dir / "indexes",
        sqlite_path=data_dir / "processed" / "mucaro_ai.sqlite3",
        default_model_provider="mock",
        default_model_name="mock-model",
    )


def _options() -> list[dict[str, str]]:
    return [
        {"option_id": "opt_skip", "label": "Skip it, merge the other 4", "consequence": "Leaves one PR untouched."},
        {"option_id": "opt_retry", "label": "Retry the failed one too", "consequence": "Requests fresh permission for it."},
        {"option_id": "opt_hold", "label": "Hold off on all merges", "consequence": "No merges happen this turn."},
    ]


# --- response envelope -----------------------------------------------------


def test_build_response_envelope_valid_text_part() -> None:
    envelope = build_response_envelope("turn_1", [make_text_part("hello")])
    assert envelope["schema_version"] == ENVELOPE_SCHEMA_VERSION
    assert envelope["turn_id"] == "turn_1"
    assert envelope["parts"] == [{"kind": "text", "text": "hello"}]


def test_validate_response_envelope_rejects_unknown_part_kind() -> None:
    with pytest.raises(ResponseEnvelopeError) as excinfo:
        validate_response_envelope(
            {"schema_version": ENVELOPE_SCHEMA_VERSION, "turn_id": "t1", "parts": [{"kind": "bogus"}]}
        )
    assert excinfo.value.status == "ERROR_ENVELOPE_PART_KIND_INVALID"


def test_validate_response_envelope_rejects_missing_fields() -> None:
    with pytest.raises(ResponseEnvelopeError) as excinfo:
        validate_response_envelope(
            {"schema_version": ENVELOPE_SCHEMA_VERSION, "turn_id": "t1", "parts": [{"kind": "inert_code"}]}
        )
    assert excinfo.value.status == "ERROR_ENVELOPE_PART_FIELDS_MISSING"


def test_inert_code_part_always_marked_inert() -> None:
    part = make_inert_code_part("python", "print(1)")
    assert part["renders_inert"] is True


def test_envelope_for_conversation_reflects_turns_and_pending_question(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    conversation = create_conversation("Merge review", cfg)
    conversation = append_answer_turn(
        conversation["conversation_id"],
        "Merge all",
        {"answer": "Working on it.", "status": "DONE", "run_id": "run_1"},
        cfg,
    )
    create_interaction(conversation["conversation_id"], "Which merges?", _options(), cfg=cfg)

    envelopes = envelope_for_conversation(conversation, cfg)
    # two turns (user + assistant) plus one pending-question envelope
    assert len(envelopes) == 3
    kinds = [part["kind"] for envelope in envelopes for part in envelope["parts"]]
    assert kinds == ["text", "text", "question"]
    question_envelope = envelopes[-1]
    assert question_envelope["parts"][0]["status"] == "PENDING"

    # Building the envelope must not have rewritten the stored answer_trace.
    from backend.app.conversations import get_conversation

    reloaded = get_conversation(conversation["conversation_id"], cfg)
    assistant_turn = next(turn for turn in reloaded["turns"] if turn["role"] == "assistant")
    assert assistant_turn["answer_trace"]["answer"] == "Working on it."


def test_make_question_part_uses_interaction_fields() -> None:
    interaction = {
        "interaction_id": "interaction_abc",
        "version": 1,
        "status": "PENDING",
        "question": "Proceed?",
        "options": [{"option_id": "opt_a", "label": "A", "consequence": None}],
        "allow_freeform": True,
        "resolution": None,
    }
    part = make_question_part(interaction)
    assert part["kind"] == "question"
    assert part["interaction_id"] == "interaction_abc"
    assert part["options"][0]["option_id"] == "opt_a"


# --- pending-interaction state machine -------------------------------------


def test_create_interaction_requires_nonempty_question(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(InteractionError) as excinfo:
        create_interaction("conv_1", "   ", _options(), cfg=cfg)
    assert excinfo.value.status == "ERROR_INTERACTION_QUESTION_REQUIRED"


def test_create_interaction_rejects_duplicate_option_ids(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    options = [
        {"option_id": "opt_a", "label": "A"},
        {"option_id": "opt_a", "label": "A again"},
    ]
    with pytest.raises(InteractionError) as excinfo:
        create_interaction("conv_1", "Pick one", options, cfg=cfg)
    assert excinfo.value.status == "ERROR_INTERACTION_OPTION_DUPLICATE"


def test_only_one_pending_interaction_per_conversation(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    create_interaction("conv_1", "First question?", _options(), cfg=cfg)
    with pytest.raises(InteractionError) as excinfo:
        create_interaction("conv_1", "Second question?", _options(), cfg=cfg)
    assert excinfo.value.status == "ERROR_INTERACTION_ALREADY_PENDING"


def test_dismiss_pending_supersedes_and_allows_new_interaction(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    first = create_interaction("conv_1", "First question?", _options(), cfg=cfg)
    second = create_interaction(
        "conv_1", "Second question?", _options(), dismiss_pending=True, cfg=cfg
    )
    assert second["supersedes_interaction_id"] == first["interaction_id"]

    stored_first = next(i for i in list_interactions("conv_1", cfg) if i["interaction_id"] == first["interaction_id"])
    assert stored_first["status"] == "DISMISSED"
    assert stored_first["resolution"]["reason"] == "SUPERSEDED_BY_NEW_INTERACTION"

    pending = get_pending_interaction("conv_1", cfg)
    assert pending["interaction_id"] == second["interaction_id"]


def test_resolve_by_option_id_not_by_label(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    resolved = resolve_interaction(
        interaction["interaction_id"],
        expected_version=interaction["version"],
        resolved_by="user",
        option_id="opt_retry",
        cfg=cfg,
    )
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolution"] == {"type": "OPTION", "option_id": "opt_retry", "freeform_text": None}


def test_resolve_rejects_unknown_option_id(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            resolved_by="user",
            option_id="Retry ... too",  # a label, not an option_id
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_OPTION_UNKNOWN"


def test_resolve_rejects_stale_version(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    resolve_interaction(
        interaction["interaction_id"],
        expected_version=interaction["version"],
        resolved_by="user",
        option_id="opt_retry",
        cfg=cfg,
    )
    # Second submission still carries the pre-resolution version -> stale.
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            resolved_by="user",
            option_id="opt_skip",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_VERSION_MISMATCH"


def test_resolve_rejects_double_submission_against_resolved_interaction(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    resolved = resolve_interaction(
        interaction["interaction_id"],
        expected_version=interaction["version"],
        resolved_by="user",
        option_id="opt_retry",
        cfg=cfg,
    )
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=resolved["version"],
            resolved_by="user",
            option_id="opt_retry",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_NOT_PENDING"


def test_resolve_rejects_ambiguous_and_empty_responses(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            resolved_by="user",
            option_id="opt_retry",
            freeform_text="Ask everything again",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_AMBIGUOUS_RESPONSE"

    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            resolved_by="user",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_EMPTY_RESPONSE"


def test_resolve_freeform_rejected_when_not_allowed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), allow_freeform=False, cfg=cfg)
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            resolved_by="user",
            freeform_text="Ask everything again",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_FREEFORM_NOT_ALLOWED"


def test_resolve_accepts_freeform_when_allowed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), allow_freeform=True, cfg=cfg)
    resolved = resolve_interaction(
        interaction["interaction_id"],
        expected_version=interaction["version"],
        resolved_by="user",
        freeform_text="Ask everything again",
        cfg=cfg,
    )
    assert resolved["resolution"] == {"type": "FREEFORM", "option_id": None, "freeform_text": "Ask everything again"}


def test_dismiss_interaction_records_reason_and_clears_pending(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    dismissed = dismiss_interaction(
        interaction["interaction_id"],
        expected_version=interaction["version"],
        reason="User started an unrelated topic.",
        dismissed_by="user",
        cfg=cfg,
    )
    assert dismissed["status"] == "DISMISSED"
    assert dismissed["resolution"]["reason"] == "User started an unrelated topic."
    assert get_pending_interaction("conv_1", cfg) is None


def test_dismiss_requires_nonempty_reason(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Which merges?", _options(), cfg=cfg)
    with pytest.raises(InteractionError) as excinfo:
        dismiss_interaction(
            interaction["interaction_id"],
            expected_version=interaction["version"],
            reason="   ",
            dismissed_by="user",
            cfg=cfg,
        )
    assert excinfo.value.status == "ERROR_INTERACTION_DISMISS_REASON_REQUIRED"


def test_resolve_unknown_interaction_id(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(InteractionError) as excinfo:
        resolve_interaction("interaction_missing", expected_version=1, resolved_by="user", option_id="opt_a", cfg=cfg)
    assert excinfo.value.status == "ERROR_INTERACTION_NOT_FOUND"


def test_interruption_then_resume_preserves_original_question(tmp_path: Path) -> None:
    """Mirrors the recording's 12-44s sequence: a question is shown, the user
    sends an unrelated message while it is pending, and resuming must surface
    the *same* interaction rather than a rewritten one."""
    cfg = _cfg(tmp_path)
    interaction = create_interaction("conv_1", "Skip, retry, or hold?", _options(), cfg=cfg)

    # Interruption: caller decides NOT to dismiss, just re-displays on resume.
    resumed = get_pending_interaction("conv_1", cfg)
    assert resumed is not None
    assert resumed["interaction_id"] == interaction["interaction_id"]
    assert resumed["version"] == interaction["version"]

    resolved = resolve_interaction(
        resumed["interaction_id"],
        expected_version=resumed["version"],
        resolved_by="user",
        option_id="opt_retry",
        cfg=cfg,
    )
    assert resolved["status"] == "RESOLVED"
    assert get_pending_interaction("conv_1", cfg) is None
