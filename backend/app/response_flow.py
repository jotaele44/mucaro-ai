from __future__ import annotations

"""Typed response envelope and pending-interaction (question) state machine.

This module is the implementation target named in the Mucaro AI mobile
interaction-extraction brief (backend/app/response_flow.py, docs/RESPONSE_FLOW.md).
Both paths returned 404 at the inspected baseline commit, so no earlier
"response flow" design was ever merged; this file is a fresh implementation,
not a resurrection of prior work.

It covers two things, kept deliberately separate:

1. A typed response envelope: a UI-facing projection of a conversation turn
   into ordered parts (text, inert_code, question, tool_call, tool_result,
   artifact, task_receipt). Building an envelope never mutates or rewrites
   the underlying turn or its answer_trace -- it is a read-only projection.

2. A pending-interaction (multiple-choice question) ledger: server-owned
   state for "the assistant is waiting on a choice". At most one PENDING
   interaction may exist per conversation at a time. Resolution is always
   by option_id, never by label text, and carries optimistic-concurrency
   version checks so a stale or duplicate submission is rejected rather
   than silently applied twice.
"""

import json
from typing import Any

from .config import Settings, settings
from .db import init_db, list_payloads, upsert_payload
from .utils import stable_id, utc_now

# ---------------------------------------------------------------------------
# Response envelope
# ---------------------------------------------------------------------------

ENVELOPE_SCHEMA_VERSION = "mucaro-response-envelope/1.0"

PART_KIND_TEXT = "text"
PART_KIND_INERT_CODE = "inert_code"
PART_KIND_QUESTION = "question"
PART_KIND_TOOL_CALL = "tool_call"
PART_KIND_TOOL_RESULT = "tool_result"
PART_KIND_ARTIFACT = "artifact"
PART_KIND_TASK_RECEIPT = "task_receipt"

PART_KINDS = {
    PART_KIND_TEXT,
    PART_KIND_INERT_CODE,
    PART_KIND_QUESTION,
    PART_KIND_TOOL_CALL,
    PART_KIND_TOOL_RESULT,
    PART_KIND_ARTIFACT,
    PART_KIND_TASK_RECEIPT,
}

_REQUIRED_PART_FIELDS: dict[str, set[str]] = {
    PART_KIND_TEXT: {"text"},
    PART_KIND_INERT_CODE: {"language", "code", "renders_inert"},
    PART_KIND_QUESTION: {"interaction_id", "version", "status", "question", "options", "allow_freeform"},
    PART_KIND_TOOL_CALL: {"tool_call_id", "tool_name", "status"},
    PART_KIND_TOOL_RESULT: {"tool_call_id", "status"},
    PART_KIND_ARTIFACT: {"artifact_id", "artifact_type", "logical_identity"},
    PART_KIND_TASK_RECEIPT: {"task_id"},
}


class ResponseEnvelopeError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def make_text_part(text: str) -> dict[str, Any]:
    return {"kind": PART_KIND_TEXT, "text": text}


def make_inert_code_part(language: str, code: str) -> dict[str, Any]:
    """Code parts always render inertly: no client executes this text."""
    return {"kind": PART_KIND_INERT_CODE, "language": language or "text", "code": code, "renders_inert": True}


def make_question_part(interaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": PART_KIND_QUESTION,
        "interaction_id": interaction["interaction_id"],
        "version": interaction["version"],
        "status": interaction["status"],
        "question": interaction["question"],
        "options": interaction["options"],
        "allow_freeform": interaction["allow_freeform"],
        "resolution": interaction.get("resolution"),
    }


def make_tool_call_part(tool_call: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": PART_KIND_TOOL_CALL,
        "tool_call_id": tool_call["tool_call_id"],
        "tool_name": tool_call["tool_name"],
        "arguments": tool_call.get("arguments", {}),
        "status": tool_call["status"],
    }


