"""QA fixture for relay_qa.mjs: the real app and built interface with a scripted answerer.

The built-in mock answerer only quotes sources, so it never writes a NEXT VECTOR block. This launcher
wraps it: every cited answer is delayed a little (so a run can be watched from another conversation)
and closed with a fixed block. Everything else is the shipped service. Data goes to a throwaway
directory, never to the checkout's data/.

The block is fixture text. A pass here shows that the interface carries the relay; it is not evidence
that any model writes the block (VIDEO_VECTOR_RELAY_V1 row VRL-002 stays open until one is run).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

import uvicorn

from backend.app import model_router
from backend.app.config import Settings
from backend.app.ingestion import ingest_file
from backend.app.main import create_app

ANCHORS = ("VERIFY SOURCE LINEAGE CHUNK IDENTITY", "CLOSE SOURCE LINEAGE CHUNK IDENTITY", "CERTIFY SOURCE LINEAGE CHUNK IDENTITY")
BLOCK = """NEXT VECTOR:
{anchor}
→ RECOMPUTE THE SHA-256 SOURCE IDENTITY
→ CONFIRM EVERY STABLE CHUNK ID
→ INDEPENDENT EVIDENCE QA

CROSS ALL T'S:
CHECK_ACCURACY→COMPLETENESS→CONSISTENCY
→ CHALLENGE_ASSUMPTIONS→TEST_FAILURE_MODES
→ RESOLVE_CONTRADICTIONS→CONFIRM

MAXIMIZE YIELD:
REUSE THE INGESTED SOURCE
→ NO_BULK_DOWNLOADS
→ NO_UNAUTHORIZED_PRODUCTION_WRITES

FINAL GATE:
LINEAGE IDENTITY = OPEN
LOCAL VALIDATION = PASS_BOUNDED
PRODUCTION = WITHHELD"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--delay", type=float, default=3.0, help="seconds each cited answer takes")
    args = parser.parse_args()

    answered = {"count": 0}
    real = model_router._mock_answer

    def scripted(query, retrieved_chunks, **kwargs):
        result = real(query, retrieved_chunks, **kwargs)
        time.sleep(args.delay)
        if retrieved_chunks:
            block = BLOCK.format(anchor=ANCHORS[answered["count"] % len(ANCHORS)])
            answered["count"] += 1
            result["answer"] = f"{result['answer']}\n\n```text\n{block}\n```"
        return result

    model_router._mock_answer = scripted
    data = Path(tempfile.mkdtemp(prefix="mucaro-relay-qa-"))
    cfg = Settings(
        project_root=ROOT,
        data_dir=data,
        input_dir=data / "input",
        processed_dir=data / "processed",
        export_dir=data / "exports",
        index_dir=data / "indexes",
        sqlite_path=data / "processed" / "relay_qa.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    note = data / "lineage.md"
    note.write_text("Source lineage binds every chunk to a SHA-256 source identity and a stable chunk id.", encoding="utf-8")
    ingest_file(note, cfg)
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
