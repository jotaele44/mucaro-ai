"""Isolated research checkpoint runner. Does not activate the production UI."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings
from backend.app.research_evidence import read_json_strict
from backend.app.research_orchestration import advance_research, create_research


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("plan", type=Path)
    start.add_argument("packet", type=Path)
    start.add_argument("--evidence-root", type=Path, required=True)
    advance = sub.add_parser("advance")
    advance.add_argument("checkpoint_id")
    advance.add_argument("--steps", type=int, default=1)
    args = parser.parse_args()
    root = args.workspace.resolve()
    if root == ROOT or root.is_relative_to(ROOT):
        parser.error("use an isolated data workspace outside the source checkout")
    cfg = Settings(
        project_root=ROOT,
        data_dir=root,
        input_dir=root / "input",
        processed_dir=root / "processed",
        export_dir=root / "exports",
        index_dir=root / "indexes",
        sqlite_path=root / "research.sqlite3",
        default_model_provider="mock",
        default_model_name="mock-cited-synthesizer-v1",
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        parser.error("research runner requires a clean committed source checkout")
    try:
        if args.command == "start":
            result = create_research(
                read_json_strict(args.plan.read_bytes()),
                read_json_strict(args.packet.read_bytes()),
                args.evidence_root,
                source_sha=sha,
                cfg=cfg,
            )
        else:
            result = advance_research(args.checkpoint_id, source_sha=sha, cfg=cfg, max_steps=args.steps)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return 0 if result["state"] in {"EXECUTING", "COMPLETED"} else 1
    except Exception as exc:  # noqa: BLE001 -- CLI boundary redacts provider error details.
        # Providers can include sensitive details; expose only the stable class/status.
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error_status": getattr(exc, "status", None),
                    "release_certification": "WITHHELD",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
