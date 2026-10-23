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


VECTOR_TEST_SELECTORS = {
    "null_empty": "backend/tests/test_response_flow.py::test_resolve_rejects_ambiguous_and_empty_responses",
    "duplicate": "backend/tests/test_contracts.py::test_allowed_approved_action_executes_once_and_duplicate_invocation_is_refused",
    "stale": "backend/tests/test_contracts.py::test_decision_against_stale_arguments_is_refused",
    "m_to_n_cardinality": "backend/tests/test_gis_processing.py::test_spatial_join_preserves_holes_multipolygons_and_overlap_multiplication",
    "race": "backend/tests/test_contracts.py::test_concurrent_runs_execute_the_handler_exactly_once",
    "replay_idempotency": "backend/tests/test_provider_attempt_identity.py::test_accepted_operation_is_not_executed_twice",
    "cancellation": "backend/tests/test_work_runs.py::test_stop_while_awaiting_approval_interrupts_and_cancels_pending_calls",
    "restart_reconstruction": "backend/tests/test_work_runs.py::test_restart_interrupts_running_runs_and_resumes_waiting_ones",
    "resume": "backend/tests/test_provider_attempt_identity.py::test_interrupted_operation_is_retried_after_resume_on_another_provider",
    "malformed_tool_result": "backend/tests/test_tools.py::test_malformed_tool_result_fails_before_executed_state",
    "partial_delayed_tool_result": "backend/tests/test_tools.py::test_a_hung_handler_times_out_and_is_never_recorded_as_executed",
    "orphan_state_transition": "backend/tests/test_agent_checkpoints.py::test_every_disallowed_edge_is_rejected_and_every_allowed_edge_is_accepted",
    "persistence_loss": "backend/tests/test_agent_checkpoint_process_recovery.py::test_checkpoint_survives_fresh_process_and_resumes_exactly_once",
    "schema_drift": "backend/tests/test_db_upgrade.py::test_newer_schema_is_refused_and_left_untouched",
    "unexpected_codes": "backend/tests/test_work_runs.py::test_unknown_command_is_reported_not_silently_dropped",
}

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


def _repo_relative(path: Path) -> str:
    target = path.expanduser().resolve()
    try:
        return target.relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise shadow_evidence.ShadowEvidenceError(
            f"Shadow evidence output must stay inside the repository: {path}"
        ) from exc