def make_tool_result_part(tool_call: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": PART_KIND_TOOL_RESULT,
        "tool_call_id": tool_call["tool_call_id"],
        "status": tool_call["status"],
        "result": tool_call.get("result"),
    }


def make_artifact_part(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": PART_KIND_ARTIFACT,
        "artifact_id": artifact["artifact_id"],
        "artifact_type": artifact["artifact_type"],
        "logical_identity": artifact["logical_identity"],
        "source_path_or_uri": artifact.get("source_path_or_uri"),
    }


def make_task_receipt_part(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": PART_KIND_TASK_RECEIPT,
        "task_id": task["task_id"],
        "status": task.get("status"),
        "summary": task.get("summary"),
    }


def validate_response_envelope(envelope: dict[str, Any]) -> None:
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise ResponseEnvelopeError("ERROR_ENVELOPE_SCHEMA_VERSION", "Unexpected envelope schema_version.")
    if not envelope.get("turn_id"):
        raise ResponseEnvelopeError("ERROR_ENVELOPE_TURN_ID_REQUIRED", "Envelope turn_id is required.")
    parts = envelope.get("parts")
    if not isinstance(parts, list):
        raise ResponseEnvelopeError("ERROR_ENVELOPE_PARTS_INVALID", "Envelope parts must be a list.")
    for part in parts:
        kind = part.get("kind") if isinstance(part, dict) else None
        if kind not in PART_KINDS:
            raise ResponseEnvelopeError("ERROR_ENVELOPE_PART_KIND_INVALID", f"Unknown part kind: {kind!r}")
        missing = _REQUIRED_PART_FIELDS[kind] - set(part.keys())
        if missing:
            raise ResponseEnvelopeError(
                "ERROR_ENVELOPE_PART_FIELDS_MISSING",
                f"Part kind {kind} missing fields: {sorted(missing)}",
            )


def build_response_envelope(turn_id: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "turn_id": turn_id,
        "created_at": utc_now(),
        "parts": parts,
    }
    validate_response_envelope(envelope)
    return envelope


def envelope_for_conversation(conversation: dict[str, Any], cfg: Settings = settings) -> list[dict[str, Any]]:
    """Project a stored conversation into read-only UI envelopes.

    This never mutates conversation turns or the stored answer_trace; the
    answer_trace remains the canonical evidence record, and the envelope is
    a derived rendering surface only.
    """
    envelopes: list[dict[str, Any]] = []
    for turn in conversation.get("turns", []):
        parts = [make_text_part(str(turn.get("content") or ""))]
        envelopes.append(build_response_envelope(turn["turn_id"], parts))
    pending = get_pending_interaction(conversation.get("conversation_id", ""), cfg)
    if pending is not None:
        envelopes.append(build_response_envelope(pending["interaction_id"], [make_question_part(pending)]))
    return envelopes


# ---------------------------------------------------------------------------
# Pending-interaction (question) state machine
# ---------------------------------------------------------------------------

INTERACTION_TABLE = "interactions"
INTERACTION_STATUSES = {"PENDING", "RESOLVED", "DISMISSED"}


class InteractionError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _normalize_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not options:
        raise InteractionError("ERROR_INTERACTION_OPTIONS_REQUIRED", "Interaction requires at least one option.")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for option in options:
        option_id = str((option or {}).get("option_id") or "").strip()
        label = str((option or {}).get("label") or "").strip()
        if not option_id or not label:
            raise InteractionError("ERROR_INTERACTION_OPTION_INVALID", "Each option requires option_id and label.")
        if option_id in seen_ids:
            raise InteractionError("ERROR_INTERACTION_OPTION_DUPLICATE", f"Duplicate option_id: {option_id}")
        seen_ids.add(option_id)
        consequence = str((option or {}).get("consequence") or "").strip() or None
        normalized.append({"option_id": option_id, "label": label, "consequence": consequence})
    return normalized


