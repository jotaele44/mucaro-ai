from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from .config import Settings, settings
from .conversations import get_conversation
from .db import ensure_db, get_connection
from .runtime_identity import current_git_head
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


def _verify_hash(payload: dict[str, Any], field: str, status: str) -> None:
    expected = str(payload.get(field) or "")
    material = {key: value for key, value in payload.items() if key != field}
    actual = _canonical_hash(material)
    if expected != actual:
        raise ExecutionEntryError(status, f"expected={expected} observed={actual}")


def entry_labels(locale: str, mode: str) -> dict[str, str]:
    mode = _require_mode(mode)
    language = "es" if str(locale or "").lower().startswith("es") else "en"
    labels = {
        ("en", "GOAL"): {"primary": "Proceed", "secondary": "Adjust"},
        ("en", "PLAN"): {"primary": "Execute", "secondary": "Revise"},
        ("es", "GOAL"): {"primary": "Proceder", "secondary": "Ajustar"},
        ("es", "PLAN"): {"primary": "Ejecutar", "secondary": "Revisar"},
    }
    return {"locale": language, "mode": mode, **labels[(language, mode)]}


def _require_source_commit(value: str) -> str:
    if not SOURCE_COMMIT_RE.fullmatch(value or ""):
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_SOURCE_COMMIT", "source_commit must be an exact 40-character Git SHA")
    return value


def _runtime_source_commit(cfg: Settings) -> str:
    observed = current_git_head(cfg.project_root)
    if observed is None:
        raise ExecutionEntryError(
            "ERROR_EXECUTION_ENTRY_SOURCE_IDENTITY_UNAVAILABLE",
            "runtime source identity is unavailable from bound package identity or local Git metadata",
        )
    return _require_source_commit(observed)


def _assert_runtime_source(expected: str, cfg: Settings) -> str:
    observed = _runtime_source_commit(cfg)
    if observed != expected:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_SOURCE_DRIFT", f"expected={expected} observed={observed}")
    return observed


def _require_mode(mode: str) -> str:
    normalized = str(mode or "").upper()
    if normalized not in ENTRY_MODES:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_MODE", f"mode must be one of {sorted(ENTRY_MODES)}")
    return normalized


def _validate_artifact_identity(payload: dict[str, Any]) -> None:
    mode = _require_mode(str(payload.get("mode") or ""))
    artifact_id = str(payload.get("artifact_id") or "")
    revision = int(payload.get("revision") or 0)
    if not artifact_id or revision < 1:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_IDENTITY", "artifact_id and revision are required")
    if mode == "GOAL":
        if payload.get("vector_id") != artifact_id or payload.get("vector_revision") != revision:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_IDENTITY", "GOAL vector identity does not match artifact revision")
    else:
        if payload.get("plan_id") != artifact_id or payload.get("plan_revision") != revision:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_IDENTITY", "PLAN identity does not match artifact revision")


