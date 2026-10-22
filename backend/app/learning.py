"""Governed local learning. Content is evidence; only operator decisions confer authority.

Artifacts and receipts commit together. No arbitrary code, network training, registry
mutation, or permission changes are performed by this module.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from typing import Any

from .config import Settings
from .injection_guard import scan_text
from .utils import utc_now

# Stable ownership IDs describe local review queues, not running worker agents.
OWNERS = {
    "instruction": "mucaro/curator/instructions",
    "skill": "mucaro/curator/skills",
    "project": "mucaro/curator/project",
    "knowledge": "mucaro/curator/knowledge",
    "evaluation": "mucaro/curator/evaluations",
    "dataset": "mucaro/curator/datasets",
}
CUES = {
    "instruction": r"\b(always|never|general rule)\b",
    "skill": r"\b(workflow|procedure|steps|repeatable)\b",
    "project": r"\b(repository|project requirement)\b",
    "knowledge": r"\b(correction|fact|lesson)\b",
    "evaluation": r"\b(regression|counterexample|test case|failure case)\b",
    "dataset": r"\b(training example|fine.tun)\b",
}


class LearningError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _check(condition: bool, reason: str) -> None:
    if not condition:
        raise LearningError(reason)


def _validate(state: dict) -> None:
    previous = None
    for seq, event in enumerate(state["receipts"], 1):
        payload = {key: value for key, value in event.items() if key != "sha256"}
        _check(event["seq"] == seq and event["previous"] == previous and digest(payload) == event["sha256"],
               "RECEIPT_CHAIN_CORRUPT")
        previous = event["sha256"]
    # Bind current materialized state as well as the historical receipt chain.
    if state["receipts"]:
        _check(state["receipts"][-1]["state_sha256"] == digest({k: v for k, v in state.items() if k != "receipts"}),
               "LEARNING_STATE_CORRUPT")


@contextmanager
def _store(cfg: Settings):
    cfg.ensure_dirs()
    conn = sqlite3.connect(cfg.processed_dir / "learning.sqlite3", timeout=30)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        row = conn.execute("SELECT payload FROM state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {"candidates": {}, "feedback": {}, "capture": {"enabled": False, "purpose": ""}, "receipts": []}
        _validate(state)
        yield state
        _validate(state)
        conn.execute("INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                     (json.dumps(state, ensure_ascii=False, allow_nan=False),))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _receipt(state: dict, action: str, candidate_id: str | None, detail: dict) -> None:
    event = {"seq": len(state["receipts"]) + 1, "at": utc_now(), "action": action,
             "candidate_id": candidate_id, "detail": detail,
             "previous": state["receipts"][-1]["sha256"] if state["receipts"] else None,
             "state_sha256": digest({k: v for k, v in state.items() if k != "receipts"})}
    event["sha256"] = digest(event)
    state["receipts"].append(event)


def snapshot(cfg: Settings) -> dict:
    with _store(cfg) as state:
        return {"status": "LEARNING_OK", "owners": OWNERS, **state,
                "mechanisms": {"instruction": "approved_prompt_context", "skill": "versioned_procedure_artifact",
                               "project": "scoped_artifact", "knowledge": "scoped_artifact",
                               "evaluation": "regression_case_artifact", "dataset": "UNBOUND"},
                "rl_mechanism": "operator_feedback_contextual_bandit_recommendations",
                "external_training": "UNBOUND", "receipt_chain": "PASS"}


def recognize(cfg: Settings, *, text: str, source_id: str, source_bytes: str,
              permitted_use: bool, purpose: str, kind: str | None = None,
              scope: str = "general", outcome: str = "UNKNOWN") -> dict:
    _check(permitted_use is True, "PERMITTED_USE_REQUIRED")
    _check(isinstance(text, str) and 0 < len(text.strip()) <= 800, "LESSON_LENGTH")
    _check(isinstance(source_bytes, str) and 0 < len(source_bytes) <= 100000, "EVIDENCE_REQUIRED")
    _check(bool(source_id.strip()) and len(source_id) <= 200 and bool(purpose.strip()) and len(purpose) <= 500,
           "SOURCE_AND_PURPOSE_REQUIRED")
    _check(0 < len(scope.strip()) <= 100, "SCOPE_REQUIRED")
    _check(kind is None or kind in OWNERS, "UNKNOWN_DESTINATION")
    _check(outcome in {"PASS", "FAIL", "UNKNOWN"}, "OUTCOME_INVALID")
    matches = [kind] if kind else [key for key, cue in CUES.items() if re.search(cue, text, re.IGNORECASE)]
    # Full discovery set retained; a tie never resolves by name/order/proximity.
    routed = matches[0] if len(matches) == 1 else None
    if routed == "instruction" and scope != "general":
        routed = None
    if routed in {"project", "knowledge", "skill"}:
        _check(scope != "general", "SPECIALIST_SCOPE_REQUIRED")
    payload = {"text_raw": text, "source_id": source_id, "source_raw": source_bytes,
               "source_sha256": hashlib.sha256(source_bytes.encode()).hexdigest(), "scope": scope,
               "purpose": purpose, "outcome": outcome, "kind": routed, "destinations": matches,
               "permitted_use": True, "classification": "OPERATOR_DECLARED" if kind else "CUE_DISCOVERY_ONLY"}
    candidate_id = "learn_" + digest(payload)
    with _store(cfg) as state:
        if candidate_id in state["candidates"]:
            return state["candidates"][candidate_id]
        row = {**payload, "candidate_id": candidate_id, "version": 1, "created_at": utc_now(),
               "owner": OWNERS.get(routed), "state": "REVIEW" if routed else "UNRESOLVED",
               "signals": scan_text(text)["signals"], "review": None, "artifact": None}
        state["candidates"][candidate_id] = row
        _receipt(state, "RECOGNIZED_AND_ROUTED" if routed else "RECOGNIZED_UNRESOLVED", candidate_id,
                 {"destinations": matches, "owner": row["owner"], "source_sha256": row["source_sha256"]})
        return row


def _candidate(state: dict, candidate_id: str, version: int) -> dict:
    _check(candidate_id in state["candidates"], "CANDIDATE_NOT_FOUND")
    row = state["candidates"][candidate_id]
    _check(type(version) is int and row["version"] == version, "STALE_VERSION")
    return row


def review(cfg: Settings, candidate_id: str, *, version: int, accept: bool, rationale: str,
           positive_case: str, negative_case: str, conflict_ids: list[str],
           explicit_approval: bool, verified_evidence: bool, authority_safe: bool) -> dict:
    with _store(cfg) as state:
        row = _candidate(state, candidate_id, version)
        _check(row["state"] == "REVIEW", "NOT_REVIEWABLE")
        _check(bool(rationale.strip()), "RATIONALE_REQUIRED")
        if accept:
            _check(row["kind"] != "instruction" or len(" ".join(row["text_raw"].split())) <= 320,
                   "INSTRUCTION_EXCEEDS_PROMPT_ITEM_LIMIT")
            _check(explicit_approval is True, "EXPLICIT_OPERATOR_APPROVAL_REQUIRED")
            _check(verified_evidence is True and authority_safe is True, "EVIDENCE_AND_AUTHORITY_REVIEW_REQUIRED")
            _check(bool(positive_case.strip()) and bool(negative_case.strip()) and positive_case != negative_case,
                   "POSITIVE_AND_NEGATIVE_CASES_REQUIRED")
            _check(not conflict_ids, "UNRESOLVED_CONFLICTS")
            duplicates = [key for key, other in state["candidates"].items()
                          if key != candidate_id and other["state"] in {"ACCEPTED", "APPLIED", "VERIFIED"}
                          and other["text_raw"] == row["text_raw"] and other["scope"] == row["scope"]
                          and other["kind"] == row["kind"]]
            _check(not duplicates, "EXACT_DUPLICATE_ALREADY_ACCEPTED")
        row["review"] = {"rationale": rationale, "positive_case": positive_case, "negative_case": negative_case,
                         "conflict_ids": conflict_ids, "operator_approval": explicit_approval,
                         "verified_evidence": verified_evidence, "authority_safe": authority_safe,
                         "reviewer": "local_operator", "at": utc_now()}
        row["state"] = "ACCEPTED" if accept else "REJECTED"
        row["version"] += 1
        _receipt(state, row["state"], candidate_id, row["review"])
        return row


def apply(cfg: Settings, candidate_id: str, *, version: int) -> dict:
    with _store(cfg) as state:
        row = _candidate(state, candidate_id, version)
        _check(row["state"] == "ACCEPTED", "ACCEPTED_REVIEW_REQUIRED")
        _check(row["kind"] != "dataset", "MODEL_TRAINER_UNBOUND")
        # No inferred authority escalation: these are local artifacts, never code or tool permissions.
        artifact = {"artifact_id": candidate_id + ":v1", "version": 1, "kind": row["kind"],
                    "scope": row["scope"], "content": row["text_raw"], "evidence_sha256": row["source_sha256"],
                    "candidate_id": candidate_id}
        artifact["sha256"] = digest(artifact)
        row["artifact"] = artifact
        row["state"] = "APPLIED"
        row["version"] += 1
        _receipt(state, "APPLIED", candidate_id, {"artifact_sha256": artifact["sha256"]})
        return row


def verify(cfg: Settings, candidate_id: str, *, version: int, positive_pass: bool,
           negative_pass: bool, evaluation_receipt: str) -> dict:
    with _store(cfg) as state:
        row = _candidate(state, candidate_id, version)
        _check(row["state"] == "APPLIED", "APPLIED_ARTIFACT_REQUIRED")
        _check(bool(evaluation_receipt.strip()), "EVALUATION_RECEIPT_REQUIRED")
        _check(positive_pass is True and negative_pass is True, "REGRESSION_GATES_FAILED")
        artifact = row["artifact"]
        _check(digest({k: v for k, v in artifact.items() if k != "sha256"}) == artifact["sha256"], "ARTIFACT_CORRUPT")
        row["verification"] = {"positive_pass": True, "negative_pass": True,
                               "receipt": evaluation_receipt, "verifier": "local_operator"}
        row["state"] = "VERIFIED"
        row["version"] += 1
        _receipt(state, "VERIFIED", candidate_id, row["verification"])
        return row


def rollback(cfg: Settings, candidate_id: str, *, version: int, reason: str) -> dict:
    with _store(cfg) as state:
        row = _candidate(state, candidate_id, version)
        _check(row["state"] in {"APPLIED", "VERIFIED"}, "NO_APPLIED_ARTIFACT")
        _check(bool(reason.strip()), "ROLLBACK_REASON_REQUIRED")
        row["state"] = "ROLLED_BACK"
        row["version"] += 1
        _receipt(state, "ROLLED_BACK", candidate_id, {"reason": reason})
        return row


def prompt_context(cfg: Settings) -> list[dict]:
    """Only explicitly reviewed, verified general rules enter the existing bounded memory selector."""
    state = snapshot(cfg)
    return [{"memory_id": row["artifact"]["artifact_id"], "kind": "instruction", "content": row["text_raw"],
             "enabled": True, "source": "governed_learning:" + row["source_sha256"]}
            for row in state["candidates"].values() if row["state"] == "VERIFIED" and row["kind"] == "instruction"]


def feedback(cfg: Settings, *, episode_id: str, candidate_id: str, context: str,
             reward: float, evidence: str, permitted_use: bool) -> dict:
    with _store(cfg) as state:
        return _record_feedback(state, episode_id=episode_id, candidate_id=candidate_id, context=context,
                                reward=reward, evidence=evidence, permitted_use=permitted_use)


def _record_feedback(state: dict, *, episode_id: str, candidate_id: str, context: str,
                     reward: float, evidence: str, permitted_use: bool) -> dict:
    _check(permitted_use is True, "FEEDBACK_PERMITTED_USE_REQUIRED")
    _check(isinstance(reward, (float, int)) and not isinstance(reward, bool)
           and math.isfinite(reward) and -1 <= reward <= 1, "REWARD_RANGE")
    _check(bool(episode_id.strip()) and len(episode_id) <= 200 and bool(context.strip()) and len(context) <= 100
           and bool(evidence.strip()) and len(evidence) <= 10000, "FEEDBACK_PROVENANCE_REQUIRED")
    payload = {"episode_id": episode_id, "candidate_id": candidate_id, "context": context,
               "reward": reward, "evidence": evidence, "permitted_use": True, "source": "local_operator"}
    if episode_id in state["feedback"]:
        _check({k: v for k, v in state["feedback"][episode_id].items() if k != "artifact_sha256"} == payload, "EPISODE_REPLAY_CONFLICT")
        return state["feedback"][episode_id]
    row = state["candidates"].get(candidate_id)
    _check(row is not None and row["state"] == "VERIFIED" and row["kind"] == "skill", "VERIFIED_SKILL_REQUIRED")
    _check(context == row["scope"], "FEEDBACK_SCOPE_MISMATCH")
    payload["artifact_sha256"] = row["artifact"]["sha256"]
    # Compare replay on the same pinned policy artifact.
    state["feedback"][episode_id] = payload
    _receipt(state, "REWARD_RECORDED", candidate_id, {"episode_id": episode_id, "reward": reward})
    return payload


def recommend(cfg: Settings, context: str) -> dict:
    state = snapshot(cfg)
    options = []
    for key, row in state["candidates"].items():
        if row["state"] != "VERIFIED" or row["kind"] != "skill" or row["scope"] != context:
            continue
        rewards = [item["reward"] for item in state["feedback"].values()
                   if item["candidate_id"] == key and item["context"] == context
                   and item["artifact_sha256"] == row["artifact"]["sha256"]]
        n = len(rewards)
        # Bounded contextual bandit value update with a zero-reward prior.
        value = sum(rewards) / (n + 1)
        options.append({"candidate_id": key, "artifact_sha256": row["artifact"]["sha256"],
                        "episodes": n, "value": value})
    top = max((option["value"] for option in options), default=None)
    winners = [option["candidate_id"] for option in options if option["value"] == top]
    return {"status": "RECOMMENDATION" if len(winners) == 1 and top > 0 else "UNRESOLVED",
            "context": context, "options": options, "top_candidates": winners,
            "policy_sha256": digest(options), "training_episodes": sum(x["episodes"] for x in options),
            "execution_authorized": False, "model_weights_trained": False,
            "limitations": ["Operator-attested rewards; no causal or held-out improvement claim.",
                           "No automatic execution or exploration; explicit skill selection remains required."]}


def resolve(cfg: Settings, candidate_id: str, *, version: int, kind: str, scope: str, rationale: str) -> dict:
    _check(kind in OWNERS, "UNKNOWN_DESTINATION")
    _check(bool(rationale.strip()) and 0 < len(scope.strip()) <= 100, "RESOLUTION_RATIONALE_AND_SCOPE_REQUIRED")
    _check((kind != "instruction" or scope == "general")
           and (kind not in {"skill", "project", "knowledge"} or scope != "general"), "SCOPE_MISMATCH")
    with _store(cfg) as state:
        row = _candidate(state, candidate_id, version)
        _check(row["state"] in {"UNRESOLVED", "REVIEW"}, "NOT_RESOLVABLE")
        row.update(kind=kind, scope=scope, owner=OWNERS[kind], state="REVIEW", version=version + 1)
        _receipt(state, "DESTINATION_RESOLVED", candidate_id, {"kind": kind, "scope": scope, "rationale": rationale})
        return row


def scan_run(cfg: Settings, run_id: str, *, permitted_use: bool, purpose: str) -> dict:
    from .db import ensure_db, list_payloads
    _check(permitted_use is True, "PERMITTED_USE_REQUIRED")
    ensure_db(cfg.sqlite_path)
    rows = [row for row in list_payloads("runs", cfg.sqlite_path) if row.get("run_id") == run_id]
    _check(len(rows) == 1, "UNIQUE_RUN_REQUIRED")
    run = {k: v for k, v in rows[0].items() if k != "learning_capture"}
    # Discovery over user-written lessons only; model answers are retained as evidence,
    # never interpreted as instructions or as verified outcomes.
    text = str(run.get("query", ""))
    if not any(re.search(cue, text, re.IGNORECASE) for cue in CUES.values()):
        return {"status": "NO_CANDIDATE", "run_id": run_id, "scope": "bounded cue recognition"}
    return recognize(cfg, text=text, source_id=run_id, source_bytes=json.dumps(run, ensure_ascii=False, sort_keys=True),
                     permitted_use=True, purpose=purpose, scope="run:" + run_id)


def configure_capture(cfg: Settings, *, enabled: bool, purpose: str) -> dict:
    _check(type(enabled) is bool and bool(purpose.strip()) and len(purpose) <= 500, "CAPTURE_PURPOSE_REQUIRED")
    with _store(cfg) as state:
        state["capture"] = {"enabled": enabled, "purpose": purpose}
        _receipt(state, "CAPTURE_CONFIGURED", None, state["capture"])
        return state["capture"]


def capture_run_if_enabled(cfg: Settings, run_id: str) -> dict:
    config = snapshot(cfg)["capture"]
    if not config["enabled"]:
        return {"status": "CAPTURE_DISABLED"}
    return scan_run(cfg, run_id, permitted_use=True, purpose=config["purpose"])
