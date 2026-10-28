from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_api import make_settings


def _client(tmp_path):
    return TestClient(create_app(make_settings(tmp_path)))


def _payload(**overrides):
    payload = {
        "user_request": "Build a bounded local-first workbench plan",
        "source_commit": "A" * 40,
        "features": ["GIS", "OCR", "GIS"],
        "inputs_available": ["operator_ui"],
        "approval_state": "NOT_REQUESTED",
        "operator_approval_recorded": False,
        "profile": "maximum",
        "network": "isolated",
        "hardware": "unavailable",
        "platforms": ["macos", "linux"],
    }
    payload.update(overrides)
    return payload


def test_workbench_plan_binds_existing_gate_and_never_executes(tmp_path):
    client = _client(tmp_path)
    response = client.post("/workbench/plan", json=_payload())
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "WORKBENCH_PLAN_READY"
    assert body["authority"] == {
        "planning_compiler": "development_workbench",
        "execution_gate": "execution_gate",
        "run_runtime": "work_runs",
        "plan_only": True,
        "execution_authorized": False,
    }
    assert body["gate"]["state"] in {"PLANNING", "READY_FOR_APPROVAL"}
    assert body["gate"]["state"] != "EXECUTING"

    plan = body["workbench_plan"]
    assert plan["features"] == ["GIS", "OCR"]
    assert plan["feature_count"] == 2
    assert plan["source_commit"] == "a" * 40
    assert plan["status"] == "CANDIDATE_NOT_EXECUTION"
    assert plan["certification"] == "BLOCKED"
    assert plan["execution_authorized"] is False
    assert all(row["status"] == "NOT_EXECUTED" for row in plan["batches"])

    execution = body["execution_plan"]
    assert execution["state"] == "PLAN_ONLY"
    assert execution["certification"] == "BLOCKED"
    assert len(execution["lanes"]) == 20
    assert execution["platforms"] == ["linux", "macos"]

    runs = client.get("/work-runs?limit=20")
    assert runs.status_code == 200
    assert runs.json()["runs"] == []


def test_recorded_operator_approval_is_not_execution_authority(tmp_path):
    response = _client(tmp_path).post(
        "/workbench/plan",
        json=_payload(operator_approval_recorded=True, approval_state="APPROVED"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workbench_plan"]["operator_approval_recorded"] is True
    assert body["workbench_plan"]["execution_authorized"] is False
    assert body["authority"]["execution_authorized"] is False
    assert body["execution_plan"]["state"] == "PLAN_ONLY"
    assert body["gate"]["state"] != "EXECUTING"


def test_workbench_rejects_invalid_feature_and_duplicate_platforms(tmp_path):
    client = _client(tmp_path)

    invalid_feature = client.post("/workbench/plan", json=_payload(features=[""]))
    assert invalid_feature.status_code == 422
    assert invalid_feature.json()["detail"]["status"] == "ERROR_WORKBENCH_PLAN_INVALID"

    duplicate_platform = client.post("/workbench/plan", json=_payload(platforms=["macos", "macos"]))
    assert duplicate_platform.status_code == 422
    assert duplicate_platform.json()["detail"]["status"] == "ERROR_WORKBENCH_PLAN_INVALID"


def test_workbench_missing_source_identity_remains_explicit_residue(tmp_path):
    response = _client(tmp_path).post("/workbench/plan", json=_payload(source_commit=None))
    assert response.status_code == 200
    plan = response.json()["workbench_plan"]
    assert plan["source_commit"] is None
    assert "source_commit" in plan["missing_evidence"]
    assert plan["certification"] == "BLOCKED"