def _validate_gates(required_gates: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(required_gates, list) or not required_gates:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", "required_gates must contain at least one mandatory gate")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in required_gates:
        gate_id = str(row.get("gate_id") or "").strip()
        state = str(row.get("state") or "").upper()
        evidence_ref = str(row.get("evidence_ref") or "").strip()
        evidence_sha256 = str(row.get("evidence_sha256") or "").strip().lower()
        if not gate_id or gate_id in seen:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", "gate_id values must be non-empty and unique")
        if not state:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", f"{gate_id} has no state")
        if not evidence_ref:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", f"{gate_id} has no evidence_ref")
        if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_GATES", f"{gate_id} has no valid evidence_sha256")
        seen.add(gate_id)
        normalized.append(
            {
                "gate_id": gate_id,
                "state": state,
                "evidence_ref": evidence_ref,
                "evidence_sha256": evidence_sha256,
            }
        )
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


def _assert_conversation_accepts_new_artifact_locked(conn: Any, conversation_id: str) -> None:
    row = conn.execute(
        "SELECT artifact_id, revision, payload_json FROM execution_entry_artifacts "
        "WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return
    artifact = _row_payload(row)
    _verify_hash(artifact, "artifact_sha256", "ERROR_EXECUTION_ENTRY_ARTIFACT_CORRUPT")
    _validate_artifact_identity(artifact)
    decision_row = conn.execute(
        "SELECT payload_json FROM execution_entry_decisions WHERE artifact_id = ? AND revision = ? "
        "ORDER BY rowid DESC LIMIT 1",
        (artifact["artifact_id"], artifact["revision"]),
    ).fetchone()
    if decision_row is None:
        raise ExecutionEntryError(
            "ERROR_EXECUTION_ENTRY_EXISTING_ARTIFACT",
            f"{artifact['artifact_id']} revision {artifact['revision']} is still unresolved",
        )
    decision = _row_payload(decision_row)
    _verify_hash(decision, "receipt_sha256", "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT")
    run_id = str(decision.get("run_id") or "")
    if decision.get("state") != "DISPATCHED" or not run_id:
        raise ExecutionEntryError(
            "ERROR_EXECUTION_ENTRY_EXISTING_ARTIFACT",
            f"{artifact['artifact_id']} revision {artifact['revision']} has decision state {decision.get('state')}",
        )
    run_row = conn.execute("SELECT state FROM work_runs WHERE run_id = ?", (run_id,)).fetchone()
    if run_row is None or str(run_row["state"]) not in {"COMPLETED", "FAILED", "CANCELLED"}:
        state = "MISSING" if run_row is None else str(run_row["state"])
        raise ExecutionEntryError(
            "ERROR_EXECUTION_ENTRY_EXISTING_ARTIFACT",
            f"{artifact['artifact_id']} revision {artifact['revision']} run {run_id} is {state}",
        )


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
    authorized_actors: list[str] | None = None,
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
    if get_conversation(conversation_id, cfg) is None:
        raise ExecutionEntryError("ERROR_CONVERSATION_NOT_FOUND", conversation_id)
    authorized = sorted({str(value).strip() for value in (authorized_actors or [actor]) if str(value).strip()})
    if actor not in authorized:
        authorized.append(actor)
        authorized.sort()
    source_commit = _require_source_commit(source_commit)
    _assert_runtime_source(source_commit, cfg)
    if runtime_mode not in {"plan", "ask", "auto"}:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_RUNTIME_MODE", runtime_mode)
    gates = _validate_gates(required_gates)
    now = utc_now()
    artifact_id = stable_id(
        "entry", mode, objective_id, conversation_id, source_commit, now, uuid.uuid4().hex, length=16
    )
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
        "authorized_actors": authorized,
        "policy_version": policy_version,
        "source_commit": source_commit,
        "predecessor_revision": None,
        "created_at": now,
    }
    payload["artifact_sha256"] = _canonical_hash(payload)
    conn = _tx(cfg)
    try:
        _assert_conversation_accepts_new_artifact_locked(conn, conversation_id)
        conn.execute(
            "INSERT INTO execution_entry_artifacts (artifact_id, revision, mode, state, conversation_id, source_commit, payload_json) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (artifact_id, mode, payload["state"], conversation_id, source_commit, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
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
    payload = _row_payload(row)
    _verify_hash(payload, "artifact_sha256", "ERROR_EXECUTION_ENTRY_ARTIFACT_CORRUPT")
    _validate_artifact_identity(payload)
    return payload


def current_conversation_entry(conversation_id: str, cfg: Settings = settings) -> dict[str, Any] | None:
    ensure_db(cfg.sqlite_path)
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_artifacts WHERE conversation_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return None
    payload = _row_payload(row)
    _verify_hash(payload, "artifact_sha256", "ERROR_EXECUTION_ENTRY_ARTIFACT_CORRUPT")
    return payload


def current_conversation_entry_context(conversation_id: str, cfg: Settings = settings) -> dict[str, Any]:
    entry = current_conversation_entry(conversation_id, cfg)
    if entry is None:
        return {"status": "EXECUTION_ENTRY_NONE", "entry": None, "decision": None}
    with get_connection(cfg.sqlite_path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions "
            "WHERE artifact_id = ? AND revision = ? ORDER BY rowid DESC LIMIT 1",
            (entry["artifact_id"], entry["revision"]),
        ).fetchone()
    decision = None
    run = None
    if row is not None:
        decision = _row_payload(row)
        _verify_hash(decision, "receipt_sha256", "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT")
        if decision.get("run_id"):
            try:
                run = get_run(str(decision["run_id"]), cfg)
            except RunError:
                run = None
    runtime_source_commit = None
    source_identity_status = "SOURCE_IDENTITY_OK"
    try:
        runtime_source_commit = _runtime_source_commit(cfg)
    except ExecutionEntryError as exc:
        source_identity_status = exc.status
    source_matches_runtime = runtime_source_commit == entry["source_commit"] if runtime_source_commit else False
    if runtime_source_commit and not source_matches_runtime:
        source_identity_status = "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT"
    return {
        "status": "EXECUTION_ENTRY_OK",
        "entry": entry,
        "decision": decision,
        "run": run,
        "runtime_source_commit": runtime_source_commit,
        "source_matches_runtime": source_matches_runtime,
        "source_identity_status": source_identity_status,
    }


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
        _verify_hash(payload, "artifact_sha256", "ERROR_EXECUTION_ENTRY_ARTIFACT_CORRUPT")
        _validate_artifact_identity(payload)
        _assert_runtime_source(payload["source_commit"], cfg)
        if actor not in payload.get("authorized_actors", []):
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_UNAUTHORIZED_ACTOR", actor)
        if payload["version"] != expected_version:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_VERSION_CONFLICT", f"expected={expected_version} actual={payload['version']}")
        latest = conn.execute(
            "SELECT MAX(revision) AS revision FROM execution_entry_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if latest is None or int(latest["revision"] or 0) != revision:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_STALE_REVISION", artifact_id)
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
    cfg: Settings,
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
    _assert_runtime_source(next_payload["source_commit"], cfg)
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
        _verify_hash(artifact, "artifact_sha256", "ERROR_EXECUTION_ENTRY_ARTIFACT_CORRUPT")
        _validate_artifact_identity(artifact)
        if actor not in artifact.get("authorized_actors", []):
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_UNAUTHORIZED_ACTOR", actor)
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
            _verify_hash(existing, "receipt_sha256", "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT")
            conn.execute("COMMIT")
            return existing
        if artifact["version"] != expected_version:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_VERSION_CONFLICT", f"expected={expected_version} actual={artifact['version']}")
        if artifact["source_commit"] != source_commit:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_SOURCE_DRIFT", f"expected={artifact['source_commit']} observed={source_commit}")
        _assert_runtime_source(artifact["source_commit"], cfg)
        latest = conn.execute(
            "SELECT MAX(revision) AS revision FROM execution_entry_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if latest is None or int(latest["revision"] or 0) != revision:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_STALE_REVISION", artifact_id)
        if action in {"Proceed", "Execute"}:
            active_decision = conn.execute(
                "SELECT decision_id FROM execution_entry_decisions WHERE artifact_id = ? AND revision = ? "
                "AND state IN ('CLAIMED','DISPATCHED') LIMIT 1",
                (artifact_id, revision),
            ).fetchone()
            if active_decision is not None:
                raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_ALREADY_CLAIMED", str(active_decision["decision_id"]))

        decision_id = stable_id("entrydec", artifact_id, str(revision), action, idempotency_key, length=18)
        now = utc_now()
        if action in {"Adjust", "Revise"}:
            revised = _create_revision_locked(
                conn,
                artifact,
                actor=actor,
                changes=dict(changes or {}),
                cfg=cfg,
            )
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
    payload = _row_payload(row)
    _verify_hash(payload, "receipt_sha256", "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT")
    return payload


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
    _assert_runtime_source(artifact["source_commit"], cfg)
    try:
        result = submit_message(
            artifact["conversation_id"],
            artifact["prompt"],
            mode=artifact["runtime_mode"],
            options={
                **artifact["options"],
                "execution_entry": {
                    "decision_id": decision_id,
                    "artifact_id": artifact["artifact_id"],
                    "revision": artifact["revision"],
                    "mode": artifact["mode"],
                    "source_commit": artifact["source_commit"],
                    "artifact_sha256": artifact["artifact_sha256"],
                },
            },
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


def resume_interrupted_entry_decision(
    decision_id: str,
    *,
    actor: str,
    idempotency_key: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    decision = get_entry_decision(decision_id, cfg)
    artifact = get_entry_artifact(decision["artifact_id"], decision["revision"], cfg)
    _assert_runtime_source(artifact["source_commit"], cfg)
    if actor not in artifact.get("authorized_actors", []):
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_UNAUTHORIZED_ACTOR", actor)
    idempotency_key = (idempotency_key or "").strip()
    if len(idempotency_key) < 8:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_INPUT", "resume idempotency_key must be at least 8 characters")
    if decision.get("resume_idempotency_key") == idempotency_key:
        if decision.get("run_id"):
            try:
                decision["run"] = get_run(str(decision["run_id"]), cfg)
            except RunError:
                pass
        return decision
    run_id = str(decision.get("run_id") or "")
    if not run_id:
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_RESUME_STATE", "decision has no dispatched run")
    run = get_run(run_id, cfg)
    if run["state"] != "INTERRUPTED":
        raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_RESUME_STATE", run["state"])
    result = submit_message(
        artifact["conversation_id"],
        artifact["prompt"],
        mode=artifact["runtime_mode"],
        options={
            **artifact["options"],
            "execution_entry": {
                "decision_id": decision_id,
                "artifact_id": artifact["artifact_id"],
                "revision": artifact["revision"],
                "mode": artifact["mode"],
                "source_commit": artifact["source_commit"],
                "resume_of_run_id": run_id,
            },
        },
        idempotency_key=f"entry-resume:{decision_id}:{run_id}",
        require_idle=True,
        cfg=cfg,
    )
    next_run = result["run"]
    conn = _tx(cfg)
    try:
        row = conn.execute(
            "SELECT payload_json FROM execution_entry_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise ExecutionEntryError("ERROR_EXECUTION_ENTRY_DECISION_NOT_FOUND", decision_id)
        current = _row_payload(row)
        _verify_hash(current, "receipt_sha256", "ERROR_EXECUTION_ENTRY_RECEIPT_CORRUPT")
        current_run_id = str(current.get("run_id") or "")
        if current_run_id != run_id:
            if current_run_id == next_run["run_id"]:
                conn.execute("COMMIT")
                current["run"] = next_run
                return current
            raise ExecutionEntryError(
                "ERROR_EXECUTION_ENTRY_RESUME_CONFLICT",
                f"expected interrupted run {run_id}; decision now points to {current_run_id or 'none'}",
            )
        prior = list(current.get("prior_run_ids") or [])
        if run_id not in prior:
            prior.append(run_id)
        current["prior_run_ids"] = prior
        current["run_id"] = next_run["run_id"]
        current["resume_count"] = int(current.get("resume_count") or 0) + 1
        current["resumed_at"] = utc_now()
        current["resume_idempotency_key"] = idempotency_key
        current["receipt_sha256"] = _canonical_hash({k: v for k, v in current.items() if k != "receipt_sha256"})
        conn.execute(
            "UPDATE execution_entry_decisions SET payload_json = ? WHERE decision_id = ?",
            (json.dumps(current, ensure_ascii=False, sort_keys=True), decision_id),
        )
        conn.execute("COMMIT")
        current["run"] = next_run
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
    rejected_conflicts: list[str] = []
    for row in rows:
        decision_id = str(row["decision_id"])
        try:
            result = dispatch_entry_decision(decision_id, cfg)
        except ExecutionEntryError as exc:
            if exc.status != "ERROR_EXECUTION_ENTRY_CONFLICTING_RUN":
                raise
            rejected_conflicts.append(decision_id)
            continue
        if result.get("state") == "DISPATCHED":
            dispatched.append(decision_id)
    return {
        "status": "EXECUTION_ENTRY_RECOVERY_OK" if not rejected_conflicts else "EXECUTION_ENTRY_RECOVERY_PARTIAL",
        "dispatched": dispatched,
        "rejected_conflicts": rejected_conflicts,
    }
