from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import PUBLIC_API_PATHS
from backend.app.main import create_app
from backend.tests.test_api import make_settings

TOKEN = "t" * 40

PROTECTED_READS = [
    ("get", "/memory", None),
    ("get", "/memory/export", None),
    ("get", "/tools/requests", None),
    ("get", "/tools/audit", None),
    ("get", "/chunks", None),
    ("get", "/sources", None),
    ("get", "/runs", None),
    ("get", "/runs/summary", None),
    ("get", "/conversations", None),
    ("get", "/permissions/directories", None),
    ("get", "/workspaces", None),
    ("get", "/exports/run_missing", None),
    ("post", "/permissions/directories/check", {"path": "/etc"}),
    ("post", "/validate", None),
]


def _client(tmp_path: Path, token: str | None = TOKEN) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, operator_token=token)))


@pytest.mark.parametrize("method,path,body", PROTECTED_READS)
def test_data_bearing_routes_reject_missing_token_when_configured(tmp_path, method, path, body):
    client = _client(tmp_path)
    response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
    assert response.status_code == 401
    assert response.json()["detail"]["status"] == "ERROR_OPERATOR_AUTH_REQUIRED"


@pytest.mark.parametrize("method,path,body", PROTECTED_READS)
def test_data_bearing_routes_accept_correct_token(tmp_path, method, path, body):
    client = _client(tmp_path)
    headers = {"X-Mucaro-Token": TOKEN}
    response = (
        getattr(client, method)(path, json=body, headers=headers)
        if body is not None
        else getattr(client, method)(path, headers=headers)
    )
    assert response.status_code != 401


def test_wrong_tokens_are_rejected_including_prefix_and_length_variants(tmp_path):
    client = _client(tmp_path)
    for supplied in ["", TOKEN[:-1], TOKEN + "x", TOKEN.upper(), "Bearer"]:
        assert client.get("/memory/export", headers={"X-Mucaro-Token": supplied}).status_code == 401
        assert client.get("/memory/export", headers={"Authorization": f"Bearer {supplied}"}).status_code == 401
    assert client.get("/memory/export", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_status_routes_remain_public_so_clients_can_discover_auth(tmp_path):
    client = _client(tmp_path)
    for path in sorted(PUBLIC_API_PATHS):
        assert client.get(path).status_code == 200, path
    assert client.get("/config").json()["auth"]["auth_required"] is True


def test_routes_stay_open_when_no_token_is_configured(tmp_path):
    client = _client(tmp_path, token=None)
    assert client.get("/memory/export").status_code == 200
    assert client.get("/tools/audit").status_code == 200


def test_static_frontend_is_served_without_token(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Mucaro</title>", encoding="utf-8")
    client = _client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Mucaro" in response.text
