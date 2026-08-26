from __future__ import annotations

import re
from pathlib import Path, PurePath

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .auth import auth_status, require_operator_auth
from .config import Settings, settings
from .conversations import (
    append_answer_turn,
    build_conversation_context,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)
from .db import init_db, list_payloads, upsert_payload
from .execution_gate import analyze_task, approve_execution
from .ingestion import delete_source, get_chunks, get_index_status, get_sources, ingest_file, ingest_folder, reindex_sources
from .models import (
    ApprovalRequest,
    AskRequest,
    ConversationCreateRequest,
    ConversationUpdateRequest,
    DirectoryPermissionRequest,
    IngestPathRequest,
    PlanRequest,
    WorkspaceProfileRequest,
)
from .permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)
from .services import answer_question
from .validation import validate_project_json
from .workspaces import list_workspace_profiles, upsert_workspace_profile

SUPPORTED_UPLOAD_SUFFIXES = {".txt", ".md", ".csv", ".json", ".pdf"}
GENERATED_NAME_PATTERN = re.compile(r"^(src_|run_|chunk_)", re.IGNORECASE)


def api_error(status: str, detail: str, *, status_code: int = 400, **extra: object) -> HTTPException:
    payload = {"status": status, "detail": detail}
    payload.update(extra)
    return HTTPException(status_code=status_code, detail=payload)


def sanitize_upload_filename(filename: str | None) -> str:
    raw = (filename or "").strip()
    if not raw:
        raise api_error("ERROR_EMPTY_FILENAME", "Upload filename is required.")
    parts = [part for part in re.split(r"[\\/]+", raw) if part]
    if any(part in {".", ".."} for part in parts):
        raise api_error("ERROR_UNSAFE_FILENAME", "Upload filename must not contain traversal segments.")
    name = PurePath(parts[-1] if parts else raw).name
    if not name or name.startswith(".") or GENERATED_NAME_PATTERN.match(name):
        raise api_error("ERROR_UNSAFE_FILENAME", "Upload filename is hidden, generated, or empty.", filename=name)
    suffix = PurePath(name).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise api_error(
            "ERROR_UNSUPPORTED_FILE_TYPE",
            f"Supported file types: {', '.join(sorted(SUPPORTED_UPLOAD_SUFFIXES))}.",
            filename=name,
        )
    return name


async def read_limited_upload(upload: UploadFile, max_bytes: int) -> bytes:
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise api_error(
            "ERROR_UPLOAD_TOO_LARGE",
            f"Upload exceeds {max_bytes} bytes.",
            max_upload_bytes=max_bytes,
        )
    return content


