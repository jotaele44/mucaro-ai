#!/usr/bin/env python3
"""Replay actual pinned Mucaro audit tasks through bound learned-workflow agents."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.config import settings
from backend.app.consumer_task_eval import evaluate

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="mucaro-real-task-learning-") as temp:
        root = Path(temp)
        cfg = replace(settings, data_dir=root, input_dir=root / "input", processed_dir=root / "processed",
                      export_dir=root / "exports", index_dir=root / "indexes", sqlite_path=root / "processed/data.sqlite3")
        report = evaluate(cfg, args.source, args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], **report["metrics"]}))
    raise SystemExit(0 if report["status"] == "PASS" else 1)
