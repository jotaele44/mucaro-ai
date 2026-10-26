from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .config import Settings, settings
from .db import ensure_db, get_connection
from .utils import stable_id, utc_now
from .work_runs import RunError, get_run, submit_message

ENTRY_SCHEMA_VERSION = "mucaro-execution-entry/1.0"
ENTRY_MODES = {"GOAL", "PLAN"}
ENTRY_ACTIONS = {"GOAL": {"Proceed", "Adjust"}, "PLAN": {"Execute", "Revise"}}
READY_STATE = {"GOAL": "VECTOR_READY", "PLAN": "PLAN_APPROVED"}
CANDIDATE_STATE = {"GOAL": "VECTOR_CANDIDATE", "PLAN": "PLAN_CANDIDATE"}
PASS_STATES = {"PASS"}
SOURCE_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ExecutionEntryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_source_commit(value: str) -> str:
    if not SOURCE_COMMIT_RE.fullmatch(value or ""):
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_SOURCE_COMMIT", "source_commit must be an exact 40-character Git SHA")
    return value


def _require_mode(mode: str) -> str:
    normalized = str(mode or "").upper()
    if normalized not in ENTRY_MODES:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_MODE", f"mode must be one of {sorted(ENTRY_MODES)}")
    return normalized


def _validate_gates(required_gates: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(required_gates, list) or not required_gates:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", "required_gates must contain at least one mandatory gate")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in required_gates:
        gate_id = str(row.get("gate_id") or "").strip()
        state = str(row.get("state") or "").upper()
        if not gate_id or gate_id in seen:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", "gate_id values must be non-empty and unique")
        if not state:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", f"{gate_id} has no state")
        seen.add(gate_id)
        normalized.append({"gate_id": gate_id, "state": state})
    return normalized


def _assert_all_gates_pass(gates: list[dict[str, str]]) -> None:
    blocked = [row for row in gates if row["state"] not in PASS_STATES]
    if blocked:
        raise ExecutionEntryError(
            "ERROR_EXECUTION_ENTRY_NOT_READY",
            "mandatory gates are not PASS: " + ",".join(f"{row['gate_id']}={row['state']}" for row in blocked),
        )


def _tx(cfg: Settings):
    conn = get_connection(cfg.sqlite_path)
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _row_payload(row: Any) -> dict[str, Any]:
    return json.loads(row["payload_json"])


def create_entry_artifact(
    *,
    mode: str,
    objective_id: str,
    conversation_id: str,
    prompt: str,
    runtime_mode: str,
    options: dict[str, Any] | None,
    source_commit: str,
    required_gates: list[dict[str, Any]],
    actor: str,
    policy_version: str = "execution-entry-v1",
    cfg: Settings = settings,
) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    mode = _require_mode(mode)
    objective_id = (objective_id or "").strip()
    conversation_id = (conversation_id or "").strip()
    prompt = (prompt or "").strip()
    actor = (actor or "").strip()
    if not objective_id or not conversation_id or not prompt or not actor:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_INPUT", "objective_id, conversation_id, prompt and actor are required")
    source_commit = _require_source_commit(source_commit)
    if runtime_mode not in {"plan", "ask", "auto"}:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_RUNTIME_MODE", runtime_mode)
    gates = _validate_gates(required_gates)
    now = utc_now()
    artifact_id = stable_id("entry", mode, objective_id, conversation_id, source_commit, now, length=16)
    payload: dict[str, Any] = {
        "schema_version": ENTRY_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "mode": mode,
        "objective_id": objective_id,
        "vector_id": artifact_id if mode == "GOAL" else None,
        "vector_revision": 1 if mode == "GOAL" else None,
        "plan_id": artifact_id if mode == "PLAN" else None,
        "plan_revision": 1 if mode == "PLAN" else None,
        "revision": 1,
        "version": 1,
        "state": CANDIDATE_STATE[mode],
        "conversation_id": conversation_id,
        "prompt": prompt,
        "runtime_mode": runtime_mode,
        "options": dict(options or {}),
        "required_gates": gates,
        "actor": actor,
        "policy_version": policy_version,
        "source_commit": source_commit,
        "predecessor_revision": None,
        "created_at": now,
    }
    payload["artifact_sha256"] = _canonical_hash(payload)
    with get_connection(cfg.sqlite_path) as conn:
        conn.execute(
            "INSERT INTO execution_entry_artifacts (artifact_id, revision, mode, state, conversation_id, source_commit, payload_json) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (artifact_id, mode, payload["state"], conversation_id, source_commit, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    return payload


def get_entry_artifact(artifact_id: str, revision: int | None = None, cfg: Settings = settings) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        if revision is None:
            row = conn.execute(
                "SELECT payload_json FROM execution_entry_artifacts WHERE artifact_id = ? ORDER BY revision DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT payload_json FROM execution_entry_artifacts WHERE artifact_id = ? AND revision = ?",
                (artifact_id, revision),
            ).fetchone()
    if row is None:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_NOT_FOUND", artifact_id)
    return _row_payload(row)


def current_conversation_entry(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_artifacts WHERE conversation_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return _row_payload(row) if row is not None else None


def transition_entry_artifact(
    artifact_id: str,
    revision: int,
    *,
    expected_version: int,
    target_state: str,
    actor: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    conn = _tx(cfg)
    try:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_artifacts WHERE artifact_id = ? AND revision = ?",
            (artifact_id, revision),
        ).fetchone()
        if row is None:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_NOT_FOUND", artifact_id)
        payload = _row_payload(row)
        if payload["version"] != expected_version:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_VERSION_CONFLICT", f"expected={expected_version} actual={payload['version']}")
        wanted = READY_STATE[payload["mode"]]
        if target_state != wanted:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_STATE", f"only {wanted} is a valid approval target")
        if payload["state"] != CANDIDATE_STATE[payload["mode"]]:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_STATE", f"cannot approve from {payload['state']}")
        _assert_all_gates_pass(payload["required_gates"])
        payload["state"] = wanted
        payload["version"] += 1
        payload["approved_by"] = actor
        payload["approved_at"] = utc_now()
        payload["artifact_sha256"] = _canonical_hash({k: v for k, v in payload.items() if k != "artifact_sha256"})
        conn.execute(
            "UPDATE execution_entry_artifacts SET state = ?, payload_json = ? WHERE artifact_id = ? AND revision = ?",
            (payload["state"], json.dumps(payload, ensure_ascii=False, sort_keys=True), artifact_id, revision),
        )
        conn.execute("COMMIT")
        return payload
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _decision_request(
    *,
    artifact: dict[str, Any],
    action: str,
    expected_version: int,
    actor: str,
    idempotency_key: str,
    source_commit: str,
    changes: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact["artifact_id"],
        "revision": artifact["revision"],
        "mode": artifact["mode"],
        "action": action,
        "expected_version": expected_version,
        "actor": actor,
        "idempotency_key": idempotency_key,
        "source_commit": source_commit,
        "changes": changes or {},
    }


def _existing_decision(conn: Any, idempotency_key: str, request_sha256: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT request_sha256, payload_json FROM execution_entry_decisions WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    if row["request_sha256"] != request_sha256:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_IDEMPOTENCY_CONFLICT", idempotency_key)
    return _row_payload(row)


def _create_revision_locked(
    conn: Any,
    artifact: dict[str, Any],
    *,
    actor: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    latest = conn.execute(
        "SELECT revision FROM execution_entry_artifacts WHERE artifact_id = ? ORDER BY revision DESC LIMIT 1",
        (artifact["artifact_id"],),
    ).fetchone()
    if latest is None or int(latest["revision"]) != artifact["revision"]:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_STALE_REVISION", artifact["artifact_id"])
    revision = artifact["revision"] + 1
    next_payload = dict(artifact)
    for key in ("prompt", "runtime_mode", "options", "required_gates", "source_commit"):
        if key in changes:
            next_payload[key] = changes[key]
    next_payload["source_commit"] = _require_source_commit(next_payload["source_commit"])
    if next_payload["runtime_mode"] not in {"plan", "ask", "auto"}:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_RUNTIME_MODE", str(next_payload["runtime_mode"]))
    next_payload["required_gates"] = _validate_gates(next_payload["required_gates"])
    next_payload["revision"] = revision
    next_payload["version"] = 1
    next_payload["state"] = CANDIDATE_STATE[artifact["mode"]]
    next_payload["predecessor_revision"] = artifact["revision"]
    next_payload["actor"] = actor
    next_payload["created_at"] = utc_now()
    next_payload.pop("approved_by", None)
    next_payload.pop("approved_at", None)
    next_payload.pop("artifact_sha256", None)
    if artifact["mode"] == "GOAL":
        next_payload["vector_revision"] = revision
    else:
        next_payload["plan_revision"] = revision
    next_payload["artifact_sha256"] = _canonical_hash(next_payload)
    conn.execute(
        "INSERT INTO execution_entry_artifacts (artifact_id, revision, mode, state, conversation_id, source_commit, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            artifact["artifact_id"],
            revision,
            artifact["mode"],
            next_payload["state"],
            artifact["conversation_id"],
            next_payload["source_commit"],
            json.dumps(next_payload, ensure_ascii=False, sort_keys=True),
        ),
    )
    return next_payload


def decide_entry(
    artifact_id: str,
    revision: int,
    *,
    action: str,
    expected_version: int,
    actor: str,
    idempotency_key: str,
    source_commit: str,
    changes: dict[str, Any] | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    source_commit = _require_source_commit(source_commit)
    actor = (actor or "").strip()
    idempotency_key = (idempotency_key or "").strip()
    if not actor or len(idempotency_key) < 8:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_INPUT", "actor and an idempotency_key of at least 8 characters are required")

    conn = _tx(cfg)
    dispatch_after_commit = False
    try:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_artifacts WHERE artifact_id = ? AND revision = ?",
            (artifact_id, revision),
        ).fetchone()
        if row is None:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_NOT_FOUND", artifact_id)
        artifact = _row_payload(row)
        if action not in ENTRY_ACTIONS[artifact["mode"]]:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_ACTION", f"{artifact['mode']} does not support {action}")
        request = _decision_request(
            artifact=artifact,
            action=action,
            expected_version=expected_version,
            actor=actor,
            idempotency_key=idempotency_key,
            source_commit=source_commit,
            changes=changes,
        )
        request_sha256 = _canonical_hash(request)
        existing = _existing_decision(conn, idempotency_key, request_sha256)
        if existing is not None:
            conn.execute("COMMIT")
            return existing
        if artifact["version"] != expected_version:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_VERSION_CONFLICT", f"expected={expected_version} actual={artifact['version']}")
        if artifact["source_commit"] != source_commit:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_SOURCE_DRIFT", f"expected={artifact['source_commit']} observed={source_commit}")

        decision_id = stable_id("entrydec", artifact_id, str(revision), action, idempotency_key, length=18)
        now = utc_now()
        if action in {"Adjust", "Revise"}:
            revised = _create_revision_locked(conn, artifact, actor=actor, changes=dict(changes or {}))
            decision = {
                "schema_version": ENTRY_SCHEMA_VERSION,
                "decision_id": decision_id,
                "artifact_id": artifact_id,
                "revision": revision,
                "mode": artifact["mode"],
                "action": action,
                "actor": actor,
                "policy_version": artifact["policy_version"],
                "idempotency_key": idempotency_key,
                "source_commit": source_commit,
                "request_sha256": request_sha256,
                "state": "REVISION_CREATED",
                "created_at": now,
                "new_revision": revised["revision"],
                "run_id": None,
            }
        else:
            expected_action = "Proceed" if artifact["mode"] == "GOAL" else "Execute"
            if action != expected_action:
                raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_ACTION", action)
            if artifact["state"] != READY_STATE[artifact["mode"]]:
                raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_NOT_READY", artifact["state"])
            _assert_all_gates_pass(artifact["required_gates"])
            active = conn.execute(
                "SELECT run_id FROM work_runs WHERE conversation_id = ? "
                "AND state IN ('QUEUED','RUNNING','AWAITING_APPROVAL','AWAITING_INPUT') LIMIT 1",
                (artifact["conversation_id"],),
            ).fetchone()
            if active is not None:
                raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_CONFLICTING_RUN", str(active["run_id"]))
            decision = {
                "schema_version": ENTRY_SCHEMA_VERSION,
                "decision_id": decision_id,
                "artifact_id": artifact_id,
                "revision": revision,
                "mode": artifact["mode"],
                "action": action,
                "actor": actor,
                "policy_version": artifact["policy_version"],
                "idempotency_key": idempotency_key,
                "source_commit": source_commit,
                "request_sha256": request_sha256,
                "state": "CLAIMED",
                "created_at": now,
                "run_id": None,
            }
            dispatch_after_commit = True
        decision["receipt_sha256"] = _canonical_hash(decision)
        conn.execute(
            "INSERT INTO execution_entry_decisions "
            "(decision_id, artifact_id, revision, idempotency_key, request_sha256, state, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                artifact_id,
                revision,
                idempotency_key,
                request_sha256,
                decision["state"],
                json.dumps(decision, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    return dispatch_entry_decision(decision["decision_id"], cfg=cfg) if dispatch_after_commit else decision


def get_entry_decision(decision_id: str, cfg: Settings = settings) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
    if row is None:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_DECISION_NOT_FOUND", decision_id)
    return _row_payload(row)


def dispatch_entry_decision(decision_id: str, cfg: Settings = settings) -> dict[str, Any]:
    decision = get_entry_decision(decision_id, cfg)
    if decision["state"] == "DISPATCHED":
        if decision.get("run_id"):
            try:
                decision["run"] = get_run(decision["run_id"], cfg)
            except RunError:
                pass
        return decision
    if decision["state"] != "CLAIMED":
        return decision
    artifact = get_entry_artifact(decision["artifact_id"], decision["revision"], cfg)
    try:
        result = submit_message(
            artifact["conversation_id"],
            artifact["prompt"],
            mode=artifact["runtime_mode"],
            options=artifact["options"],
            idempotency_key=f"entry:{decision_id}",
            require_idle=True,
            cfg=cfg,
        )
    except RunError as exc:
        if exc.status != "ERROR_RUN_CONFLICTING_ACTIVE":
            raise
        conn = _tx(cfg)
        try:
            row = conn.execute(
                "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            if row is None:
                raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_DECISION_NOT_FOUND", decision_id)
            current = _row_payload(row)
            current["state"] = "REJECTED_CONFLICT"
            current["rejected_at"] = utc_now()
            current["conflicting_run_id"] = exc.detail
            current["receipt_sha256"] = _canonical_hash({k: v for k, v in current.items() if k != "receipt_sha256"})
            conn.execute(
                "UPDATE execution_entry_decisions SET state = 'REJECTED_CONFLICT', payload_json = ? WHERE decision_id = ?",
                (json.dumps(current, ensure_ascii=False, sort_keys=True), decision_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_CONFLICTING_RUN", exc.detail) from exc
    run = result["run"]
    conn = _tx(cfg)
    try:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_DECISION_NOT_FOUND", decision_id)
        current = _row_payload(row)
        if current["state"] == "DISPATCHED":
            conn.execute("COMMIT")
            return current
        current["state"] = "DISPATCHED"
        current["run_id"] = run["run_id"]
        current["dispatched_at"] = utc_now()
        current["receipt_sha256"] = _canonical_hash({k: v for k, v in current.items() if k != "receipt_sha256"})
        conn.execute(
            "UPDATE execution_entry_decisions SET state = 'DISPATCHED', payload_json = ? WHERE decision_id = ?",
            (json.dumps(current, ensure_ascii=False, sort_keys=True), decision_id),
        )
        conn.execute("COMMIT")
        current["run"] = run
        return current
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def resume_pending_entry_decisions(cfg: Settings = settings) -> dict[str, Any]:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        rows = conn.execute(
            "SELECT decision_id FROM execution_entry_decisions WHERE state = 'CLAIMED' ORDER BY rowid ASC"
        ).fetchall()
    dispatched: list[str] = []
    for row in rows:
        result = dispatch_entry_decision(str(row["decision_id"]), cfg)
        if result.get("state") == "DISPATCHED":
            dispatched.append(str(row["decision_id"]))
    return {"status": "EXECUTION_ENTRY_RECOVERY_OK", "dispatched": dispatched}
