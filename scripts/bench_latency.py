#!/usr/bin/env python3
"""Measure answer latency through the real work-run and event-stream path.

Reports time to first word (submit -> first text_delta event a client would read) and total time
(submit -> end of stream), p50 and p95, plus the median of each answer_trace timings_ms stage.
Runs against a throwaway data directory, so saved conversations and runs are never touched.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

# env_file reads no settings, so importing it here cannot fix the data directory before main() sets it.
from backend.app.env_file import merge_env_file  # noqa: E402

DEFAULT_QUESTIONS = (
    "How does source lineage work?",
    "What is Mucaro AI?",
    "How are answers cited?",
)
PROVIDERS = ("mock", "anthropic", "openai_compatible_local")


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 1)


def _summary(values: list[float]) -> dict[str, float | None]:
    return {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)}


def _measure_once(question: str, provider: str, cfg: Any) -> dict[str, Any]:
    from backend.app.conversations import create_conversation
    from backend.app.db import list_payloads
    from backend.app.work_runs import get_run, stream_run_events, submit_message

    conversation_id = create_conversation("latency bench", cfg)["conversation_id"]
    options = {"model_provider": provider, "allow_live": provider == "anthropic"}
    started = time.perf_counter()
    run_id = submit_message(conversation_id, question, options=options, cfg=cfg)["run"]["run_id"]
    first_word_ms: float | None = None
    for block in stream_run_events(run_id, 0, cfg):
        if first_word_ms is None and block.startswith("id:") and "\nevent: text_delta\n" in block:
            first_word_ms = (time.perf_counter() - started) * 1000
        if block.startswith(("event: end", "event: timeout")):
            break
    total_ms = (time.perf_counter() - started) * 1000
    run = get_run(run_id, cfg)
    stages: dict[str, Any] = {}
    if run.get("answer_run_id"):
        for trace in list_payloads("runs", cfg.sqlite_path):
            if trace.get("run_id") == run["answer_run_id"]:
                stages = trace.get("timings_ms") or {}
                break
    error = (run.get("error") or {}).get("status")
    return {"state": run["state"], "error": error, "first_word_ms": first_word_ms, "total_ms": total_ms, "stages": stages}


def run_bench(provider: str, runs: int, questions: tuple[str, ...], corpus: Path) -> dict[str, Any]:
    from backend.app.config import Settings
    from backend.app.ingestion import ingest_folder

    cfg = Settings.load()
    cfg.ensure_dirs()
    ingest = ingest_folder(corpus, cfg)
    if not ingest.get("files_ingested"):
        return {"status": "ERROR_BENCH_CORPUS_EMPTY", "corpus": str(corpus), "ingest_status": ingest.get("status")}
    samples = [_measure_once(questions[i % len(questions)], provider, cfg) for i in range(runs)]
    # Let run threads finish their post-run dispatch before the throwaway data dir is removed.
    for thread in threading.enumerate():
        if thread.name.startswith("run-"):
            thread.join(timeout=5)
    ok =[row for row in samples if row["state"] == "COMPLETED"]
    stage_names = sorted({name for row in ok for name, value in row["stages"].items() if isinstance(value, (int, float))})
    return {
        "status": "BENCH_OK" if len(ok) == runs else "BENCH_PARTIAL",
        "provider": provider,
        "runs": runs,
        "completed": len(ok),
        "errors": sorted({row["error"] or row["state"] for row in samples if row["state"] != "COMPLETED"}),
        "first_word_ms": _summary([row["first_word_ms"] for row in ok if row["first_word_ms"] is not None]),
        "total_ms": _summary([row["total_ms"] for row in ok]),
        "stage_median_ms": {
            name: round(statistics.median(row["stages"][name] for row in ok if isinstance(row["stages"].get(name), (int, float))), 1)
            for name in stage_names
        },
    }


def format_text(report: dict[str, Any]) -> str:
    if report["status"].startswith("ERROR_"):
        return f"{report['status']}: corpus={report.get('corpus')} ingest_status={report.get('ingest_status')}"
    lines = [
        f"{report['status']} provider={report['provider']} completed={report['completed']}/{report['runs']}",
        f"time to first word: p50={report['first_word_ms']['p50']} ms  p95={report['first_word_ms']['p95']} ms",
        f"total:              p50={report['total_ms']['p50']} ms  p95={report['total_ms']['p95']} ms",
    ]
    for name, value in report["stage_median_ms"].items():
        lines.append(f"stage {name}: median={value} ms")
    if report["errors"]:
        lines.append("errors: " + ", ".join(report["errors"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=PROVIDERS, default="mock")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--question", action="append", help="Question to ask; repeat for several. Defaults to a fixed set.")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data" / "input", help="Folder to ingest into the throwaway data dir.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    questions = tuple(args.question) if args.question else DEFAULT_QUESTIONS
    # Same settings source as the launcher: .env fills in what the shell has not set.
    os.environ.update(merge_env_file(os.environ, ROOT / ".env"))
    with tempfile.TemporaryDirectory(prefix="mucaro-bench-") as data_dir:
        # Set before any backend import reads settings, so nothing touches the real data directory.
        os.environ["MUCARO_AI_DATA_DIR"] = data_dir
        report = run_bench(args.provider, args.runs, questions, args.corpus.resolve())
    print(json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else format_text(report))
    return 0 if report["status"] == "BENCH_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
