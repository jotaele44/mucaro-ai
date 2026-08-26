from __future__ import annotations

import csv
from typing import Any

from .config import Settings, settings
from .utils import write_json


def export_answer_trace(answer_trace: dict[str, Any], cfg: Settings = settings) -> dict[str, str]:
    cfg.ensure_dirs()
    run_id = answer_trace["run_id"]
    json_path = cfg.export_dir / f"{run_id}.answer_trace.json"
    md_path = cfg.export_dir / f"{run_id}.answer_trace.md"
    csv_path = cfg.export_dir / f"{run_id}.citations.csv"
    write_json(json_path, answer_trace)
    md_path.write_text(_to_markdown(answer_trace), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["run_id", "chunk_id", "source_id"])
        writer.writeheader()
        for chunk_id, source_id in zip(answer_trace.get("cited_chunk_ids", []), answer_trace.get("source_ids", [])):
            writer.writerow({"run_id": run_id, "chunk_id": chunk_id, "source_id": source_id})
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
    for chunk_id in answer_trace.get("cited_chunk_ids", []):
        lines.append(f"- `{chunk_id}`")
    return "\n".join(lines) + "\n"
