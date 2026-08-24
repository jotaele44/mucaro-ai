from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app, sanitize_upload_filename


def make_settings(root: Path, *, max_upload_bytes: int = 25 * 1024 * 1024) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
        max_upload_bytes=max_upload_bytes,
    )


def make_client(tmp_path: Path, *, max_upload_bytes: int = 25 * 1024 * 1024) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, max_upload_bytes=max_upload_bytes)))


def test_api_happy_path_covers_operator_routes(tmp_path: Path):
    client = make_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "OK"

    plan = client.post("/plan", json={"user_request": "Add CLI capability", "inputs_available": ["mucaro-ai"]})
    assert plan.status_code == 200
    gate = plan.json()
    assert gate["task_id"].startswith("task_")

    approve = client.post("/approve", json={"gate": gate, "approved_mode": gate["recommended_mode"]})
    assert approve.status_code == 200
    assert approve.json()["state"] == "EXECUTING"

    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    assert ingest.json()["status"] == "INGEST_OK"

    sources = client.get("/sources")
    assert sources.status_code == 200
    assert len(sources.json()["sources"]) == 1

    chunks = client.get("/chunks")
    assert chunks.status_code == 200
    assert len(chunks.json()["chunks"]) >= 1

    ask = client.post(
        "/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )
    assert ask.status_code == 200
    trace = ask.json()
    assert trace["citation_status"] == "CITATION_OK"
    assert trace["retrieval_strategy"] == "persistent_tfidf_v1"
    assert trace["run_id"].startswith("run_")

    runs = client.get("/runs")
    assert runs.status_code == 200
    assert len(runs.json()["runs"]) == 1

    export = client.get(f"/exports/{trace['run_id']}")
    assert export.status_code == 200
    assert trace["run_id"] in export.json()["content"]

    validate = client.post("/validate")
    assert validate.status_code == 200
    assert validate.json()["status"] == "JSON_OK"


def test_api_ingest_rejects_empty_filename(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ingest", files={"upload": ("", b"text", "text/plain")})

    assert response.status_code == 422


def test_upload_filename_sanitizer_rejects_empty_name():
    try:
        sanitize_upload_filename("")
    except Exception as exc:
        assert exc.detail["status"] == "ERROR_EMPTY_FILENAME"


def test_api_ingest_rejects_path_traversal_filename(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ingest", files={"upload": ("../secret.md", b"text", "text/markdown")})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "ERROR_UNSAFE_FILENAME"


def test_api_ingest_rejects_unsupported_extension(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ingest", files={"upload": ("tool.exe", b"text", "application/octet-stream")})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "ERROR_UNSUPPORTED_FILE_TYPE"


def test_api_ingest_rejects_oversized_upload(tmp_path: Path):
    client = make_client(tmp_path, max_upload_bytes=4)

    response = client.post("/ingest", files={"upload": ("source.md", b"12345", "text/markdown")})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "ERROR_UPLOAD_TOO_LARGE"


def test_api_ask_without_sources_returns_structured_error(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ask", json={"query": "What is source lineage?", "model_provider": "mock"})

    assert response.status_code == 200
    assert response.json()["status"] == "ERROR_NO_SOURCES"


def test_api_invalid_top_k_is_validation_error(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ask", json={"query": "What is source lineage?", "top_k": 0})

    assert response.status_code == 422


def test_api_invalid_retrieval_strategy_is_validation_error(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ask", json={"query": "What is source lineage?", "retrieval_strategy": "not-real"})

    assert response.status_code == 422


def test_api_approve_rejects_malformed_mode(tmp_path: Path):
    client = make_client(tmp_path)
    gate = client.post("/plan", json={"user_request": "Add CLI capability"}).json()

    response = client.post("/approve", json={"gate": gate, "approved_mode": "NOT_A_MODE"})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "ERROR_INVALID_APPROVAL_MODE"


def test_api_missing_export_is_structured_404(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/exports/run_missing")

    assert response.status_code == 404
    assert response.json()["detail"]["status"] == "ERROR_EXPORT_NOT_FOUND"


def test_api_cors_default_allows_local_frontend(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
