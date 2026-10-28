from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .artifact_identity import ArtifactIdentityError, create_artifact_record
from .config import Settings, settings
from .contracts import validate_contract
from .utils import read_json, write_json

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

    lineage = _register_trace_lineage(answer_trace, cfg)
    answer_trace["artifact_lineage"] = lineage
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
    export_errors = _register_export_artifacts(
        answer_trace=answer_trace,
        files={
            "json": json_path,
            "markdown": md_path,
            "csv": csv_path,
        },
        cfg=cfg,
    )
    if export_errors:
        answer_trace["artifact_lineage"]["status"] = "PARTIAL"
        answer_trace["artifact_lineage"]["export_registration_errors"] = export_errors
        write_json(json_path, answer_trace)
    return {"json": str(json_path), "markdown": str(md_path), "csv": str(csv_path)}


def _register_trace_lineage(answer_trace: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Bind a trace to ingested source artifacts when the source manifests provide handles."""
    manifests = read_json(cfg.processed_dir / "source_manifest.json", [])
    by_source = {
        str(row.get("source_id")): row
        for row in manifests
        if isinstance(row, dict) and row.get("source_id")
    }
    source_ids = [str(source_id) for source_id in answer_trace.get("source_ids", [])]
    parent_ids = [
        str(by_source[source_id]["artifact_id"])
        for source_id in source_ids
        if isinstance(by_source.get(source_id), dict) and by_source[source_id].get("artifact_id")
    ]
    unresolved = [
        source_id
        for source_id in source_ids
        if not isinstance(by_source.get(source_id), dict) or not by_source[source_id].get("artifact_id")
    ]
    if not parent_ids:
        return {
            "status": "NO_SOURCE_PARENTS",
            "source_ids": source_ids,
            "source_artifact_ids": [],
            "unresolved_source_ids": unresolved or source_ids,
        }
    try:
        trace_artifact = create_artifact_record(
            processed_dir=cfg.processed_dir,
            artifact_type="DERIVED",
            payload=_trace_payload(answer_trace),
            logical_identity=f"answer_trace:{answer_trace['run_id']}",
            producer="answer_service",
            producer_version="1.0.0",
            parent_artifact_ids=parent_ids,
            provenance_parameters={"source_ids": source_ids},
        )
    except ArtifactIdentityError as exc:
        return {
            "status": "LINEAGE_REGISTRATION_FAILED",
            "detail": str(exc),
            "source_ids": source_ids,
            "source_artifact_ids": parent_ids,
            "unresolved_source_ids": unresolved,
        }
    return {
        "status": "VALID",
        "trace_artifact_id": trace_artifact.artifact_id,
        "source_ids": source_ids,
        "source_artifact_ids": parent_ids,
        "unresolved_source_ids": unresolved,
    }


def _trace_payload(answer_trace: dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in answer_trace.items()
        if key not in {"export_paths", "artifact_lineage"}
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _register_export_artifacts(*, answer_trace: dict[str, Any], files: dict[str, Path], cfg: Settings) -> list[dict[str, str]]:
    trace_artifact_id = (answer_trace.get("artifact_lineage") or {}).get("trace_artifact_id")
    if not isinstance(trace_artifact_id, str) or not trace_artifact_id:
        return []
    errors: list[dict[str, str]] = []
    for kind, path in files.items():
        try:
            create_artifact_record(
                processed_dir=cfg.processed_dir,
                artifact_type="EXPORT",
                payload=path.read_bytes(),
                logical_identity=f"answer_export:{answer_trace['run_id']}:{kind}",
                producer="answer_export",
                producer_version="1.0.0",
                parent_artifact_ids=[trace_artifact_id],
                source_path_or_uri=str(path),
                provenance_parameters={"export_kind": kind, "run_id": answer_trace["run_id"]},
            )
        except ArtifactIdentityError:
            errors.append({"kind": kind, "path": str(path), "status": "ERROR_EXPORT_ARTIFACT_LINEAGE"})
    return errors


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
