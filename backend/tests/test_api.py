from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app, sanitize_upload_filename


def minimal_png(width: int = 2, height: int = 3) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


def make_settings(
    root: Path,
    *,
    max_upload_bytes: int = 25 * 1024 * 1024,
    operator_token: str | None = None,
) -> Settings:
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
        operator_token=operator_token,
    )


def make_client(
    tmp_path: Path,
    *,
    max_upload_bytes: int = 25 * 1024 * 1024,
    operator_token: str | None = None,
) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, max_upload_bytes=max_upload_bytes, operator_token=operator_token)))


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


def test_api_optional_operator_auth_protects_mutations(tmp_path: Path):
    client = make_client(tmp_path, operator_token="secret-token")

    config = client.get("/config")
    assert config.status_code == 200
    assert config.json()["auth"]["auth_required"] is True

    rejected = client.post("/plan", json={"user_request": "Add CLI capability"})
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["status"] == "ERROR_OPERATOR_AUTH_REQUIRED"
    assert rejected.json()["detail"]["category"] == "PERMISSION_DENIED"

    allowed = client.post(
        "/plan",
        json={"user_request": "Add CLI capability"},
        headers={"X-Mucaro-Token": "secret-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["task_id"].startswith("task_")

    bearer_allowed = client.post(
        "/conversations",
        json={"title": "Authorized chat"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert bearer_allowed.status_code == 200
    assert bearer_allowed.json()["title"] == "Authorized chat"


def test_api_validation_failure_has_actionable_category(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/plan", json={})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["status"] == "ERROR_REQUEST_VALIDATION"
    assert detail["category"] == "INPUT_INVALID"
    assert detail["detail"] == "Request validation failed."
    assert detail["errors"]
    assert all(set(row) == {"loc", "msg", "type"} for row in detail["errors"])


def test_api_framework_404_has_source_unavailable_category(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["status"] == "ERROR_HTTP_404"
    assert detail["category"] == "SOURCE_UNAVAILABLE"


def test_api_uncaught_failure_is_generic_and_categorized(tmp_path: Path):
    app = create_app(make_settings(tmp_path))

    @app.get("/_test/internal-failure")
    def internal_failure():
        raise RuntimeError("sensitive detail must not cross the API boundary")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_test/internal-failure")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "status": "ERROR_INTERNAL_REQUEST_FAILURE",
            "category": "INTERNAL_FAILURE",
            "detail": "Internal request failure.",
        }
    }
    assert "sensitive detail" not in response.text


def test_api_security_status_reports_profile(tmp_path: Path):
    client = make_client(tmp_path, operator_token="x" * 32)

    response = client.get("/security")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "SECURITY_OK"
    assert payload["deployment_profile"] == "local"
    assert payload["checks"]


def test_api_tool_runtime_requires_approval_and_audits_execution(tmp_path: Path):
    client = make_client(tmp_path)

    tools = client.get("/tools")
    assert tools.status_code == 200
    assert any(tool["name"] == "source_library.status" for tool in tools.json()["tools"])

    requested = client.post(
        "/tools/requests",
        json={"tool_name": "source_library.status", "arguments": {}, "reason": "api readiness"},
    )
    assert requested.status_code == 200
    tool_call_id = requested.json()["tool_call"]["tool_call_id"]
    assert requested.json()["tool_call"]["status"] == "PENDING_APPROVAL"

    blocked = client.post(f"/tools/requests/{tool_call_id}/run")
    assert blocked.status_code == 400
    assert blocked.json()["detail"]["status"] == "ERROR_TOOL_APPROVAL_REQUIRED"
    assert blocked.json()["detail"]["category"] == "BLOCKED_ACTION"

    approved = client.post(f"/tools/requests/{tool_call_id}/approve", json={"reason": "operator approved"})
    assert approved.status_code == 200
    assert approved.json()["tool_call"]["status"] == "APPROVED"

    executed = client.post(f"/tools/requests/{tool_call_id}/run")
    assert executed.status_code == 200
    assert executed.json()["status"] == "TOOL_CALL_EXECUTED"
    assert executed.json()["result"]["status"] == "SOURCE_LIBRARY_STATUS_OK"

    audit = client.get("/tools/audit")
    assert audit.status_code == 200
    assert [event["event"] for event in audit.json()["events"]] == [
        "TOOL_CALL_REQUESTED",
        "TOOL_CALL_APPROVED",
        "TOOL_CALL_STARTED",
        "TOOL_CALL_EXECUTED",
    ]


def test_api_tool_runtime_rejects_unknown_tools(tmp_path: Path):
    client = make_client(tmp_path)

    rejected = client.post("/tools/requests", json={"tool_name": "shell.exec", "arguments": {"cmd": "date"}})

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["status"] == "ERROR_TOOL_NOT_ALLOWED"
    assert rejected.json()["detail"]["category"] == "BLOCKED_ACTION"
    audit = client.get("/tools/audit")
    assert audit.json()["events"][0]["event"] == "TOOL_REQUEST_REJECTED"


def test_api_memory_controls_are_explicit_and_user_managed(tmp_path: Path):
    client = make_client(tmp_path)

    saved = client.post("/memory", json={"kind": "preference", "content": "Prefer concise technical answers."})
    assert saved.status_code == 200
    memory = saved.json()["memory"]
    assert saved.json()["status"] == "MEMORY_SAVED"
    assert memory["enabled"] is True
    assert memory["content"] == "Prefer concise technical answers."

    listed = client.get("/memory")
    assert listed.status_code == 200
    assert listed.json()["memory_count"] == 1
    assert listed.json()["enabled_count"] == 1

    disabled = client.patch(f"/memory/{memory['memory_id']}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["memory"]["enabled"] is False

    exported = client.get("/memory/export")
    assert exported.status_code == 200
    assert exported.json()["status"] == "MEMORY_EXPORT_OK"
    assert exported.json()["memory_count"] == 1

    deleted = client.delete(f"/memory/{memory['memory_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "MEMORY_DELETE_OK"
    assert deleted.json()["remaining_memories"] == 0


def test_api_memory_mutations_require_operator_auth_when_enabled(tmp_path: Path):
    client = make_client(tmp_path, operator_token="secret-token")

    rejected = client.post("/memory", json={"content": "Prefer tables."})
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["status"] == "ERROR_OPERATOR_AUTH_REQUIRED"

    allowed = client.post(
        "/memory",
        json={"content": "Prefer tables."},
        headers={"X-Mucaro-Token": "secret-token"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "MEMORY_SAVED"


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
    assert response.json()["detail"]["category"] == "UNSUPPORTED_FORMAT"


def test_api_ingest_accepts_png_metadata_upload(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ingest", files={"upload": ("sample.png", minimal_png(), "image/png")})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "INGEST_OK"
    assert payload["source"]["parser"] == "image_metadata_v1"
    assert payload["source"]["mime_type"] == "image/png"
    assert payload["source"]["parser_metadata"]["media_kind"] == "image"
    assert payload["source"]["parser_metadata"]["width"] == 2
    assert payload["source"]["parser_metadata"]["height"] == 3


def test_api_ingest_rejects_oversized_upload(tmp_path: Path):
    client = make_client(tmp_path, max_upload_bytes=4)

    response = client.post("/ingest", files={"upload": ("source.md", b"12345", "text/markdown")})

    assert response.status_code == 400
    assert response.json()["detail"]["status"] == "ERROR_UPLOAD_TOO_LARGE"


def test_api_ingest_path_requires_then_accepts_directory_permission(tmp_path: Path):
    project = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    sample = external / "source.md"
    sample.write_text("External path ingestion is permission-gated.", encoding="utf-8")
    client = make_client(project)

    denied = client.post("/ingest-path", json={"path": str(external)})
    assert denied.status_code == 403
    assert denied.json()["detail"]["status"] == "ERROR_DIRECTORY_PERMISSION_REQUIRED"

    grant = client.post(
        "/permissions/directories/grant",
        json={"path": str(external), "reason": "api regression"},
    )
    assert grant.status_code == 200
    assert grant.json()["status"] == "DIRECTORY_PERMISSION_GRANTED"

    grants = client.get("/permissions/directories")
    assert grants.status_code == 200
    assert len(grants.json()["grants"]) == 1

    ingest = client.post("/ingest-path", json={"path": str(external)})
    assert ingest.status_code == 200
    assert ingest.json()["status"] == "INGEST_FOLDER_OK"
    assert ingest.json()["directory_permission"]["status"] == "PERMISSION_OK"

    revoke = client.post("/permissions/directories/revoke", json={"path": str(external)})
    assert revoke.status_code == 200
    assert revoke.json()["status"] == "DIRECTORY_PERMISSION_REVOKED"


def test_api_ask_without_sources_returns_structured_error(tmp_path: Path):
    client = make_client(tmp_path)

    response = client.post("/ask", json={"query": "What is source lineage?", "model_provider": "mock"})

    assert response.status_code == 200
    assert response.json()["status"] == "ERROR_NO_SOURCES"


def test_api_conversation_persists_cited_chat_turns(tmp_path: Path):
    client = make_client(tmp_path)
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200

    created = client.post("/conversations", json={"title": "Source lineage chat"})
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]

    ask = client.post(
        f"/conversations/{conversation_id}/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )
    assert ask.status_code == 200
    payload = ask.json()
    assert payload["answer"]["citation_status"] == "CITATION_OK"
    assert payload["conversation"]["conversation_id"] == conversation_id
    assert [turn["role"] for turn in payload["conversation"]["turns"]] == ["user", "assistant"]
    assert payload["conversation"]["turns"][1]["run_id"] == payload["answer"]["run_id"]

    listed = client.get("/conversations")
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["conversation_id"] == conversation_id
    assert listed.json()["conversations"][0]["turn_count"] == 2

    fetched = client.get(f"/conversations/{conversation_id}")
    assert fetched.status_code == 200
    assert fetched.json()["turns"][1]["answer_trace"]["citation_status"] == "CITATION_OK"

    missing = client.post(
        "/conversations/conv_missing/ask",
        json={"query": "What does source lineage require?", "model_provider": "mock"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["status"] == "ERROR_CONVERSATION_NOT_FOUND"


def test_api_conversation_rename_and_delete_preserve_runs(tmp_path: Path):
    client = make_client(tmp_path)
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Scratch"}).json()["conversation_id"]
    ask = client.post(
        f"/conversations/{conversation_id}/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )
    assert ask.status_code == 200
    run_id = ask.json()["answer"]["run_id"]

    renamed = client.patch(f"/conversations/{conversation_id}", json={"title": "Source Lineage Notes"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Source Lineage Notes"

    listed = client.get("/conversations")
    assert listed.status_code == 200
    assert listed.json()["conversations"][0]["title"] == "Source Lineage Notes"

    deleted = client.delete(f"/conversations/{conversation_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "CONVERSATION_DELETE_OK"
    assert client.get(f"/conversations/{conversation_id}").status_code == 404

    run_detail = client.get(f"/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["run_id"] == run_id

    missing_delete = client.delete(f"/conversations/{conversation_id}")
    assert missing_delete.status_code == 404
    assert missing_delete.json()["detail"]["status"] == "ERROR_CONVERSATION_NOT_FOUND"


def test_api_conversation_follow_up_uses_prior_turn_context(tmp_path: Path):
    client = make_client(tmp_path)
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    conversation_id = client.post("/conversations", json={"title": "Follow-up chat"}).json()["conversation_id"]

    first = client.post(
        f"/conversations/{conversation_id}/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )
    assert first.status_code == 200
    assert first.json()["answer"]["conversation_context_turn_count"] == 0

    second = client.post(
        f"/conversations/{conversation_id}/ask",
        json={
            "query": "Using source lineage, answer with my previous question in mind.",
            "retrieval_strategy": "persistent",
            "model_provider": "mock",
        },
    )
    assert second.status_code == 200
    answer = second.json()["answer"]
    assert answer["citation_status"] == "CITATION_OK"
    assert answer["prompt_template_id"] == "default_cited_rag_with_history_v1"
    assert answer["conversation_context_turn_count"] == 2
    assert "Conversation context turns used: 2" in answer["answer"]
    assert "What does source lineage require?" in answer["answer"]


def test_api_ask_uses_enabled_memory_without_treating_it_as_source(tmp_path: Path):
    client = make_client(tmp_path)
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    saved = client.post("/memory", json={"kind": "preference", "content": "Prefer short answers."})
    assert saved.status_code == 200
    memory_id = saved.json()["memory"]["memory_id"]

    ask = client.post(
        "/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )

    assert ask.status_code == 200
    payload = ask.json()
    assert payload["citation_status"] == "CITATION_OK"
    assert payload["memory_context_count"] == 1
    assert payload["memory_ids"] == [memory_id]
    assert "Memory items used: 1" in payload["answer"]
    assert memory_id not in payload["source_ids"]


def test_api_source_management_run_detail_and_workspace_profiles(tmp_path: Path):
    client = make_client(tmp_path)
    ingest = client.post(
        "/ingest",
        files={"upload": ("source.md", b"Source lineage requires cited chunk IDs and source IDs.", "text/markdown")},
    )
    assert ingest.status_code == 200
    source_id = ingest.json()["source"]["source_id"]

    sources = client.get("/sources")
    assert sources.status_code == 200
    assert sources.json()["index"]["status"] == "INDEX_OK"

    reindex = client.post("/sources/reindex")
    assert reindex.status_code == 200
    assert reindex.json()["status"] == "REINDEX_OK"

    ask = client.post(
        "/ask",
        json={"query": "What does source lineage require?", "retrieval_strategy": "persistent", "model_provider": "mock"},
    )
    assert ask.status_code == 200
    run_id = ask.json()["run_id"]
    run_detail = client.get(f"/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["run_id"] == run_id
    run_summary = client.get("/runs/summary")
    assert run_summary.status_code == 200
    assert run_summary.json()["status"] == "RUN_HISTORY_OK"
    assert run_summary.json()["by_retrieval_strategy"]["persistent_tfidf_v1"] == 1

    workspaces = client.get("/workspaces")
    assert workspaces.status_code == 200
    assert any(profile["name"] == "default" for profile in workspaces.json()["workspaces"])
    saved = client.post("/workspaces", json={"name": "research", "notes": "local corpus"})
    assert saved.status_code == 200
    assert saved.json()["profile"]["name"] == "research"

    deleted = client.delete(f"/sources/{source_id}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "SOURCE_DELETE_OK"
    assert deleted.json()["removed_chunks"] >= 1
    assert client.get("/sources").json()["sources"] == []

    missing_delete = client.delete(f"/sources/{source_id}")
    assert missing_delete.status_code == 404
    assert missing_delete.json()["detail"]["status"] == "ERROR_SOURCE_NOT_FOUND"


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
