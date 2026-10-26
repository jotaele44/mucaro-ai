from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from backend.app.conversations import create_conversation
from backend.app.main import create_app
from backend.tests.test_api import make_settings

SHA = "a" * 40
OTHER_SHA = "b" * 40
def _gate(gate_id: str) -> dict[str, str]:
    evidence = f"fixture://execution-entry-api/{gate_id}"
    return {
        "gate_id": gate_id,
        "state": "PASS",
        "evidence_ref": evidence,
        "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
    }


GATES = [_gate("scope"), _gate("authorization")]


def _client(tmp_path, monkeypatch, *, token: str | None = None):
    monkeypatch.setenv("MUCARO_SOURCE_HEAD", SHA)
    cfg = make_settings(tmp_path, operator_token=token)
    cfg.ensure_dirs()
    conversation = create_conversation("entry api", cfg)["conversation_id"]
    return TestClient(create_app(cfg)), conversation


def _artifact_payload(conversation_id: str) -> dict:
    return {
        "mode": "GOAL",
        "objective_id": "api-goal",
        "conversation_id": conversation_id,
        "prompt": "Run the governed API proof.",
        "runtime_mode": "plan",
        "options": {"model_provider": "mock"},
        "source_commit": SHA,
        "required_gates": GATES,
        "policy_version": "execution-entry-v1",
        "actor": "forged-client-actor",
        "authorized_actors": ["forged-client-actor"],
    }


def test_local_api_ignores_forged_actor_and_binds_local_operator(tmp_path, monkeypatch) -> None:
    client, conversation_id = _client(tmp_path, monkeypatch)
    created = client.post("/execution-entry/artifacts", json=_artifact_payload(conversation_id))
    assert created.status_code == 200
    artifact = created.json()
    assert artifact["actor"] == "local_operator"
    assert artifact["authorized_actors"] == ["local_operator"]

    approved = client.post(
        f"/execution-entry/artifacts/{artifact['artifact_id']}/1/state",
        json={"expected_version": 1, "target_state": "VECTOR_READY", "actor": "forged"},
    )
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "local_operator"

    decided = client.post(
        f"/execution-entry/artifacts/{artifact['artifact_id']}/1/decisions",
        json={
            "action": "Proceed",
            "expected_version": 2,
            "idempotency_key": "api-local-proceed",
            "source_commit": SHA,
            "changes": {},
            "actor": "forged",
        },
    )
    assert decided.status_code == 200
    decision = decided.json()
    assert decision["actor"] == "local_operator"
    assert decision["state"] == "DISPATCHED"


def test_token_authenticated_api_binds_operator_principal(tmp_path, monkeypatch) -> None:
    client, conversation_id = _client(tmp_path, monkeypatch, token="secret-token")
    headers = {"Authorization": "Bearer secret-token"}
    created = client.post(
        "/execution-entry/artifacts",
        json=_artifact_payload(conversation_id),
        headers=headers,
    )
    assert created.status_code == 200
    artifact = created.json()
    assert artifact["actor"] == "operator"
    assert artifact["authorized_actors"] == ["operator"]

    unauthenticated = client.post(
        f"/execution-entry/artifacts/{artifact['artifact_id']}/1/state",
        json={"expected_version": 1, "target_state": "VECTOR_READY"},
    )
    assert unauthenticated.status_code == 401

    approved = client.post(
        f"/execution-entry/artifacts/{artifact['artifact_id']}/1/state",
        json={"expected_version": 1, "target_state": "VECTOR_READY"},
        headers=headers,
    )
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == "operator"


def test_api_fails_closed_on_runtime_source_drift(tmp_path, monkeypatch) -> None:
    client, conversation_id = _client(tmp_path, monkeypatch)
    payload = _artifact_payload(conversation_id)
    payload["source_commit"] = OTHER_SHA
    response = client.post("/execution-entry/artifacts", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "ERROR_EXECUTION_ENTRY_SOURCE_DRIFT"