def get_interaction(interaction_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    for record in list_payloads(INTERACTION_TABLE, cfg.sqlite_path):
        if record.get("interaction_id") == interaction_id:
            return record
    return None


def list_interactions(conversation_id: str, cfg: Settings = settings) -> list[dict[str, Any]]:
    init_db(cfg.sqlite_path)
    records = [
        record
        for record in list_payloads(INTERACTION_TABLE, cfg.sqlite_path)
        if record.get("conversation_id") == conversation_id
    ]
    return sorted(records, key=lambda record: record.get("created_at") or "")


def get_pending_interaction(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    init_db(cfg.sqlite_path)
    pending = [record for record in list_interactions(conversation_id, cfg) if record.get("status") == "PENDING"]
    if not pending:
        return None
    if len(pending) > 1:
        # Enforced at creation time; a violation here means store corruption, not user error.
        raise InteractionError(
            "ERROR_INTERACTION_INVARIANT_VIOLATED",
            f"More than one pending interaction exists for conversation {conversation_id}.",
        )
    return pending[0]


def create_interaction(
    conversation_id: str,
    question: str,
    options: list[dict[str, Any]],
    *,
    allow_freeform: bool = True,
    dismiss_pending: bool = False,
    created_by: str = "assistant",
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Create a PENDING multiple-choice interaction.

    Enforces at most one PENDING interaction per conversation. If one
    already exists, the caller must pass dismiss_pending=True to explicitly
    supersede it (interruption semantics) -- creation never silently
    discards a prior pending question.
    """
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)
    question = question.strip()
    if not question:
        raise InteractionError("ERROR_INTERACTION_QUESTION_REQUIRED", "Interaction question must not be empty.")
    normalized_options = _normalize_options(options)

    existing_pending = get_pending_interaction(conversation_id, cfg)
    supersedes_interaction_id = None
    if existing_pending is not None:
        if not dismiss_pending:
            raise InteractionError(
                "ERROR_INTERACTION_ALREADY_PENDING",
                f"Conversation {conversation_id} already has a pending interaction: "
                f"{existing_pending['interaction_id']}",
            )
        dismissed = dismiss_interaction(
            existing_pending["interaction_id"],
            expected_version=existing_pending["version"],
            reason="SUPERSEDED_BY_NEW_INTERACTION",
            dismissed_by=created_by,
            cfg=cfg,
        )
        supersedes_interaction_id = dismissed["interaction_id"]

    now = utc_now()
    interaction_id = stable_id(
        "interaction",
        conversation_id,
        question,
        now,
        json.dumps(normalized_options, sort_keys=True),
    )
    record = {
        "interaction_id": interaction_id,
        "conversation_id": conversation_id,
        "kind": "MULTIPLE_CHOICE",
        "question": question,
        "options": normalized_options,
        "allow_freeform": bool(allow_freeform),
        "status": "PENDING",
        "version": 1,
        "created_at": now,
        "created_by": created_by,
        "resolved_at": None,
        "resolved_by": None,
        "resolution": None,
        "supersedes_interaction_id": supersedes_interaction_id,
    }
    upsert_payload(INTERACTION_TABLE, "interaction_id", record, cfg.sqlite_path)
    return record


def resolve_interaction(
    interaction_id: str,
    *,
    expected_version: int,
    resolved_by: str,
    option_id: str | None = None,
    freeform_text: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Resolve a PENDING interaction by option_id (never by label) or freeform text.

    Requires expected_version to match the stored version exactly, so a
    stale submission (built from a version the client already saw resolved
    or superseded) is rejected rather than silently applied. A second
    resolve attempt against an already-RESOLVED interaction is rejected as
    ERROR_INTERACTION_NOT_PENDING, not retried.
    """
    record = get_interaction(interaction_id, cfg)
    if record is None:
        raise InteractionError("ERROR_INTERACTION_NOT_FOUND", f"Interaction not found: {interaction_id}")
    # Version is checked before status: a submission built against a version
    # the client no longer holds is "stale", distinct from a fresh, correct
    # re-submission against an interaction that has already left PENDING
    # ("not pending" -- e.g. a genuine double click racing another request).
    if int(record.get("version", 0)) != int(expected_version):
        raise InteractionError(
            "ERROR_INTERACTION_VERSION_MISMATCH",
            f"Stale interaction version for {interaction_id}: "
            f"expected {record.get('version')}, submission carried {expected_version}.",
        )
    if record.get("status") != "PENDING":
        raise InteractionError(
            "ERROR_INTERACTION_NOT_PENDING",
            f"Interaction {interaction_id} is not pending (status={record.get('status')}).",
        )

    has_option = option_id is not None and str(option_id).strip() != ""
    normalized_freeform = (freeform_text or "").strip() or None
    has_freeform = normalized_freeform is not None
    if has_option and has_freeform:
        raise InteractionError(
            "ERROR_INTERACTION_AMBIGUOUS_RESPONSE", "Provide exactly one of option_id or freeform_text."
        )
    if not has_option and not has_freeform:
        raise InteractionError("ERROR_INTERACTION_EMPTY_RESPONSE", "Provide either option_id or freeform_text.")

    if has_option:
        valid_ids = {option["option_id"] for option in record.get("options", [])}
        if option_id not in valid_ids:
            raise InteractionError("ERROR_INTERACTION_OPTION_UNKNOWN", f"Unknown option_id: {option_id}")
        resolution = {"type": "OPTION", "option_id": option_id, "freeform_text": None}
    else:
        if not record.get("allow_freeform"):
            raise InteractionError(
                "ERROR_INTERACTION_FREEFORM_NOT_ALLOWED",
                f"Interaction {interaction_id} does not accept freeform responses.",
            )
        resolution = {"type": "FREEFORM", "option_id": None, "freeform_text": normalized_freeform}

    now = utc_now()
    updated = {
        **record,
        "status": "RESOLVED",
        "version": int(record["version"]) + 1,
        "resolved_at": now,
        "resolved_by": resolved_by,
        "resolution": resolution,
    }
    upsert_payload(INTERACTION_TABLE, "interaction_id", updated, cfg.sqlite_path)
    return updated


def dismiss_interaction(
    interaction_id: str,
    *,
    expected_version: int,
    reason: str,
    dismissed_by: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    """Explicitly dismiss (suspend) a PENDING interaction, e.g. on interruption.

    Dismissal is a distinct terminal state from resolution: it records that
    the question was abandoned, with why, rather than answered. Resuming a
    conversation after an interruption should call get_pending_interaction
    to see whether the same question is still pending before assuming any
    prior narration about it is accurate.
    """
    record = get_interaction(interaction_id, cfg)
    if record is None:
        raise InteractionError("ERROR_INTERACTION_NOT_FOUND", f"Interaction not found: {interaction_id}")
    if int(record.get("version", 0)) != int(expected_version):
        raise InteractionError(
            "ERROR_INTERACTION_VERSION_MISMATCH",
            f"Stale interaction version for {interaction_id}: "
            f"expected {record.get('version')}, submission carried {expected_version}.",
        )
    if record.get("status") != "PENDING":
        raise InteractionError(
            "ERROR_INTERACTION_NOT_PENDING",
            f"Interaction {interaction_id} is not pending (status={record.get('status')}).",
        )
    reason = reason.strip()
    if not reason:
        raise InteractionError("ERROR_INTERACTION_DISMISS_REASON_REQUIRED", "A dismiss reason is required.")

    now = utc_now()
    updated = {
        **record,
        "status": "DISMISSED",
        "version": int(record["version"]) + 1,
        "resolved_at": now,
        "resolved_by": dismissed_by,
        "resolution": {"type": "DISMISSED", "option_id": None, "freeform_text": None, "reason": reason},
    }
    upsert_payload(INTERACTION_TABLE, "interaction_id", updated, cfg.sqlite_path)
    return updated
