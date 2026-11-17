#!/usr/bin/env python3
"""TGT-009 live certification: permissioned remote AI against the real Anthropic API.

Runs the app's own answer pipeline (``services.answer_question``) in a temporary workspace that holds two
documents: one that answers the question and one private document that does not. Every outbound request to the
provider is captured at the SDK client, so the receipt can show exactly what left the machine.

Cases:
- the four permission gates each refuse before any request is sent: live mode off, no per-request allow_live, no
  API key, and a chat restricted to offline models;
- with every permission given, the remote model answers, cites a retrieved chunk, and the request carries only the
  retrieved chunk: the private document that retrieval did not select is never transmitted.

The API key is read from ANTHROPIC_API_KEY (or --key-file) into this process only. It is never written to the
receipt, printed, or logged; the receipt records the model, usage, timings and digests of what was sent and received.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import model_router
from backend.app.config import Settings
from backend.app.ingestion import ingest_file
from backend.app.services import answer_question

SCHEMA_VERSION = "mucaro-tgt009-live-cert/1.0"
STATUS_OK = "TGT009_LIVE_CERT_OK"
STATUS_FAILED = "TGT009_LIVE_CERT_FAILED"
QUESTION = "How does source lineage work?"
ANSWER_DOC = "Source lineage binds every chunk to a SHA-256 source identity and a stable chunk id."
PRIVATE_MARKER = "ZEBRAQUARTZ-PAYROLL"
PRIVATE_DOC = f"Payroll ledger: employee {PRIVATE_MARKER} salary and bonus figures are confidential."
GATED_ENV = ("ANTHROPIC_API_KEY", "MUCARO_AI_LIVE_MODE")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False).stdout.strip()


def _settings(root: Path) -> Settings:
    data = root / "data"
    cfg = Settings(
        project_root=root,
        data_dir=data,
        input_dir=data / "input",
        processed_dir=data / "processed",
        export_dir=data / "exports",
        index_dir=data / "indexes",
        sqlite_path=data / "processed" / "tgt009.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _capture_requests() -> list[dict[str, Any]]:
    """Wrap the SDK client so every request the provider receives is recorded here first."""
    sent: list[dict[str, Any]] = []
    original = model_router._anthropic_client

    def wrapped(sdk: Any) -> Any:
        client = original(sdk)
        stream = client.messages.stream

        def recording_stream(**request: Any) -> Any:
            sent.append(request)
            return stream(**request)

        client.messages.stream = recording_stream
        return client

    model_router._anthropic_client = wrapped
    return sent


def _set_env(key: str, **values: str) -> None:
    for name in GATED_ENV:
        os.environ.pop(name, None)
    for name, value in values.items():
        os.environ[name] = key if value == "<key>" else value


def _gate_case(case_id: str, cfg: Settings, sent: list, key: str, env: dict[str, str], expected_reason: str, **options: Any) -> dict[str, Any]:
    _set_env(key, **env)
    before = len(sent)
    result = answer_question(QUESTION, model_provider="anthropic", cfg=cfg, persist=False, **options)
    transmitted = len(sent) - before
    detail = str(result.get("detail") or "")
    passed = result.get("status") == "ERROR_MODEL_ROUTE_BLOCKED" and expected_reason in detail and transmitted == 0
    return {
        "case_id": case_id,
        "expected": f"ERROR_MODEL_ROUTE_BLOCKED ({expected_reason}), nothing transmitted",
        "observed_status": result.get("status"),
        "observed_detail": detail,
        "requests_transmitted": transmitted,
        "passed": passed,
    }


def _live_case(cfg: Settings, sent: list, key: str) -> dict[str, Any]:
    _set_env(key, **{"ANTHROPIC_API_KEY": "<key>", "MUCARO_AI_LIVE_MODE": "1"})
    before = len(sent)
    started = time.monotonic()
    result = answer_question(QUESTION, model_provider="anthropic", allow_live=True, top_k=1, cfg=cfg, persist=False)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    requests = sent[before:]
    body = json.dumps(requests, sort_keys=True, default=str)
    answer = str(result.get("answer") or "")
    cited = list(result.get("cited_chunk_ids") or [])
    retrieved = list(result.get("retrieved_chunk_ids") or [])
    checks = {
        "answered_by_remote_provider": result.get("model_provider") == "anthropic",
        "exactly_one_request": len(requests) == 1,
        "answer_cites_a_retrieved_chunk": bool(cited) and set(cited) <= set(retrieved),
        "citation_status_ok": result.get("citation_status") == "CITATION_OK",
        "retrieved_source_was_sent": ANSWER_DOC[:40] in body,
        "unselected_private_source_not_sent": PRIVATE_MARKER not in body,
        "key_not_in_request_body": bool(os.environ.get("ANTHROPIC_API_KEY")) and os.environ["ANTHROPIC_API_KEY"] not in body,
    }
    return {
        "case_id": "LIVE_ANSWER_SENDS_ONLY_RETRIEVED_CONTENT",
        "expected": "remote answer citing the retrieved chunk; only that chunk transmitted",
        "model_provider": result.get("model_provider"),
        "model_name": result.get("model_name"),
        "model_stop_reason": result.get("model_stop_reason"),
        "model_usage": result.get("model_usage"),
        "citation_status": result.get("citation_status"),
        "cited_chunk_ids": cited,
        "retrieved_chunk_ids": retrieved,
        "elapsed_ms": elapsed_ms,
        "requests_transmitted": len(requests),
        "request_body_sha256": _sha256(body),
        "answer_sha256": _sha256(answer),
        "answer_excerpt": answer[:240],
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, help="Read the API key from this file instead of ANTHROPIC_API_KEY.")
    args = parser.parse_args()
    key = args.key_file.read_text(encoding="utf-8").strip() if args.key_file else os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("ERROR_TGT009_KEY_REQUIRED: set ANTHROPIC_API_KEY or pass --key-file", file=sys.stderr)
        return 2

    sent = _capture_requests()
    with tempfile.TemporaryDirectory(prefix="mucaro-tgt009-") as temporary:
        root = Path(temporary)
        cfg = _settings(root)
        (root / "lineage.md").write_text(ANSWER_DOC, encoding="utf-8")
        (root / "private.md").write_text(PRIVATE_DOC, encoding="utf-8")
        ingest_file(root / "lineage.md", cfg)
        ingest_file(root / "private.md", cfg)
        live_on = {"ANTHROPIC_API_KEY": "<key>", "MUCARO_AI_LIVE_MODE": "1"}
        rows = [
            _gate_case("LIVE_MODE_OFF_REFUSED", cfg, sent, key, {"ANTHROPIC_API_KEY": "<key>"}, "live mode is off", allow_live=True),
            _gate_case("NO_PER_REQUEST_PERMISSION_REFUSED", cfg, sent, key, live_on, "has not been allowed for this chat"),
            _gate_case("NO_KEY_REFUSED", cfg, sent, key, {"MUCARO_AI_LIVE_MODE": "1"}, "is not set", allow_live=True),
            _gate_case("OFFLINE_CHAT_REFUSED", cfg, sent, key, live_on, "restricted to offline models", allow_live=True, require_offline=True),
            _live_case(cfg, sent, key),
        ]
    for name in GATED_ENV:
        os.environ.pop(name, None)

    passed = sum(1 for row in rows if row["passed"])
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_OK if passed == len(rows) else STATUS_FAILED,
        "requirement_id": "TGT-009",
        "head_sha": _git("rev-parse", "HEAD"),
        "worktree_clean": _git("status", "--porcelain") == "",
        "executed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": "anthropic",
        "arithmetic": {"cases": len(rows), "passed": passed, "failed": len(rows) - passed},
        "case_rows": rows,
        "limitations": [
            "One live request to the default Anthropic model; it shows the governed path works end to end, not answer quality at scale.",
            "Run from a cloud development container with the operator's key, not from the packaged macOS app.",
            "The API key is never recorded; requests are recorded as digests plus the checks above.",
        ],
    }
    receipt["receipt_sha256"] = _sha256(json.dumps(receipt, sort_keys=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if key in text:  # last line of defence: never write the key
        print("ERROR_TGT009_KEY_IN_RECEIPT", file=sys.stderr)
        return 3
    args.output.write_text(text, encoding="utf-8")
    print(receipt["status"])
    print(json.dumps({"head_sha": receipt["head_sha"], "arithmetic": receipt["arithmetic"], "receipt_sha256": receipt["receipt_sha256"]}))
    return 0 if receipt["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
