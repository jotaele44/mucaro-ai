#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.ingestion import ingest_folder  # noqa: E402


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "input"
    result = ingest_folder(folder)
    print(result["status"], f"files_ingested={result['files_ingested']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
