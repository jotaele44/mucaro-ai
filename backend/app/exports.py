from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .contracts import validate_contract
from .utils import write_json

# Every answer is exported as these files, named <run_id>.<suffix>; the keys are the kinds the API serves.
EXPORT_FILES: dict[str, tuple[str, str]] = {
    "json": ("answer_trace.json", "application/json"),
    "markdown": ("answer_trace.md", "text/markdown; charset=utf-8"),
    "csv": ("citations.csv", "text/csv; charset=utf-8"),
}
_RUN_ID = re.compile(r"^run_[0-9a-f]{8,64}$")


def export_file_path(run_id: str, kind: str, cfg: Settings = settings) -> Path | None:
    """The exported file of one kind for a run, or None. Only well-formed run ids map to a file name."""
    spec = EXPORT_FILES.get(kind)
    if spec is None or not _RUN_ID.fullmatch(run_id):
        return None
    path = cfg.export_dir / f"{run_id}.{spec[0]}"
    return path if path.is_file() else None


def export_answer_trace(answer_trace: dict[str, Any], cfg: Settings = settings) -> dict[str, str]:
    cfg.ensure_dirs()
    # CITATION contract: a trace and every citation record must validate before any export is written.
    validate_contract("answer_trace", answer_trace)
    for record in answer_trace.get("cited_chunks", []):
        validate_contract("citation_record", record)
    run_id = answer_trace["run_id"]
    json_path = cfg.export_dir / f"{run_id}.{EXPORT_FILES['json'][0]}"
    md_path = cfg.export_dir / f"{run_id}.{EXPORT_FILES['markdown'][0]}"
    csv_path = cfg.export_dir / f"{run_id}.{EXPORT_FILES['csv'][0]}"
    write_json(json_path, answer_trace)
    md_path.write_text(_to_markdown(answer_trace), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "run_id",
                "citation_order",
                "retrieval_rank",
                "chunk_id",
                "source_id",
                "chunk_index",
                "score",
                "retrieval_strategy",
                "source_format",
                "source_url",
                "snapshot_id",
                "snapshot_sha256",
                "content_sha256",
                "retrieval_utc",
                "text_snippet",
            ],
        )
        writer.writeheader()
        for record in answer_trace.get("cited_chunks", []):
            writer.writerow(
                {
                    "run_id": run_id,
                    "citation_order": record.get("citation_order"),
                    "retrieval_rank": record.get("retrieval_rank"),
                    "chunk_id": record.get("chunk_id"),
                    "source_id": record.get("source_id"),
                    "chunk_index": record.get("chunk_index"),
                    "score": record.get("score"),
                    "retrieval_strategy": record.get("retrieval_strategy"),
                    "source_format": record.get("source_format"),
                    "source_url": record.get("source_url"),
                    "snapshot_id": record.get("snapshot_id"),
                    "snapshot_sha256": record.get("snapshot_sha256"),
                    "content_sha256": record.get("content_sha256"),
                    "retrieval_utc": record.get("retrieval_utc"),
                    "text_snippet": record.get("text_snippet"),
                }
            )
    return {"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}


def _to_markdown(answer_trace: dict[str, Any]) -> str:
    lines = [
        f"# Answer Trace {answer_trace['run_id']}",
        "",
        f"**Query:** {answer_trace['query']}",
        f"**Model:** {answer_trace['model_provider']} / {answer_trace['model_name']}",
        f"**Confidence:** {answer_trace['confidence']}",
        f"**Validation:** {', '.join(answer_trace.get('validation_flags', [])) or 'OK'}",
        "",
        "## Answer",
        "",
        answer_trace["answer"],
        "",
        "## Cited chunks",
    ]
    for record in answer_trace.get("cited_chunks", []):
        chunk_id = record.get("chunk_id")
        source_id = record.get("source_id")
        rank = record.get("retrieval_rank")
        score = record.get("score")
        snippet = record.get("text_snippet")
        detail = f"source_id={source_id} retrieval_rank={rank}"
        if score is not None:
            detail = f"{detail} score={score}"
        if record.get("source_format"):
            detail = f"{detail} source_format={record.get('source_format')}"
        if record.get("source_url"):
            detail = f"{detail} source_url={record.get('source_url')}"
        if record.get("snapshot_sha256"):
            detail = f"{detail} snapshot_sha256={record.get('snapshot_sha256')}"
        lines.append(f"- `{chunk_id}` ({detail})")
        if snippet:
            lines.append(f"  - {snippet}")
    return "\n".join(lines) + "\n"
