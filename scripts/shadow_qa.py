#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts import shadow_evidence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "mucaro-shadow-hardening-receipt/1.0"


def run(cmd: list[str], *, cwd: Path = ROOT) -> dict[str, object]:
    print("RUN", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return {
        "command": cmd,
        "cwd": str(cwd.relative_to(ROOT)) if cwd != ROOT else ".",
        "returncode": completed.returncode,
    }


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_paths(base: str, head: str) -> list[str]:
    out = git("diff", "--name-only", f"{base}...{head}")
    return [line for line in out.splitlines() if line.strip()]


def adjacent_paths(paths: list[str]) -> list[str]:
    adjacent: set[str] = set()
    for path in paths:
        if path.startswith("backend/app/"):
            adjacent.update(["backend/tests", "schemas", "backend/app/main.py", "backend/app/cli.py"])
        elif path.startswith("backend/tests/"):
            adjacent.add("backend/app")
        elif path.startswith("frontend/"):
            adjacent.update(["backend/app/main.py", "backend/tests/test_api.py", "frontend/src"])
        elif path.startswith("schemas/"):
            adjacent.update(["backend/app", "backend/tests"])
        elif path.startswith("scripts/"):
            adjacent.update(["backend/tests", ".github/workflows", "docs"])
        elif path.startswith(".github/workflows/"):
            adjacent.update(["scripts", "docs"])
        elif path.startswith("docs/"):
            adjacent.add("docs")
    return sorted(adjacent)


def classify_rendered(
    paths: list[str],
    *,
    checkpoint_sha: str,
    receipt_paths: list[Path],
) -> tuple[str, str]:
    if not any(path.startswith("frontend/") for path in paths):
        return ("AUDIT_ONLY", "No frontend surface changed in this checkpoint denominator.")
    if not receipt_paths:
        return (
            "BLOCKED",
            "Rendered desktop/mobile interaction evidence requires exact-checkpoint browser receipts; static CI is not accepted as rendered proof.",
        )

    browsers: set[str] = set()
    for receipt_path in receipt_paths:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ("BLOCKED", f"Rendered receipt unreadable: {receipt_path}: {exc}")
        if payload.get("schema_version") != "mucaro-rendered-qa/1.0":
            return ("BLOCKED", f"Rendered receipt schema invalid: {receipt_path}")
        if payload.get("status") != "RENDERED_QA_PASS":
            return ("BLOCKED", f"Rendered receipt is not PASS: {receipt_path}")
        if payload.get("head_sha") != checkpoint_sha:
            return ("BLOCKED", f"Rendered receipt checkpoint mismatch: {receipt_path}")
        if payload.get("worktree_clean") is not True or int(payload.get("failed", 1)) != 0:
            return ("BLOCKED", f"Rendered receipt is not clean/zero-failure: {receipt_path}")
        browser = str(payload.get("browser") or "").lower()
        if browser.startswith("chromium"):
            browsers.add("chromium")
        if browser.startswith("webkit"):
            browsers.add("webkit")

    missing = sorted({"chromium", "webkit"} - browsers)
    if missing:
        return ("BLOCKED", f"Rendered receipt browser denominator incomplete: missing={missing}")
    return ("PASS", "Exact-checkpoint Chromium and WebKit rendered receipts passed.")


REQUIRED_ADVERSARIAL_VECTORS = [
    "null_empty",
    "duplicate",
    "stale",
    "m_to_n_cardinality",
    "race",
    "replay_idempotency",
    "cancellation",
    "restart_reconstruction",
    "resume",
    "malformed_tool_result",
    "partial_delayed_tool_result",
    "orphan_state_transition",
    "persistence_loss",
    "schema_drift",
    "unexpected_codes",
]

SHADOW_BOOTSTRAP_PATHS = {
    ".github/workflows/shadow-qa.yml",
    "backend/tests/test_shadow_baseline.py",
    "backend/tests/test_shadow_evidence.py",
    "backend/tests/test_shadow_qa.py",
    "docs/SHADOW_QA.md",
    "scripts/resolve_shadow_baseline.py",
    "scripts/shadow_evidence.py",
    "scripts/shadow_qa.py",
}


def adversarial_vector_results(paths: list[str]) -> dict[str, dict[str, str]]:
    path_set = set(paths)
    bootstrap_only = bool(path_set) and all(
        path in SHADOW_BOOTSTRAP_PATHS or path.startswith("_architecture/shadow/evidence/")
        for path in path_set
    )
    if bootstrap_only:
        reason = (
            "NOT_APPLICABLE: checkpoint changes only Shadow QA infrastructure; "
            "no application runtime/state/persistence/tool-result surface is in the denominator."
        )
        return {vector: {"state": "NOT_APPLICABLE", "reason": reason} for vector in REQUIRED_ADVERSARIAL_VECTORS}

    reason = (
        "UNRESOLVED: this generic runner cannot prove the vector from broad release gates alone; "
        "checkpoint-specific adversarial evidence is required."
    )
    return {vector: {"state": "UNRESOLVED", "reason": reason} for vector in REQUIRED_ADVERSARIAL_VECTORS}


def exit_code_for_state(state: str) -> int:
    if state == "PASS":
        return 0
    if state == "FAIL":
        return 1
    return 6


def execute_vector_evidence(
    vector_results: dict[str, dict[str, object]],
    *,
    checkpoint_sha: str,
) -> list[str]:
    failures: list[str] = []
    for vector, result in vector_results.items():
        if result.get("state") != "PASS":
            continue
        evidence = list(result.get("evidence") or [])
        if any(item.get("kind") == "EXECUTION_RECEIPT" for item in evidence):
            continue
        tests = [item for item in evidence if item.get("kind") == "CHECKPOINT_TEST"]
        nodeids = [f"{item['path']}::{item['selector'].split('::')[-1]}" for item in tests]
        command = [sys.executable, "-m", "pytest", *nodeids, "-q"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        receipt = {
            "schema_version": shadow_evidence.EXECUTION_RECEIPT_SCHEMA_VERSION,
            "checkpoint_sha": checkpoint_sha,
            "vector": vector,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "command": command,
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }
        result["runtime_execution_receipt"] = receipt
        if completed.returncode != 0:
            failures.append(vector)
    return failures

def overall_state_for_results(
    *,
    failed_gates: list[dict[str, object]],
    blocked_gates: list[dict[str, object]],
    unresolved_vectors: list[str],
    blocked_vectors: list[str],
    rendered_state: str,
) -> str:
    if failed_gates:
        return "FAIL"
    if blocked_gates or unresolved_vectors or blocked_vectors or rendered_state == "BLOCKED":
        return "PROVISIONAL"
    return "PASS"




def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded MUCARO Shadow QA against an exact frozen checkpoint.")
    parser.add_argument("--base", default="HEAD^", help="Base commit/ref for the changed-surface denominator.")
    parser.add_argument("--head", default="HEAD", help="Frozen checkpoint commit/ref.")
    parser.add_argument("--evidence-manifest", type=Path, help="Checkpoint-bound vector evidence manifest.")
    parser.add_argument("--execute-vector-evidence", action="store_true", help="Execute PASS vector checkpoint tests in this exact-checkpoint run and generate runtime receipts.")
    parser.add_argument("--rendered-receipt", action="append", type=Path, default=[], help="Exact-checkpoint rendered QA receipt; provide both Chromium and WebKit receipts when frontend surfaces changed.")
    parser.add_argument("--historical-replay", action="store_true", help="Adjudicate a historical checkpoint from frozen evidence without claiming its gates were rerun.")
    parser.add_argument(
        "--output",
        default=os.environ.get("MUCARO_SHADOW_RECEIPT", "artifacts/shadow-qa-receipt.json"),
        help="Receipt path.",
    )
    args = parser.parse_args()

    base_sha = git("rev-parse", args.base)
    head_sha = git("rev-parse", args.head)
    actual_head = git("rev-parse", "HEAD")
    if head_sha != actual_head and not args.historical_replay:
        print(f"ERROR_SHADOW_HEAD_MISMATCH requested={head_sha} checkout={actual_head}", file=sys.stderr)
        return 2
    if args.historical_replay and not args.evidence_manifest:
        print("ERROR_SHADOW_EVIDENCE historical replay requires --evidence-manifest", file=sys.stderr)
        return 7

    paths = changed_paths(base_sha, head_sha)
    adjacency = adjacent_paths(paths)

    if args.historical_replay:
        gates = [
            {
                "command": [],
                "cwd": ".",
                "returncode": None,
                "state": "BLOCKED",
                "reason": "Historical replay preserves evidence but does not rerun checkpoint code from the tooling checkout.",
            }
        ]
    else:
        gates = [
            run([sys.executable, "-m", "pytest", "backend/tests", "-q"]),
            run([sys.executable, "scripts/validate_json.py"]),
            run([sys.executable, "scripts/certify_release.py"]),
        ]

        frontend = ROOT / "frontend"
        if (frontend / "package.json").exists() and (frontend / "node_modules").exists():
            gates.append(run(["npm", "run", "build"], cwd=frontend))
        else:
            gates.append(
                {
                    "command": ["npm", "run", "build"],
                    "cwd": "frontend",
                    "returncode": None,
                    "state": "BLOCKED",
                    "reason": "frontend/node_modules is absent; build not silently treated as PASS",
                }
            )

    failed = [gate for gate in gates if isinstance(gate.get("returncode"), int) and gate["returncode"] != 0]
    blocked = [gate for gate in gates if gate.get("state") == "BLOCKED"]

    rendered_state, rendered_reason = classify_rendered(
        paths,
        checkpoint_sha=head_sha,
        receipt_paths=args.rendered_receipt,
    )
    try:
        vector_results = (
            shadow_evidence.load_manifest(
                args.evidence_manifest,
                base_sha=base_sha,
                checkpoint_sha=head_sha,
                required_vectors=REQUIRED_ADVERSARIAL_VECTORS,
                require_execution_receipt=not args.execute_vector_evidence,
            )
            if args.evidence_manifest
            else adversarial_vector_results(paths)
        )
    except shadow_evidence.ShadowEvidenceError as exc:
        print(f"ERROR_SHADOW_EVIDENCE {exc}", file=sys.stderr)
        return 7

    unresolved_vectors = [
        name for name, result in vector_results.items() if result["state"] == "UNRESOLVED"
    ]
    blocked_vectors = [
        name for name, result in vector_results.items() if result["state"] == "BLOCKED"
    ]
    failed_vector_executions = (
        execute_vector_evidence(vector_results, checkpoint_sha=head_sha)
        if args.execute_vector_evidence
        else []
    )

    # A gate can verify the declared changed-file denominator only when every executable gate passes
    # and every required adversarial vector is explicitly classified.
    # BLOCKED gates or unresolved vectors remain unresolved rather than being silently counted as verified.
    overall = (
        "FAIL"
        if failed_vector_executions
        else overall_state_for_results(
            failed_gates=failed,
            blocked_gates=blocked,
            unresolved_vectors=unresolved_vectors,
            blocked_vectors=blocked_vectors,
            rendered_state=rendered_state,
        )
    )
    if overall == "PASS":
        verified = list(paths)
        unresolved = []
    else:
        verified = []
        unresolved = list(paths)

    receipt = {
        "schema_version": SCHEMA_VERSION,
        "repository": os.environ.get("GITHUB_REPOSITORY", "local/mucaro-ai"),
        "base_sha": base_sha,
        "checkpoint_sha": head_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "changed_surfaces": paths,
        "adjacent_surfaces": adjacency,
        "verified_surfaces": verified,
        "unresolved_surfaces": unresolved,
        "surface_arithmetic": {
            "changed": len(paths),
            "verified": len(verified),
            "unresolved": len(unresolved),
            "closes": len(paths) == len(verified) + len(unresolved),
        },
        "gates": gates,
        "findings": {
            "DEFECT": [],
            "REGRESSION": [],
            "GAP": [],
            "DEDUST": [],
        },
        "defect_arithmetic": {
            "confirmed": 0,
            "fixed": 0,
            "open": 0,
            "blocked": 0,
            "intentionally_deferred": 0,
            "closes": True,
        },
        "rendered_pass": {
            "state": rendered_state,
            "reason": rendered_reason,
        },
        "adversarial_vectors": vector_results,
        "vector_arithmetic": {
            "required": len(REQUIRED_ADVERSARIAL_VECTORS),
            "classified": len(vector_results),
            "unresolved": len(unresolved_vectors),
            "blocked": len(blocked_vectors),
            "execution_failures": len(failed_vector_executions),
            "closes": len(vector_results) == len(REQUIRED_ADVERSARIAL_VECTORS),
        },
        "execution_mode": "HISTORICAL_EVIDENCE_REPLAY" if args.historical_replay else "EXACT_CHECKPOINT_EXECUTION",
        "evidence_manifest": str(args.evidence_manifest) if args.evidence_manifest else None,
        "tooling_head_sha": actual_head,
        "overall_state": overall,
        "scope_note": "This receipt is bounded to the exact checkpoint and declared gates; it does not claim universal defect freedom.",
    }

    if not receipt["surface_arithmetic"]["closes"]:
        print("ERROR_SHADOW_SURFACE_ARITHMETIC", file=sys.stderr)
        return 3
    if not receipt["defect_arithmetic"]["closes"]:
        print("ERROR_SHADOW_DEFECT_ARITHMETIC", file=sys.stderr)
        return 4
    if not receipt["vector_arithmetic"]["closes"]:
        print("ERROR_SHADOW_VECTOR_ARITHMETIC", file=sys.stderr)
        return 5

    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SHADOW_RECEIPT {out}")
    print(f"SHADOW_STATE {overall}")
    return exit_code_for_state(overall)


if __name__ == "__main__":
    raise SystemExit(main())