def certify_exact_vectors(
    *,
    base_sha: str,
    checkpoint_sha: str,
    broad_gates: list[dict[str, object]],
    execution_receipt_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, object], dict[str, dict[str, object]], str, str]:
    if set(VECTOR_TEST_SELECTORS) != set(REQUIRED_ADVERSARIAL_VECTORS):
        raise shadow_evidence.ShadowEvidenceError("vector selector denominator does not match required vectors")

    selectors = [VECTOR_TEST_SELECTORS[vector] for vector in REQUIRED_ADVERSARIAL_VECTORS]
    gate = run([sys.executable, "-m", "pytest", "-q", *selectors])
    broad_pass = all(
        gate_row.get("state") != "BLOCKED"
        and (not isinstance(gate_row.get("returncode"), int) or gate_row["returncode"] == 0)
        for gate_row in broad_gates
    )
    status = "PASS" if broad_pass and gate["returncode"] == 0 else "FAIL"

    receipt_target = execution_receipt_path.expanduser().resolve()
    manifest_target = manifest_path.expanduser().resolve()
    receipt_rel = _repo_relative(receipt_target)
    manifest_rel = _repo_relative(manifest_target)
    receipt_target.parent.mkdir(parents=True, exist_ok=True)
    manifest_target.parent.mkdir(parents=True, exist_ok=True)

    execution_receipt = {
        "schema_version": shadow_evidence.EXECUTION_RECEIPT_SCHEMA_VERSION,
        "base_sha": base_sha,
        "checkpoint_sha": checkpoint_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "broad_gates_passed": broad_pass,
        "vector_selector_count": len(selectors),
        "vector_selectors": selectors,
        "vector_gate_returncode": gate["returncode"],
    }
    receipt_target.write_text(json.dumps(execution_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    vectors = []
    for vector in REQUIRED_ADVERSARIAL_VECTORS:
        selector = VECTOR_TEST_SELECTORS[vector]
        path, _, _test = selector.partition("::")
        if status == "PASS":
            vectors.append(
                {
                    "vector": vector,
                    "state": "PASS",
                    "rationale": "Exact-checkpoint dedicated adversarial selector passed.",
                    "evidence": [
                        {
                            "evidence_id": f"{vector}-checkpoint-test",
                            "kind": "CHECKPOINT_TEST",
                            "path": path,
                            "selector": selector,
                        },
                        {
                            "evidence_id": f"{vector}-execution-receipt",
                            "kind": "EXECUTION_RECEIPT",
                            "path": receipt_rel,
                            "selector": "",
                        },
                    ],
                }
            )
        else:
            vectors.append(
                {
                    "vector": vector,
                    "state": "UNRESOLVED",
                    "rationale": "Exact-checkpoint dedicated adversarial selector suite did not pass.",
                    "evidence": [],
                }
            )

    manifest = {
        "schema_version": shadow_evidence.SCHEMA_VERSION,
        "base_sha": base_sha,
        "checkpoint_sha": checkpoint_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "EXACT_CHECKPOINT_GENERATED",
        "vectors": vectors,
    }
    manifest_target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    normalized = shadow_evidence.validate_manifest(
        manifest,
        base_sha=base_sha,
        checkpoint_sha=checkpoint_sha,
        required_vectors=REQUIRED_ADVERSARIAL_VECTORS,
    )
    return gate, normalized, receipt_rel, manifest_rel


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded MUCARO Shadow QA against an exact frozen checkpoint.")
    parser.add_argument("--base", default="HEAD^", help="Base commit/ref for the changed-surface denominator.")
    parser.add_argument("--head", default="HEAD", help="Frozen checkpoint commit/ref.")
    parser.add_argument("--evidence-manifest", type=Path, help="Checkpoint-bound vector evidence manifest.")
    parser.add_argument("--historical-replay", action="store_true", help="Adjudicate a historical checkpoint from frozen evidence without claiming its gates were rerun.")
    parser.add_argument(
        "--execution-receipt",
        type=Path,
        default=Path(os.environ.get("MUCARO_SHADOW_EXECUTION_RECEIPT", "data/exports/shadow/shadow-execution-receipt.json")),
        help="Exact-checkpoint execution receipt emitted after broad and vector gates.",
    )
    parser.add_argument(
        "--generated-evidence-manifest",
        type=Path,
        default=Path(os.environ.get("MUCARO_SHADOW_GENERATED_EVIDENCE", "data/exports/shadow/shadow-vector-evidence.json")),
        help="Generated exact-checkpoint vector evidence manifest.",
    )
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
                "state": "AUDIT_ONLY",
                "reason": (
                    "Historical replay validates frozen exact-checkpoint execution evidence; "
                    "checkpoint code is not rerun from the tooling checkout."
                ),
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

    rendered_state, rendered_reason = classify_rendered(paths)
    generated_manifest: str | None = None
    execution_receipt: str | None = None
    try:
        if args.evidence_manifest:
            vector_results = shadow_evidence.load_manifest(
                args.evidence_manifest,
                base_sha=base_sha,
                checkpoint_sha=head_sha,
                required_vectors=REQUIRED_ADVERSARIAL_VECTORS,
            )
        elif args.historical_replay:
            vector_results = adversarial_vector_results(paths)
        else:
            initial_failed = [
                gate for gate in gates if isinstance(gate.get("returncode"), int) and gate["returncode"] != 0
            ]
            initial_blocked = [gate for gate in gates if gate.get("state") == "BLOCKED"]
            if initial_failed or initial_blocked:
                vector_results = adversarial_vector_results(paths)
            else:
                vector_gate, vector_results, execution_receipt, generated_manifest = certify_exact_vectors(
                    base_sha=base_sha,
                    checkpoint_sha=head_sha,
                    broad_gates=gates,
                    execution_receipt_path=args.execution_receipt,
                    manifest_path=args.generated_evidence_manifest,
                )
                gates.append(vector_gate)
    except shadow_evidence.ShadowEvidenceError as exc:
        print(f"ERROR_SHADOW_EVIDENCE {exc}", file=sys.stderr)
        return 7

    failed = [gate for gate in gates if isinstance(gate.get("returncode"), int) and gate["returncode"] != 0]
    blocked = [gate for gate in gates if gate.get("state") == "BLOCKED"]

    unresolved_vectors = [
        name for name, result in vector_results.items() if result["state"] == "UNRESOLVED"
    ]
    blocked_vectors = [
        name for name, result in vector_results.items() if result["state"] == "BLOCKED"
    ]

    # A gate can verify the declared changed-file denominator only when every executable gate passes
    # and every required adversarial vector is explicitly classified.
    # BLOCKED gates or unresolved vectors remain unresolved rather than being silently counted as verified.
    if failed:
        verified: list[str] = []
        unresolved = list(paths)
        overall = "FAIL"
    elif blocked or unresolved_vectors or blocked_vectors:
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
            "blocked": len(blocked_vectors),
            "closes": len(vector_results) == len(REQUIRED_ADVERSARIAL_VECTORS),
        },
        "execution_mode": "HISTORICAL_EVIDENCE_REPLAY" if args.historical_replay else "EXACT_CHECKPOINT_EXECUTION",
        "evidence_manifest": str(args.evidence_manifest) if args.evidence_manifest else generated_manifest,
        "execution_receipt": execution_receipt,
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
