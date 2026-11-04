"""Acquire two real OFFLINE MOCK pipeline outputs; never a ChatGPT quality benchmark.

Run only in a clean committed checkout. Keep custodian/ separate from reviewers.
No scores are generated: independent review is still required.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.artifact_identity import sha256_bytes
from backend.app.config import Settings
from backend.app.model_router import generate_answer
from backend.app.research_benchmark import prepare_blind_pairs
from backend.app.research_orchestration import advance_research, create_research
from backend.app.utils import utc_now, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination == ROOT or destination.is_relative_to(ROOT):
        parser.error("use a destination outside source checkout")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        parser.error("clean committed checkout required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    destination.mkdir(parents=True, exist_ok=False)
    public, private = destination / "reviewer", destination / "custodian"
    public.mkdir()
    private.mkdir(mode=0o700)
    write_json(
        public / "rubric.json",
        {
            "status": "FROZEN_BEFORE_RUNS",
            "created_utc": utc_now(),
            "purpose": "OFFLINE_MOCK_PIPELINE_SMOKE_ONLY",
            "scale": [0, 100],
            "criteria": {"source_fidelity": 50, "question_relevance": 50},
            "required_review": "External reviewer, complete case coverage, explicit identity leakage assessment",
            "comparative_superiority": "NOT_TESTED",
        },
    )
    evidence = destination / "evidence"
    evidence.mkdir()
    content = "SYNTHETIC FIXTURE: Río Grande is named. No coordinate or facility identity is established."
    raw = content.encode()
    snapshot = {
        "schema_version": "mucaro_web_snapshot_v1",
        "snapshot_id": "synthetic-smoke",
        "requested_url": "https://example.org/synthetic-fixture",
        "final_url": "https://example.org/synthetic-fixture",
        "retrieval_utc": "2026-11-02T00:00:00Z",
        "http_status": 200,
        "content_base64": base64.b64encode(raw).decode(),
        "content_sha256": sha256_bytes(raw),
        "content_size_bytes": len(raw),
    }
    write_json(evidence / "snapshot.json", snapshot)
    packet = {
        "schema_version": "mucaro-research-packet/1",
        "sources": [
            {
                "source_id": "fixture",
                "path": "snapshot.json",
                "snapshot_sha256": sha256_bytes((evidence / "snapshot.json").read_bytes()),
            }
        ],
        "claims": [
            {
                "claim_id": "c1",
                "text": "The synthetic fixture names a location.",
                "source_id": "fixture",
                "byte_start": 0,
                "byte_end": len(raw),
                "span_sha256": sha256_bytes(raw),
                "epistemic_class": "UNKNOWN",
            }
        ],
    }
    plan = {
        "schema_version": "mucaro-research-plan/1",
        "objective": "Offline pipeline parity smoke",
        "provider": "mock",
        "model": "mock-cited-synthesizer-v1",
        "allow_live": False,
        "max_model_calls": 2,
        "max_input_chars": 10000,
        "deadline_seconds": 3600,
        "questions": [
            {"id": "location", "query": "Which location does the fixture name?", "depends_on": [], "claim_ids": ["c1"]},
            {
                "id": "limits",
                "query": "Does this fixture establish coordinates or facility identity?",
                "depends_on": ["location"],
                "claim_ids": ["c1"],
            },
        ],
    }
    write_json(evidence / "packet.json", packet)
    write_json(evidence / "plan.json", plan)
    runtime = destination / "runtime"
    cfg = Settings(
        project_root=ROOT,
        data_dir=runtime,
        input_dir=runtime / "input",
        processed_dir=runtime / "processed",
        export_dir=runtime / "exports",
        index_dir=runtime / "indexes",
        sqlite_path=runtime / "research.sqlite3",
        default_model_provider="mock",
        default_model_name="mock-cited-synthesizer-v1",
    )
    checkpoint = create_research(plan, packet, evidence, source_sha=sha, cfg=cfg)
    checkpoint = advance_research(checkpoint["checkpoint_id"], source_sha=sha, cfg=cfg, max_steps=10)
    if checkpoint["state"] != "COMPLETED":
        raise RuntimeError("pipeline smoke did not complete")
    write_json(private / "checkpoint.json", checkpoint)
    report = checkpoint["completed_steps"][-1]["result"]["payload"]
    cases = []
    for row in report["answers"]:
        control = generate_answer(
            row["query"],
            [{"chunk_id": "c1", "source_id": "fixture", "text": content}],
            model_provider="mock",
            model_name=plan["model"],
            cfg=cfg,
        )
        cases.append(
            {
                "case_id": row["id"],
                "question": row["query"],
                "responses": [
                    {
                        "system_id": "direct-router-control",
                        "run_id": sha + ":control:" + row["id"],
                        "answer": control["answer"],
                    },
                    {
                        "system_id": "checkpoint-orchestrated-router",
                        "run_id": checkpoint["checkpoint_id"] + ":" + row["id"],
                        "answer": row["answer"]["answer"],
                    },
                ],
            }
        )
    reviewer_packet, key = prepare_blind_pairs(cases)
    write_json(public / "reviewer_packet.json", reviewer_packet)
    write_json(public / "scores.template.json", [])
    write_json(private / "paired_outputs.json", cases)
    write_json(private / "private_mapping.json", key)
    for path in private.iterdir():
        os.chmod(path, 0o600)
    write_json(
        destination / "RUN_STATUS.json",
        {
            "source_commit": sha,
            "status": "OFFLINE_MOCK_SMOKE_PASS",
            "cases": len(cases),
            "outputs": sum(len(c["responses"]) for c in cases),
            "fixture_provenance": "SYNTHETIC_GENERATED_LOCALLY; timestamps/URLs are fixture values",
            "scores": "NOT_ACQUIRED",
            "blind_quality_comparison": "NOT_RUN",
            "release_certification": "WITHHELD",
        },
    )
    print(json.dumps({"status": "OFFLINE_MOCK_SMOKE_PASS", "source_commit": sha, "cases": len(cases), "outputs": 4}))


if __name__ == "__main__":
    main()
