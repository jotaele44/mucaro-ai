from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db, list_payloads, upsert_payload
from .execution_gate import analyze_task, approve_execution
from .ingestion import get_chunks, get_sources, ingest_file
from .models import ApprovalRequest, AskRequest, PlanRequest
from .services import answer_question
from .validation import validate_project_json

app = FastAPI(title="Mucaro AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
settings.ensure_dirs()
init_db(settings.sqlite_path)


@app.get("/health")
def health() -> dict:
    return {"status": "OK", "model_provider": settings.default_model_provider, "project_root": str(settings.project_root)}


@app.post("/plan")
def plan(req: PlanRequest) -> dict:
    gate = analyze_task(req.user_request, inputs_available=req.inputs_available, approval_state=req.approval_state)
    upsert_payload("execution_gates", "task_id", gate, settings.sqlite_path)
    return gate


@app.post("/approve")
def approve(req: ApprovalRequest) -> dict:
    gate = approve_execution(req.gate, req.approved_mode)
    upsert_payload("execution_gates", "task_id", gate, settings.sqlite_path)
    return gate


@app.post("/ingest")
async def ingest(upload: UploadFile = File(...)) -> dict:
    if not upload.filename:
        raise HTTPException(status_code=400, detail="ERROR_EMPTY_FILENAME")
    target = settings.input_dir / upload.filename
    content = await upload.read()
    target.write_bytes(content)
    try:
        return ingest_file(target, settings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"ERROR_INGEST_FAILED:{exc}") from exc


@app.get("/sources")
def sources() -> dict:
    return {"sources": get_sources(settings)}


@app.get("/chunks")
def chunks() -> dict:
    return {"chunks": get_chunks(settings)}


@app.post("/ask")
def ask(req: AskRequest) -> dict:
    return answer_question(
        req.query,
        top_k=req.top_k,
        model_provider=req.model_provider,
        model_name=req.model_name,
        cfg=settings,
    )


@app.get("/runs")
def runs() -> dict:
    return {"runs": list_payloads("runs", settings.sqlite_path)}


@app.get("/exports/{run_id}")
def exports(run_id: str) -> dict:
    path = settings.export_dir / f"{run_id}.answer_trace.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="ERROR_EXPORT_NOT_FOUND")
    return {"run_id": run_id, "path": str(path), "content": path.read_text(encoding="utf-8")}


@app.post("/validate")
def validate() -> dict:
    return validate_project_json(settings)


FRONTEND_DIST = settings.project_root / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
