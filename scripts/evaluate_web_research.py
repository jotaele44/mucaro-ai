#!/usr/bin/env python3
"""Frozen offline research acceptance corpus; no web acquisition or model execution."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings  # noqa: E402
from backend.app.skill_runtime import activate_skill, execute_skill, mount_skill  # noqa: E402
from backend.app.utils import stable_id  # noqa: E402
from backend.app.web_capture_store import persist_web_snapshot  # noqa: E402
from backend.app.web_research import _extract  # noqa: E402

FIXTURE = ROOT / "backend/tests/fixtures/web_research_eval_v1.json"
COUNT_FIELDS = ["a", "b", "intersection", "a_only", "b_only", "union", "symmetric_difference"]


def evaluate() -> dict:
    raw = FIXTURE.read_bytes()
    fixture = json.loads(raw)
    if (
        fixture["schema_version"] != "mucaro_web_research_eval_v1"
        or len({row["id"] for row in fixture["cases"]}) != len(fixture["cases"])
        or not fixture["cases"]
    ):
        raise ValueError("Invalid or empty benchmark fixture")
    started = time.perf_counter()
    cases, citation_checks, negative_checks = [], [], []
    with tempfile.TemporaryDirectory(prefix="mucaro-research-eval-") as temporary:
        root = Path(temporary)
        cfg = Settings(
            project_root=ROOT,
            data_dir=root,
            input_dir=root / "input",
            processed_dir=root / "processed",
            export_dir=root / "exports",
            index_dir=root / "indexes",
            sqlite_path=root / "processed/test.sqlite3",
            default_model_provider="mock",
            default_model_name="mock",
        )
        mount_skill("mucaro/web-research", cfg=cfg)
        activate_skill(
            "mucaro/web-research", cfg=cfg, allowed_capabilities={"filesystem.read", "filesystem.write", "model.invoke"}
        )
        for index, case in enumerate(fixture["cases"]):
            args, texts = {"question": case["question"], "synthesis": "extractive"}, {}
            for side in ("a", "b"):
                content = case[side].encode("utf-8")
                url = f"https://authored-fixture.invalid/{case['id']}/{side}"
                snapshot = {
                    "schema_version": "mucaro_web_snapshot_v1",
                    "requested_url": url,
                    "final_url": url,
                    "retrieval_utc": "2026-11-02T00:00:00Z",
                    "http_status": 200,
                    "content_type": case.get(f"{side}_mime", "text/plain; charset=utf-8"),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "content_size_bytes": len(content),
                    "content_base64": base64.b64encode(content).decode(),
                }
                snapshot["snapshot_id"] = stable_id(
                    "websnap", url, url, snapshot["content_sha256"], snapshot["retrieval_utc"]
                )
                saved = persist_web_snapshot(snapshot, cfg)
                args[f"{side}_id"], args[f"{side}_sha256"] = snapshot["snapshot_id"], saved["snapshot_sha256"]
                texts[snapshot["snapshot_id"]] = _extract(snapshot, content)[0]
            begin = time.perf_counter()
            report = execute_skill("mucaro/web-research", args, cfg=cfg)["result"]
            fidelity = [
                texts[row["source_id"]][row["start"] : row["end"]] == row["text"] for row in report["citations"]
            ]
            citation_checks.extend(fidelity)
            has_context = report["synthesis"]["status"] == "EXTRACTIVE_SYNTHESIS"
            counts_ok = [report["comparison"]["counts"][key] for key in COUNT_FIELDS] == case["expected_counts"]
            source_coverage = (
                {row["source_id"] for row in report["citations"]} == set(texts)
                if case["expected_context"]
                else not report["citations"]
            )
            context_ok = has_context is case["expected_context"] and source_coverage
            if not case["expected_context"]:
                negative_checks.append(context_ok and not report["synthesis"]["model_invoked"])
            warning_ok = ("INSTRUCTION_BEARING_SOURCE_CONTENT" in report["warnings"]) is case["instruction_warning"]
            cases.append(
                {
                    "id": case["id"],
                    "pass": counts_ok and context_ok and warning_ok and all(fidelity),
                    "accounting_correct": counts_ok,
                    "context_correct": context_ok,
                    "warning_correct": warning_ok,
                    "citation_count": len(fidelity),
                    "elapsed_ms": round((time.perf_counter() - begin) * 1000, 3),
                }
            )
    metrics = {
        "case_pass_rate": sum(row["pass"] for row in cases) / len(cases),
        "exact_excerpt_rate": sum(citation_checks) / len(citation_checks) if citation_checks else 0,
        "no_context_accuracy": sum(negative_checks) / len(negative_checks) if negative_checks else 0,
    }
    passed = all(metrics[key] >= value for key, value in fixture["thresholds"].items())
    return {
        "schema_version": "mucaro_web_research_eval_report_v1",
        "status": "WEB_RESEARCH_EVAL_OK" if passed else "WEB_RESEARCH_EVAL_FAILED",
        "head_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "fixture_bytes": len(raw),
        "thresholds": fixture["thresholds"],
        "metrics": metrics,
        "case_count": len(cases),
        "citation_count": len(citation_checks),
        "cases": cases,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "scope": "Authored offline capture comparison, exact excerpt citations, and extractive synthesis; not live search, semantic truth, or model intelligence.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "WEB_RESEARCH_EVAL_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
