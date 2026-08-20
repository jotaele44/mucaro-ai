#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.config import settings  # noqa: E402
from backend.app.cli import emit_payload  # noqa: E402
from backend.app.services import answer_question  # noqa: E402


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "What does the sample say about source lineage?"
    trace = answer_question(query, cfg=settings)
    emit_payload(trace, "json")
    if trace.get("status", "").startswith("ERROR_"):
        return 2
    return 0 if trace.get("citation_status") == "CITATION_OK" else 3


if __name__ == "__main__":
    raise SystemExit(main())
