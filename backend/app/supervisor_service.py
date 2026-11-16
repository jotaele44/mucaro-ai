"""Standalone, opt-in supervisor proof worker. No application tools are enabled.

Only the built-in read-only SQLite health probe can be dispatched.
An external service manager must restart this process; no production worker
or automatic permission grant is registered by importing this module.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from .supervisor_queue import claim, finish, reconcile_expired

PROBE_KIND = "supervisor.read_only_probe"


def tick(db_path: str | Path, *, worker_id: str) -> dict:
    recovery = reconcile_expired(db_path)
    task = claim(db_path, worker_id=worker_id, kind=PROBE_KIND, read_only_only=True)
    if task is None:
        return {"status": "IDLE", "recovery": recovery}
    payload = task["payload"]
    # This is an exact allowlist of a single harmless operation, not a
    # dynamically imported handler or an LLM-derived execution command.
    valid = (
        isinstance(payload, dict)
        and payload.get("kind") == PROBE_KIND
        and isinstance(payload.get("run_id"), str) and bool(payload["run_id"])
        and isinstance(payload.get("source_sha"), str)
        and len(payload["source_sha"]) == 40
        and all(char in "0123456789abcdef" for char in payload["source_sha"])
        and task["mutates_state"] is False
        and set(payload) == {"kind", "run_id", "source_sha"}
    )
    if valid:
        with sqlite3.connect(":memory:") as db:
            observed = db.execute("SELECT 1").fetchone()[0]
        receipt = {"verified": observed == 1, "probe": PROBE_KIND, "observed": observed}
    else:
        receipt = {"verified": False, "error": "INVALID_PROBE_PAYLOAD"}
    committed = finish(
        db_path, task_id=task["task_id"], worker_id=worker_id,
        generation=task["generation"], receipt=receipt, succeeded=receipt["verified"],
    )
    return {
        "status": "COMMITTED" if committed else "FENCED",
        "task_id": task["task_id"], "generation": task["generation"],
        "verified": receipt["verified"], "recovery": recovery,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in, read-only Mucaro supervisor probe worker")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--worker-id", default="supervisor-probe")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if not args.worker_id or not (0.05 <= args.poll_seconds <= 60):
        parser.error("invalid worker ID or poll interval")
    if not args.db.is_file():
        parser.error("database must already exist (no implicit production DB creation)")
    while True:
        result = tick(args.db, worker_id=args.worker_id)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return 0 if result["status"] != "FENCED" else 2
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
