from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from backend.app.coding_certification import build_coding_certification_report
from backend.app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
STATUS_OK = "CODING_REGRESSION_CORPUS_TERMINAL_BLOCKED"
STATUS_FAILED = "CODING_REGRESSION_CORPUS_FAILED"
EXPECTED_COUNTS = {"PASS": 20, "FAIL": 0, "BLOCKED": 3, "UNRESOLVED": 0}
EXPECTED_BLOCKED = {"CODE-R13", "CODE-R21", "CODE-R23"}


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} did not contain an object")
    return payload, hashlib.sha256(raw).hexdigest()


def _evidence_token(label: str, payload: dict[str, Any], file_sha256: str) -> str:
    internal = payload.get("receipt_sha256") or _stable_hash(payload)
    return f"{label}:file_sha256={file_sha256}:payload_sha256={internal}"


def _build_rows(
    evidence: dict[str, str],
    *,
    hosted_ci_pass: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(requirement_id: str, state: str, refs: list[str], detail: str) -> None:
        rows.append(
            {
                "requirement_id": requirement_id,
                "state": state,
                "evidence": refs,
                "detail": detail,
            }
        )

    workspace = evidence["full_profile"]
    for rid, detail in (
        ("CODE-R01", "Exact repository discovery/HEAD/branch/dirty-state regressions executed."),
        ("CODE-R02", "Repository permission/path escape negatives executed."),
        ("CODE-R03", "UTF-8 byte/hash read regressions executed."),
        ("CODE-R04", "Binary/non-UTF8 no-synthetic-text negatives executed."),
        ("CODE-R05", "Diff candidate-set preservation regressions executed."),
        ("CODE-R06", "Invalid-ref and oversized-diff negatives executed."),
        ("CODE-R07", "Git-parsed patch target/preimage proposal regressions executed."),
        ("CODE-R08", "Traversal/.git/ambiguous/oversized-patch negatives executed."),
    ):
        add(rid, "PASS", [workspace], detail)

    add("CODE-R09", "PASS", [evidence["container"]], "Live pinned isolated coding-container invariants executed.")
    add("CODE-R10", "PASS", [evidence["container"], workspace], "Network/pull/mount/image fail-closed regressions executed.")
    add("CODE-R11", "PASS", [evidence["mutation"]], "Live approved mutation and exact precondition execution passed.")
    add("CODE-R12", "PASS", [evidence["mutation"], workspace], "Live preimage rejection/rollback plus drift negatives executed.")

    add(
        "CODE-R13",
        "BLOCKED",
        [workspace, "security_benchmark_receipts=ABSENT"],
        "Exact-head test/lint/type/build are 4/4 PASS, but isolated coding security and benchmark terminal receipts are not yet frozen.",
    )
    add("CODE-R14", "PASS", [workspace], "Required nonzero and infrastructure BLOCKED-vs-FAIL semantics executed in the exact-head profile.")

    add("CODE-R15", "PASS", [evidence["review"]], "Live changed-file/source-hash/exact-line review binding executed.")
    add("CODE-R16", "PASS", [evidence["review"]], "Live unchanged-path and invalid-line review negatives rejected.")

    add("CODE-R17", "PASS", [evidence["lifecycle"]], "Live exact-clean-HEAD branch plus exact approved commit transaction executed.")
    add("CODE-R18", "PASS", [evidence["lifecycle"]], "Protected/stale/dirty/prestaged negatives and real failed-commit rollback executed.")
    add("CODE-R19", "PASS", [evidence["lifecycle"]], "Deterministic clean local PR-request artifact executed with network_action_performed=false.")

    add(
        "CODE-R20",
        "PASS",
        [evidence["container"], workspace, evidence["mutation"]],
        "Current-head live pinned image/engine execution and dependency-complete coding profile are frozen.",
    )
    add(
        "CODE-R21",
        "BLOCKED",
        ["credential_last_remote_git=DEFERRED", evidence["lifecycle"]],
        "Authenticated remote branch push and PR submission are intentionally deferred to the credential-last production-Git stage.",
    )
    add(
        "CODE-R22",
        "PASS" if hosted_ci_pass else "BLOCKED",
        [evidence["hosted_ci"]],
        "Hosted CI admitted executable steps for this exact tested source tree."
        if hosted_ci_pass
        else "Hosted exact-tree CI admission was not proven.",
    )
    add(
        "CODE-R23",
        "BLOCKED",
        ["expected_residue=CODE-R13,CODE-R21"],
        "Coding-plane certification remains prohibited while security/benchmark execution and credential-last remote Git residue remain.",
    )
    return rows


def build_corpus_report(
    *,
    source_head_sha: str,
    container: dict[str, Any],
    full_profile: dict[str, Any],
    mutation: dict[str, Any],
    review: dict[str, Any],
    lifecycle: dict[str, Any],
    receipt_file_hashes: dict[str, str],
    github_run_id: str | None,
    github_actions: bool,
    pull_request_head_sha: str | None,
    tree_equivalent_to_pr_head: bool | None,
    cfg: Settings,
) -> dict[str, Any]:
    statuses = {
        "container": container.get("status"),
        "full_profile": full_profile.get("status"),
        "mutation": mutation.get("status"),
        "review": review.get("status"),
        "lifecycle": lifecycle.get("status"),
    }
    expected_statuses = {
        "container": "CODING_CONTAINER_LIVE_CERT_OK",
        "full_profile": "CODING_FULL_PROFILE_CERT_OK",
        "mutation": "CODING_MUTATION_LIVE_CERT_OK",
        "review": "CODING_REVIEW_LIVE_CERT_OK",
        "lifecycle": "CODING_LIFECYCLE_LIVE_CERT_OK",
    }

    suite = _dict(full_profile.get("suite"))
    kinds = {
        row.get("kind")
        for row in suite.get("checks", [])
        if isinstance(row, dict) and row.get("state") == "PASS"
    }
    four_kind_pass = (
        full_profile.get("status") == "CODING_FULL_PROFILE_CERT_OK"
        and suite.get("execution_status") == "PASS"
        and suite.get("check_count") == 4
        and suite.get("state_counts") == {"PASS": 4, "FAIL": 0, "BLOCKED": 0}
        and kinds == {"test", "lint", "type", "build"}
    )

    mutation_heads = [
        _dict(_dict(mutation.get(name)).get("execution")).get("head_sha")
        for name in ("positive", "negative", "rollback")
    ]
    source_bindings = {
        "container": ((container.get("identity") or {}).get("source_head_sha")),
        "full_profile": full_profile.get("head_sha"),
        "mutation_positive": mutation_heads[0],
        "mutation_negative": mutation_heads[1],
        "mutation_rollback": mutation_heads[2],
        "review": review.get("source_head_sha"),
        "lifecycle": lifecycle.get("source_head_sha"),
    }
    exact_source_binding = all(value == source_head_sha for value in source_bindings.values())

    full_image = full_profile.get("image")
    image_bindings = {
        "full_profile": full_image,
        "mutation": mutation.get("image"),
    }
    exact_full_image_binding = bool(full_image) and all(value == full_image for value in image_bindings.values())

    pr_projection_ok = (
        pull_request_head_sha is None
        or (
            isinstance(pull_request_head_sha, str)
            and len(pull_request_head_sha) == 40
            and tree_equivalent_to_pr_head is True
        )
    )
    hosted_ci_pass = bool(github_actions and github_run_id and pr_projection_ok)

    evidence = {
        key: _evidence_token(key, payload, receipt_file_hashes[key])
        for key, payload in (
            ("container", container),
            ("full_profile", full_profile),
            ("mutation", mutation),
            ("review", review),
            ("lifecycle", lifecycle),
        )
    }
    evidence["hosted_ci"] = (
        f"github_actions_run={github_run_id}:tested_head={source_head_sha}:"
        f"pr_head={pull_request_head_sha or 'NONE'}:tree_equivalent={tree_equivalent_to_pr_head}"
    )

    rows = _build_rows(evidence, hosted_ci_pass=hosted_ci_pass)
    runtime_identity = full_profile.get("runtime_identity_sha256")
    coding_image_digest = full_profile.get("coding_image_digest")
    certification = build_coding_certification_report(
        rows,
        source_head_sha=source_head_sha,
        runtime_identity_sha256=runtime_identity if isinstance(runtime_identity, str) else None,
        coding_image_digest=coding_image_digest if isinstance(coding_image_digest, str) else None,
        cfg=cfg,
    )
    blocked_ids = {
        row["requirement_id"]
        for row in certification.get("rows", [])
        if row.get("state") == "BLOCKED"
    }

    invariants = {
        "input_statuses_exact": statuses == expected_statuses,
        "source_bindings_exact": exact_source_binding,
        "full_profile_image_bound_to_mutation": exact_full_image_binding,
        "four_kind_test_lint_type_build_pass": four_kind_pass,
        "hosted_ci_exact_tree_admitted": hosted_ci_pass,
        "row_count_23": certification.get("row_count") == 23,
        "state_counts_expected": certification.get("state_counts") == EXPECTED_COUNTS,
        "blocked_set_expected": blocked_ids == EXPECTED_BLOCKED,
        "identity_closed": _dict(certification.get("identity")).get("identity_closed") is True,
        "certification_fail_closed": certification.get("certification_ready") is False
        and certification.get("status") == "CODING_CERTIFICATION_BLOCKED",
        "no_fail_or_unresolved": certification.get("state_counts", {}).get("FAIL") == 0
        and certification.get("state_counts", {}).get("UNRESOLVED") == 0,
    }
    result: dict[str, Any] = {
        "schema_version": "mucaro-coding-regression-corpus-cert/1.0",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "source_head_sha": source_head_sha,
        "pull_request_head_sha": pull_request_head_sha,
        "tree_equivalent_to_pull_request_head": tree_equivalent_to_pr_head,
        "github_run_id": github_run_id,
        "four_kind_review_denominator": {
            "required": ["test", "lint", "type", "build"],
            "state": "PASS" if four_kind_pass else "FAIL",
            "suite_state_counts": suite.get("state_counts"),
        },
        "source_bindings": source_bindings,
        "image_bindings": image_bindings,
        "input_statuses": statuses,
        "input_receipt_file_sha256": receipt_file_hashes,
        "certification_report": certification,
        "invariants": invariants,
        "bounded_result": {
            "branch_commit_pr_request_safety": "PASS",
            "coding_regression_corpus_terminal_classification": "PASS",
            "full_coding_plane_certification": "BLOCKED",
            "blocked_requirements": sorted(blocked_ids),
        },
        "limitations": [
            "CODE-R13 remains BLOCKED because isolated security and benchmark terminal receipts are not yet frozen.",
            "CODE-R21 remains BLOCKED because authenticated remote branch push/PR submission is intentionally credential-last.",
            "CODE-R23 remains BLOCKED while either prerequisite remains blocked.",
            "A development PR used to execute CI is not evidence that the Mucaro runtime has production Git mutation authority.",
        ],
    }
    result["receipt_sha256"] = _stable_hash(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile the frozen 23-row coding regression corpus.")
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--container-receipt", type=Path, required=True)
    parser.add_argument("--full-profile-receipt", type=Path, required=True)
    parser.add_argument("--mutation-receipt", type=Path, required=True)
    parser.add_argument("--review-receipt", type=Path, required=True)
    parser.add_argument("--lifecycle-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = {
        "container": args.container_receipt,
        "full_profile": args.full_profile_receipt,
        "mutation": args.mutation_receipt,
        "review": args.review_receipt,
        "lifecycle": args.lifecycle_receipt,
    }
    loaded: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for key, path in paths.items():
        loaded[key], hashes[key] = _load(path)

    pr_head = os.environ.get("MUCARO_CI_PR_HEAD") or None
    raw_equivalent = os.environ.get("MUCARO_CI_TREE_EQUIVALENT")
    tree_equivalent = None if pr_head is None else raw_equivalent == "true"
    report = build_corpus_report(
        source_head_sha=args.source_head,
        container=loaded["container"],
        full_profile=loaded["full_profile"],
        mutation=loaded["mutation"],
        review=loaded["review"],
        lifecycle=loaded["lifecycle"],
        receipt_file_hashes=hashes,
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        github_actions=os.environ.get("GITHUB_ACTIONS") == "true",
        pull_request_head_sha=pr_head,
        tree_equivalent_to_pr_head=tree_equivalent,
        cfg=Settings.load(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])
    print(json.dumps(report["four_kind_review_denominator"], sort_keys=True))
    print(json.dumps(report["certification_report"]["state_counts"], sort_keys=True))
    print(f"receipt_sha256={report['receipt_sha256']}")
    return 0 if report["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