def create_app(cfg: Settings = settings) -> FastAPI:
    app = FastAPI(title="Mucaro AI", version="0.1.16")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)

    def require_auth(
        authorization: str | None = Header(default=None),
        x_mucaro_token: str | None = Header(default=None),
    ) -> dict:
        return require_operator_auth(cfg, authorization=authorization, x_mucaro_token=x_mucaro_token)

    @app.get("/health")
    def health() -> dict:
        return {"status": "OK", "model_provider": cfg.default_model_provider, "project_root": str(cfg.project_root)}

    @app.get("/config")
    def config_status() -> dict:
        return {
            "status": "CONFIG_OK",
            "project_root": str(cfg.project_root),
            "data_dir": str(cfg.data_dir),
            "workspace_profile": cfg.workspace_profile,
            "model_provider": cfg.default_model_provider,
            "model_name": cfg.default_model_name,
            "max_upload_bytes": cfg.max_upload_bytes,
            "cors_origins": list(cfg.cors_origins),
            "auth": auth_status(cfg),
        }

    @app.post("/plan")
    def plan(req: PlanRequest, _auth: dict = Depends(require_auth)) -> dict:
        gate = analyze_task(req.user_request, inputs_available=req.inputs_available, approval_state=req.approval_state)
        upsert_payload("execution_gates", "task_id", gate, cfg.sqlite_path)
        return gate

    @app.post("/approve")
    def approve(req: ApprovalRequest, _auth: dict = Depends(require_auth)) -> dict:
        try:
            gate = approve_execution(req.gate, req.approved_mode)
        except ValueError as exc:
            raise api_error("ERROR_INVALID_APPROVAL_MODE", str(exc)) from exc
        upsert_payload("execution_gates", "task_id", gate, cfg.sqlite_path)
        return gate

    @app.post("/ingest")
    async def ingest(upload: UploadFile = File(...), _auth: dict = Depends(require_auth)) -> dict:
        safe_name = sanitize_upload_filename(upload.filename)
        target = cfg.input_dir / safe_name
        content = await read_limited_upload(upload, cfg.max_upload_bytes)
        target.write_bytes(content)
        try:
            return ingest_file(target, cfg)
        except Exception as exc:
            raise api_error("ERROR_INGEST_FAILED", type(exc).__name__, filename=safe_name) from exc

    @app.post("/ingest-path")
    def ingest_path(req: IngestPathRequest, _auth: dict = Depends(require_auth)) -> dict:
        path = Path(req.path).expanduser().resolve()
        if not path.exists():
            raise api_error("ERROR_INPUT_PATH_NOT_FOUND", str(path), status_code=404)
        if not path.is_file() and not path.is_dir():
            raise api_error("ERROR_INPUT_PATH_UNSUPPORTED", str(path))
        permission = check_directory_permission(path, cfg)
        if permission["status"] != "PERMISSION_OK":
            raise api_error(str(permission["status"]), str(permission["detail"]), status_code=403, path=permission["path"])
        try:
            result = ingest_folder(path, cfg) if path.is_dir() else ingest_file(path, cfg)
        except Exception as exc:
            raise api_error("ERROR_INGEST_FAILED", type(exc).__name__, path=str(path)) from exc
        result["directory_permission"] = permission
        return result

    @app.get("/permissions/directories")
    def directory_permissions() -> dict:
        return {"status": "DIRECTORY_PERMISSIONS_OK", "grants": load_directory_grants(cfg)}

    @app.post("/permissions/directories/check")
    def check_directory(req: DirectoryPermissionRequest) -> dict:
        try:
            return check_directory_permission(req.path, cfg)
        except DirectoryPermissionError as exc:
            raise api_error(exc.status, exc.detail, status_code=400) from exc

    @app.post("/permissions/directories/grant")
    def grant_directory_permission(req: DirectoryPermissionRequest, _auth: dict = Depends(require_auth)) -> dict:
        try:
            return grant_directory(req.path, cfg, granted_by="api", reason=req.reason)
        except DirectoryPermissionError as exc:
            raise api_error(exc.status, exc.detail, status_code=400) from exc

    @app.post("/permissions/directories/revoke")
    def revoke_directory_permission(req: DirectoryPermissionRequest, _auth: dict = Depends(require_auth)) -> dict:
        try:
            return revoke_directory(req.path, cfg)
        except DirectoryPermissionError as exc:
            raise api_error(exc.status, exc.detail, status_code=400) from exc

    @app.get("/sources")
    def sources() -> dict:
        return {"sources": get_sources(cfg), "index": get_index_status(cfg)}

    @app.post("/sources/reindex")
    def reindex(_auth: dict = Depends(require_auth)) -> dict:
        return reindex_sources(cfg)

    @app.delete("/sources/{source_id}")
    def remove_source(source_id: str, _auth: dict = Depends(require_auth)) -> dict:
        result = delete_source(source_id, cfg)
        if result["status"] == "ERROR_SOURCE_NOT_FOUND":
            raise api_error("ERROR_SOURCE_NOT_FOUND", "Source not found.", status_code=404, source_id=source_id)
        return result

    @app.get("/chunks")
    def chunks() -> dict:
        return {"chunks": get_chunks(cfg)}

    @app.post("/ask")
    def ask(req: AskRequest, _auth: dict = Depends(require_auth)) -> dict:
        return answer_question(
            req.query,
            top_k=req.top_k,
            retrieval_strategy=req.retrieval_strategy,
            model_provider=req.model_provider,
            model_name=req.model_name,
            allow_mock_fallback=req.allow_mock_fallback,
            cfg=cfg,
        )

    @app.get("/conversations")
    def conversations() -> dict:
        return {"conversations": list_conversations(cfg)}

    @app.post("/conversations")
    def create_chat(req: ConversationCreateRequest, _auth: dict = Depends(require_auth)) -> dict:
        return create_conversation(req.title, cfg)

    @app.get("/conversations/{conversation_id}")
    def conversation(conversation_id: str) -> dict:
        payload = get_conversation(conversation_id, cfg)
        if payload is None:
            raise api_error("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.", status_code=404)
        return payload

    @app.patch("/conversations/{conversation_id}")
    def rename_chat(
        conversation_id: str,
        req: ConversationUpdateRequest,
        _auth: dict = Depends(require_auth),
    ) -> dict:
        try:
            return rename_conversation(conversation_id, req.title, cfg)
        except KeyError as exc:
            raise api_error("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.", status_code=404) from exc
        except ValueError as exc:
            raise api_error(str(exc), "Conversation title is required.") from exc

    @app.delete("/conversations/{conversation_id}")
    def delete_chat(conversation_id: str, _auth: dict = Depends(require_auth)) -> dict:
        result = delete_conversation(conversation_id, cfg)
        if result["status"] == "ERROR_CONVERSATION_NOT_FOUND":
            raise api_error("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.", status_code=404)
        return result

    @app.post("/conversations/{conversation_id}/ask")
    def ask_conversation(conversation_id: str, req: AskRequest, _auth: dict = Depends(require_auth)) -> dict:
        existing_conversation = get_conversation(conversation_id, cfg)
        if existing_conversation is None:
            raise api_error("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.", status_code=404)
        conversation_context = build_conversation_context(existing_conversation)
        answer = answer_question(
            req.query,
            top_k=req.top_k,
            retrieval_strategy=req.retrieval_strategy,
            model_provider=req.model_provider,
            model_name=req.model_name,
            allow_mock_fallback=req.allow_mock_fallback,
            conversation_context=conversation_context,
            cfg=cfg,
        )
        try:
            conversation_payload = append_answer_turn(conversation_id, req.query, answer, cfg)
        except KeyError as exc:
            raise api_error("ERROR_CONVERSATION_NOT_FOUND", "Conversation not found.", status_code=404) from exc
        return {"answer": answer, "conversation": conversation_payload}

    @app.get("/runs")
    def runs() -> dict:
        return {"runs": list_payloads("runs", cfg.sqlite_path)}

    @app.get("/runs/summary")
    def run_summary() -> dict:
        rows = list_payloads("runs", cfg.sqlite_path)
        citation_ok = sum(1 for run in rows if run.get("citation_status") == "CITATION_OK")
        by_strategy: dict[str, int] = {}
        for run in rows:
            strategy = str(run.get("retrieval_strategy") or "unknown")
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
        return {
            "status": "RUN_HISTORY_OK",
            "run_count": len(rows),
            "citation_ok": citation_ok,
            "citation_error": len(rows) - citation_ok,
            "by_retrieval_strategy": by_strategy,
            "latest_run_id": rows[-1].get("run_id") if rows else None,
        }

    @app.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict:
        for run in list_payloads("runs", cfg.sqlite_path):
            if run.get("run_id") == run_id:
                return run
        raise api_error("ERROR_RUN_NOT_FOUND", "Run not found.", status_code=404, run_id=run_id)

    @app.get("/workspaces")
    def workspaces() -> dict:
        return {"status": "WORKSPACES_OK", "workspaces": list_workspace_profiles(cfg)}

    @app.post("/workspaces")
    def save_workspace(req: WorkspaceProfileRequest, _auth: dict = Depends(require_auth)) -> dict:
        result = upsert_workspace_profile(req.name, cfg, notes=req.notes)
        if result["status"].startswith("ERROR_"):
            raise api_error(result["status"], "Workspace profile could not be saved.")
        return result

    @app.get("/exports/{run_id}")
    def exports(run_id: str) -> dict:
        path = cfg.export_dir / f"{run_id}.answer_trace.json"
        if not path.exists():
            raise api_error("ERROR_EXPORT_NOT_FOUND", "Export not found.", status_code=404, run_id=run_id)
        return {"run_id": run_id, "path": str(path), "content": path.read_text(encoding="utf-8")}

    @app.post("/validate")
    def validate() -> dict:
        return validate_project_json(cfg)

    frontend_dist = cfg.project_root / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    return app


app = create_app(settings)
