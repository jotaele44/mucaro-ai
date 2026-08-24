from __future__ import annotations

import re
from pathlib import PurePath

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import Settings, settings
from .db import init_db, list_payloads, upsert_payload
from .execution_gate import analyze_task, approve_execution
from .ingestion import get_chunks, get_sources, ingest_file
from .models import ApprovalRequest, AskRequest, PlanRequest
from .services import answer_question
from .validation import validate_project_json

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
    app = FastAPI(title="Mucaro AI", version="0.1.4")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    cfg.ensure_dirs()
    init_db(cfg.sqlite_path)

    @app.get("/health")
    def health() -> dict:
        return {"status": "OK", "model_provider": cfg.default_model_provider, "project_root": str(cfg.project_root)}

    @app.post("/plan")
    def plan(req: PlanRequest) -> dict:
        gate = analyze_task(req.user_request, inputs_available=req.inputs_available, approval_state=req.approval_state)
        upsert_payload("execution_gates", "task_id", gate, cfg.sqlite_path)
        return gate

    @app.post("/approve")
    def approve(req: ApprovalRequest) -> dict:
        try:
            gate = approve_execution(req.gate, req.approved_mode)
        except ValueError as exc:
            raise api_error("ERROR_INVALID_APPROVAL_MODE", str(exc)) from exc
        upsert_payload("execution_gates", "task_id", gate, cfg.sqlite_path)
        return gate

    @app.post("/ingest")
    async def ingest(upload: UploadFile = File(...)) -> dict:
        safe_name = sanitize_upload_filename(upload.filename)
        target = cfg.input_dir / safe_name
        content = await read_limited_upload(upload, cfg.max_upload_bytes)
        target.write_bytes(content)
        try:
            return ingest_file(target, cfg)
        except Exception as exc:
            raise api_error("ERROR_INGEST_FAILED", type(exc).__name__, filename=safe_name) from exc

    @app.get("/sources")
    def sources() -> dict:
        return {"sources": get_sources(cfg)}

    @app.get("/chunks")
    def chunks() -> dict:
        return {"chunks": get_chunks(cfg)}

    @app.post("/ask")
    def ask(req: AskRequest) -> dict:
        return answer_question(
            req.query,
            top_k=req.top_k,
            retrieval_strategy=req.retrieval_strategy,
            model_provider=req.model_provider,
            model_name=req.model_name,
            allow_mock_fallback=req.allow_mock_fallback,
            cfg=cfg,
        )

    @app.get("/runs")
    def runs() -> dict:
        return {"runs": list_payloads("runs", cfg.sqlite_path)}

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
