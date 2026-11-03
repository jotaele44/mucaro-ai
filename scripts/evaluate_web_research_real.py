#!/usr/bin/env python3
"""Evaluate comparative evidence preservation on complete, hash-bound frozen RFCs."""

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

FIXTURES = ROOT / "backend/tests/fixtures/web_research_real_v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_sources(directory: Path = FIXTURES) -> tuple[dict, dict[str, tuple[dict, bytes]]]:
    manifest = json.loads((directory / "manifest.json").read_bytes())
    acquisition = (directory / "acquisition.json").read_bytes()
    corpus_manifest = (directory / "corpus_manifest.json").read_bytes()
    if (
        _sha(acquisition) != manifest["acquisition_sha256"]
        or _sha(corpus_manifest) != manifest["corpus_manifest_sha256"]
    ):
        raise ValueError("Frozen parent manifest bytes changed")
    parents = json.loads(acquisition)["sources"]
    rows = manifest["included_sources"]
    if (
        len(rows) != manifest["included_source_count"]
        or len(parents) != manifest["upstream_corpus_source_count"]
        or len(parents) != len(rows) + manifest["excluded_source_count"]
    ):
        raise ValueError("Source denominator mismatch")
    result = {}
    for row in rows:
        path = directory / row["path"]
        if (
            Path(row["path"]).name != row["path"]
            or path.is_symlink()
            or row not in parents
            or row["source_id"] in result
        ):
            raise ValueError("Source binding invalid")
        raw = path.read_bytes()
        if _sha(raw) != row["sha256"] or len(raw) != row["byte_size"]:
            raise ValueError("Frozen source bytes changed")
        raw.decode("utf-8", errors="strict")
        result[row["source_id"]] = (row, raw)
    return manifest, result


def evaluate(directory: Path = FIXTURES) -> dict:
    parent, sources = load_sources(directory)
    case_bytes = (directory / "cases.json").read_bytes()
    fixture = json.loads(case_bytes)
    cases = fixture["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Empty or duplicate case IDs")
    results, anchors, excerpts, negatives = [], [], [], []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="mucaro-real-research-") as temporary:
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
        captures = {}
        for sid, (row, raw) in sources.items():
            snapshot = {
                "schema_version": "mucaro_web_snapshot_v1",
                "requested_url": row["requested_url"],
                "final_url": row["final_url"],
                "retrieval_utc": row["retrieved_utc"],
                "http_status": row["http_status"],
                "content_type": "text/plain; charset=utf-8",
                "content_sha256": row["sha256"],
                "content_size_bytes": len(raw),
                "content_base64": base64.b64encode(raw).decode(),
                "archival_derivation": {
                    "kind": "RESTORED_CAPTURE_REPRESENTATION",
                    "parent_archive_sha256": parent["archive_sha256"],
                    "parent_acquisition_sha256": parent["acquisition_sha256"],
                    "original_source_id": sid,
                    "acquisition_repeated": False,
                },
            }
            snapshot["snapshot_id"] = stable_id(
                "websnap", row["requested_url"], row["final_url"], row["sha256"], row["retrieved_utc"]
            )
            saved = persist_web_snapshot(snapshot, cfg)
            captures[sid] = (snapshot["snapshot_id"], saved["snapshot_sha256"])
        for case in cases:
            begin = time.perf_counter()
            args = {"question": case["question"], "synthesis": "extractive", "passage_mode": "paragraph"}
            for side in ("a", "b"):
                args[f"{side}_id"], args[f"{side}_sha256"] = captures[case[side]]
            report = execute_skill("mucaro/web-research", args, cfg=cfg)["result"]
            anchor_rows = []
            for expected in case["anchors"]:
                sid = expected["source_id"]
                hits = [
                    row["chunk_id"]
                    for row in report["citations"]
                    if row["source_id"] == captures[sid][0] and expected["text"] in " ".join(row["text"].split())
                ]
                anchor_rows.append(
                    {"source_id": sid, "anchor": expected["text"], "cited_passage_ids": hits, "pass": bool(hits)}
                )
                anchors.append(bool(hits))
            fidelity = []
            for citation in report["citations"]:
                sid = next(sid for sid in sources if captures[sid][0] == citation["source_id"])
                text = sources[sid][1].decode("utf-8")
                fidelity.append(text[citation["start"] : citation["end"]] == citation["text"])
            excerpts.extend(fidelity)
            context_ok = (report["synthesis"]["status"] == "EXTRACTIVE_SYNTHESIS") is case["expected_context"]
            if not case["expected_context"]:
                negative = context_ok and not report["citations"] and not report["synthesis"]["model_invoked"]
                negatives.append(negative)
                context_ok = negative
            results.append(
                {
                    "case_id": case["id"],
                    "pass": context_ok and all(row["pass"] for row in anchor_rows) and all(fidelity),
                    "relation": case["relation"],
                    "context_correct": context_ok,
                    "anchors": anchor_rows,
                    "citation_count": len(fidelity),
                    "selected_excerpts": [
                        {"source_id": row["source_id"], "text": row["text"]} for row in report["citations"]
                    ],
                    "passage_counts": [source["passage_count"] for source in report["sources"]],
                    "elapsed_ms": round((time.perf_counter() - begin) * 1000, 3),
                }
            )

    def rate(values):
        return sum(values) / len(values) if values else 0

    metrics = {
        "case_pass_rate": rate([row["pass"] for row in results]),
        "required_anchor_coverage": rate(anchors),
        "exact_excerpt_rate": rate(excerpts),
        "no_context_accuracy": rate(negatives),
    }
    passed = all(metrics[key] >= value for key, value in fixture["thresholds"].items())
    return {
        "schema_version": "mucaro_web_research_real_eval_v1",
        "status": "WEB_RESEARCH_REAL_EVAL_OK" if passed else "WEB_RESEARCH_REAL_EVAL_FAILED",
        "head_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "cases_sha256": _sha(case_bytes),
        "manifest_sha256": _sha((directory / "manifest.json").read_bytes()),
        "source_count": len(sources),
        "source_bytes": sum(len(raw) for row, raw in sources.values()),
        "case_count": len(cases),
        "thresholds": fixture["thresholds"],
        "metrics": metrics,
        "cases": results,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "scope": "Complete frozen RFC source bytes with manually authored evidence anchors; no live-model intelligence or independent semantic certification claim. Capture JSON wrappers are new derived representations; original HTTP content hashes and acquisition times are preserved.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate()
    raw = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw, encoding="utf-8")
    print(raw, end="")
    return 0 if report["status"] == "WEB_RESEARCH_REAL_EVAL_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
