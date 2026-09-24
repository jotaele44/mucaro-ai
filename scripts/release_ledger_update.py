#!/usr/bin/env python3
"""Apply a reviewed JSON patch of acceptance-ledger and DAG updates, then re-validate.

Usage:
    python scripts/release_ledger_update.py updates.json

The patch file has the shape::

    {"rows": {"REQ-ID": {"state": "PASS", "positive_gate": "...", ...}},
     "dag": {"N02": {"state": "PASS", "notes": "..."}}}

Only fields defined by the ledger schemas may be changed; requirement_text and
scope_state stay owned by the manifest. The write is rolled back if the result
does not validate, so a patch can never leave the ledgers invalid.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_scope import LEDGER_FILES, RELEASE_DIR, validate_release_scope, write_status_doc

ROW_FIELDS = {
    "implementation_state", "implementation_refs", "dependencies", "positive_gate", "negative_gate", "benchmark",
    "evidence_refs", "state", "blocker_id", "dag_node", "notes",
}
NODE_FIELDS = {"state", "notes", "blocker", "evidence_required", "dependencies", "requirement_ids"}


def main() -> int:
    patch = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    acceptance_path = ROOT / RELEASE_DIR / LEDGER_FILES["acceptance"][0]
    dag_path = ROOT / RELEASE_DIR / LEDGER_FILES["dag"][0]
    original = {acceptance_path: acceptance_path.read_text(encoding="utf-8"), dag_path: dag_path.read_text(encoding="utf-8")}
    acceptance = json.loads(original[acceptance_path])
    dag = json.loads(original[dag_path])
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = {row["requirement_id"]: row for row in acceptance["rows"]}
    for requirement_id, changes in patch.get("rows", {}).items():
        unknown = set(changes) - ROW_FIELDS
        if unknown or requirement_id not in rows:
            raise SystemExit(f"invalid row patch {requirement_id}: unknown={sorted(unknown)}")
        rows[requirement_id].update(changes)
    nodes = {node["node_id"]: node for node in dag["nodes"]}
    for node_id, changes in patch.get("dag", {}).items():
        unknown = set(changes) - NODE_FIELDS
        if unknown or node_id not in nodes:
            raise SystemExit(f"invalid node patch {node_id}: unknown={sorted(unknown)}")
        nodes[node_id].update(changes)
    acceptance["updated_utc"] = now
    dag["updated_utc"] = now
    acceptance_path.write_text(json.dumps(acceptance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    dag_path.write_text(json.dumps(dag, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = validate_release_scope(ROOT)
    if report["status"] != "RELEASE_SCOPE_VALID":
        for path, text in original.items():
            path.write_text(text, encoding="utf-8")
        print(json.dumps(report["errors"], indent=2))
        print("RELEASE_LEDGER_UPDATE_ROLLED_BACK")
        return 1
    print(json.dumps(report["denominator"]["by_state"]))
    print(f"STATUS_DOC {write_status_doc(ROOT).relative_to(ROOT)}")
    print("RELEASE_LEDGER_UPDATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
