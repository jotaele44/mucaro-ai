from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.error_categories import classify_error
from backend.app.main import create_app
from backend.tests.test_api import make_settings


def _client(root: Path, *, operator_token: str | None = None) -> TestClient:
    return TestClient(create_app(make_settings(root, operator_token=operator_token)))


def _assert_categorized_error(response, *, status: str, category: str) -> None:
    payload = response.json()["detail"]
    assert payload["status"] == status
    assert payload["category"] == category
    assert payload["category"] == classify_error(status)
    assert isinstance(payload["detail"], str) and payload["detail"]


def test_bad_request_api_error_includes_input_category(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/ingest", files={"upload": ("tool.exe", b"x", "application/octet-stream")})

    assert response.status_code == 400
    _assert_categorized_error(
        response,
        status="ERROR_UNSUPPORTED_FILE_TYPE",
        category="UNSUPPORTED_FORMAT",
    )


def test_not_found_api_error_includes_source_category(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/sources/src_missing")

    assert response.status_code == 404
    _assert_categorized_error(
        response,
        status="ERROR_SOURCE_NOT_FOUND",
        category="SOURCE_UNAVAILABLE",
    )


def test_permission_api_error_includes_permission_category(tmp_path: Path) -> None:
    project = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    (external / "source.md").write_text("external", encoding="utf-8")
    client = _client(project)

    response = client.post("/ingest-path", json={"path": str(external)})

    assert response.status_code == 403
    _assert_categorized_error(
        response,
        status="ERROR_DIRECTORY_PERMISSION_REQUIRED",
        category="PERMISSION_DENIED",
    )


def test_operator_auth_error_includes_permission_category(tmp_path: Path) -> None:
    client = _client(tmp_path, operator_token="secret-token")

    response = client.get("/memory/export")

    assert response.status_code == 401
    _assert_categorized_error(
        response,
        status="ERROR_OPERATOR_AUTH_REQUIRED",
        category="PERMISSION_DENIED",
    )


def test_remote_access_token_requirement_is_permission_denied() -> None:
    assert classify_error("ERROR_REMOTE_ACCESS_REQUIRES_TOKEN") == "PERMISSION_DENIED"
