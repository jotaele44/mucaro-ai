#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

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


def classify_rendered(paths: list[str]) -> tuple[str, str]:
    if any(path.startswith("frontend/") for path in paths):
        return (
            "BLOCKED",
            "Rendered desktop/mobile interaction evidence requires a runnable browser/device pass; static CI is not accepted as rendered proof.",
        )
    return ("AUDIT_ONLY", "No frontend surface changed in this checkpoint denominator.")


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
    "backend/tests/test_shadow_qa.py",
    "docs/SHADOW_QA.md",
    "scripts/shadow_qa.py",
}


def adversarial_vector_results(paths: list[str]) -> dict[str, dict[str, str]]:
    path_set = set(paths)
    bootstrap_only = bool(path_set) and path_set <= SHADOW_BOOTSTRAP_PATHS
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded MUCARO Shadow QA against an exact frozen checkpoint.")
    parser.add_argument("--base", default="HEAD^", help="Base commit/ref for the changed-surface denominator.")
    parser.add_argument("--head", default="HEAD", help="Frozen checkpoint commit/ref.")
    parser.add_argument(
        "--output",
        default=os.environ.get("MUCARO_SHADOW_RECEIPT", "artifacts/shadow-qa-receipt.json"),
        help="Receipt path.",
    )
    args = parser.parse_args()

    base_sha = git("rev-parse", args.base)
    head_sha = git("rev-parse", args.head)
    actual_head = git("rev-parse", "HEAD")
    if head_sha != actual_head:
        print(f"ERROR_SHADOW_HEAD_MISMATCH requested={head_sha} checkout={actual_head}", file=sys.stderr)
        return 2

    paths = changed_paths(base_sha, head_sha)
    adjacency = adjacent_paths(paths)

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

    rendered_state, rendered_reason = classify_rendered(paths)
    vector_results = adversarial_vector_results(paths)
    unresolved_vectors = [
        name for name, result in vector_results.items() if result["state"] == "UNRESOLVED"
    ]

    # A gate can verify the declared changed-file denominator only when every executable gate passes
    # and every required adversarial vector is explicitly classified.
    # BLOCKED gates or unresolved vectors remain unresolved rather than being silently counted as verified.
    if failed:
        verified: list[str] = []
        unresolved = list(paths)
        overall = "FAIL"
    elif blocked or unresolved_vectors:
        verified = []
        unresolved = list(paths)
        overall = "PROVISIONAL"
    else:
        verified = list(paths)
        unresolved = []
        overall = "PASS"

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
            "closes": len(vector_results) == len(REQUIRED_ADVERSARIAL_VECTORS),
        },
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
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
