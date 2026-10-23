from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.app.coding_lifecycle import (
    CodingLifecycleError,
    build_pull_request_request,
    commit_approved_changes,
    create_work_branch_transaction,
)
from backend.app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
STATUS_OK = "CODING_LIFECYCLE_LIVE_CERT_OK"
STATUS_FAILED = "CODING_LIFECYCLE_LIVE_CERT_FAILED"


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data/input",
        processed_dir=root / "data/processed",
        export_dir=root / "data/exports",
        index_dir=root / "data/indexes",
        sqlite_path=root / "data/processed/lifecycle-cert.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "mucaro-lifecycle-cert@example.invalid")
    _git(repo, "config", "user.name", "Mucaro Lifecycle Certification")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _expect_status(callable_obj, expected: str) -> dict[str, Any]:
    try:
        callable_obj()
    except CodingLifecycleError as exc:
        return {
            "expected": expected,
            "observed": exc.status,
            "detail": exc.detail,
            "passed": exc.status == expected,
            "receipt": exc.receipt,
        }
    return {
        "expected": expected,
        "observed": "UNEXPECTED_ACCEPT",
        "detail": None,
        "passed": False,
        "receipt": None,
    }


def run_certification(source_root: Path = ROOT) -> dict[str, Any]:
    source_root = source_root.resolve()
    source_head_before = _git(source_root, "rev-parse", "HEAD").strip()
    source_status_before = _git(source_root, "status", "--porcelain=v1", "-z")

    with tempfile.TemporaryDirectory(prefix="mucaro-lifecycle-cert-") as tmp:
        temp_root = Path(tmp)
        cfg = _settings(temp_root)

        positive_repo = _make_repo(temp_root, "positive")
        base = _head(positive_repo)
        branch_receipt = create_work_branch_transaction(
            positive_repo,
            branch_name="feature/lifecycle-cert",
            expected_head_sha=base,
            cfg=cfg,
        )
        (positive_repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
        (positive_repo / "new.txt").write_text("new\n", encoding="utf-8")
        commit_receipt = commit_approved_changes(
            positive_repo,
            expected_head_sha=base,
            expected_branch="feature/lifecycle-cert",
            approved_paths=["tracked.txt", "new.txt"],
            message="Certify governed lifecycle",
            cfg=cfg,
        )
        pr_request = build_pull_request_request(
            positive_repo,
            expected_head_sha=commit_receipt["commit_sha"],
            head_branch="feature/lifecycle-cert",
            base_branch="main",
            title="Governed lifecycle certification",
            body="Exact local branch/commit/PR-request evidence. No network mutation.",
            cfg=cfg,
        )
        pr_request_repeat = build_pull_request_request(
            positive_repo,
            expected_head_sha=commit_receipt["commit_sha"],
            head_branch="feature/lifecycle-cert",
            base_branch="main",
            title="Governed lifecycle certification",
            body="Exact local branch/commit/PR-request evidence. No network mutation.",
            cfg=cfg,
        )

        negative: dict[str, dict[str, Any]] = {}

        protected_repo = _make_repo(temp_root, "protected")
        protected_head = _head(protected_repo)
        negative["protected_branch"] = _expect_status(
            lambda: create_work_branch_transaction(
                protected_repo, branch_name="main", expected_head_sha=protected_head, cfg=cfg
            ),
            "ERROR_CODING_PROTECTED_BRANCH",
        )
        negative["stale_branch_head"] = _expect_status(
            lambda: create_work_branch_transaction(
                protected_repo, branch_name="feature/stale", expected_head_sha="0" * 40, cfg=cfg
            ),
            "ERROR_CODING_BRANCH_HEAD_DRIFT",
        )
        (protected_repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        negative["dirty_branch"] = _expect_status(
            lambda: create_work_branch_transaction(
                protected_repo, branch_name="feature/dirty", expected_head_sha=protected_head, cfg=cfg
            ),
            "ERROR_CODING_BRANCH_DIRTY_REPOSITORY",
        )

        drift_repo = _make_repo(temp_root, "dirty-set")
        drift_head = _head(drift_repo)
        create_work_branch_transaction(
            drift_repo, branch_name="feature/dirty-set", expected_head_sha=drift_head, cfg=cfg
        )
        (drift_repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
        (drift_repo / "extra.txt").write_text("extra\n", encoding="utf-8")
        negative["dirty_set_drift"] = _expect_status(
            lambda: commit_approved_changes(
                drift_repo,
                expected_head_sha=drift_head,
                expected_branch="feature/dirty-set",
                approved_paths=["tracked.txt"],
                message="Incomplete approval",
                cfg=cfg,
            ),
            "ERROR_CODING_COMMIT_DIRTY_SET_DRIFT",
        )
        _git(drift_repo, "add", "tracked.txt", "extra.txt")
        negative["prestaged_state"] = _expect_status(
            lambda: commit_approved_changes(
                drift_repo,
                expected_head_sha=drift_head,
                expected_branch="feature/dirty-set",
                approved_paths=["tracked.txt", "extra.txt"],
                message="Prestaged",
                cfg=cfg,
            ),
            "ERROR_CODING_COMMIT_PRESTAGED_STATE",
        )

        rollback_repo = _make_repo(temp_root, "rollback")
        rollback_head = _head(rollback_repo)
        create_work_branch_transaction(
            rollback_repo, branch_name="feature/rollback", expected_head_sha=rollback_head, cfg=cfg
        )
        (rollback_repo / "tracked.txt").write_text("beta\n", encoding="utf-8")
        _git(rollback_repo, "config", "commit.gpgsign", "true")
        _git(rollback_repo, "config", "gpg.program", "/definitely/missing-mucaro-gpg")
        negative["failed_commit_rollback"] = _expect_status(
            lambda: commit_approved_changes(
                rollback_repo,
                expected_head_sha=rollback_head,
                expected_branch="feature/rollback",
                approved_paths=["tracked.txt"],
                message="Must be rejected by hook",
                cfg=cfg,
            ),
            "CODING_COMMIT_ROLLED_BACK",
        )
        rollback_state = {
            "head_sha": _head(rollback_repo),
            "staged_paths": sorted(
                path for path in _git(rollback_repo, "diff", "--cached", "--name-only").splitlines() if path
            ),
            "worktree_text": (rollback_repo / "tracked.txt").read_text(encoding="utf-8"),
        }

        pr_repo = _make_repo(temp_root, "pr-negatives")
        pr_head = _head(pr_repo)
        create_work_branch_transaction(pr_repo, branch_name="feature/pr", expected_head_sha=pr_head, cfg=cfg)
        negative["pr_branch_drift"] = _expect_status(
            lambda: build_pull_request_request(
                pr_repo,
                expected_head_sha=pr_head,
                head_branch="feature/other",
                base_branch="main",
                title="Wrong branch",
                body="",
                cfg=cfg,
            ),
            "ERROR_CODING_PR_BRANCH_DRIFT",
        )
        negative["pr_same_branch"] = _expect_status(
            lambda: build_pull_request_request(
                pr_repo,
                expected_head_sha=pr_head,
                head_branch="feature/pr",
                base_branch="feature/pr",
                title="Same branch",
                body="",
                cfg=cfg,
            ),
            "ERROR_CODING_PR_SAME_BRANCH",
        )
        (pr_repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        negative["pr_dirty"] = _expect_status(
            lambda: build_pull_request_request(
                pr_repo,
                expected_head_sha=pr_head,
                head_branch="feature/pr",
                base_branch="main",
                title="Dirty branch",
                body="",
                cfg=cfg,
            ),
            "ERROR_CODING_PR_DIRTY_REPOSITORY",
        )

        source_head_after = _git(source_root, "rev-parse", "HEAD").strip()
        source_status_after = _git(source_root, "status", "--porcelain=v1", "-z")
        negative_pass_count = sum(1 for row in negative.values() if row["passed"])
        invariants = {
            "source_head_conserved": source_head_before == source_head_after,
            "source_dirty_state_conserved": source_status_before == source_status_after,
            "branch_created_at_exact_head": branch_receipt.get("head_sha") == base,
            "branch_invariants_closed": all(branch_receipt.get("invariants", {}).values()),
            "commit_parent_exact": commit_receipt.get("parent_sha") == base,
            "commit_candidate_set_exact": commit_receipt.get("commit_changed_paths")
            == ["new.txt", "tracked.txt"],
            "commit_invariants_closed": all(commit_receipt.get("invariants", {}).values()),
            "pr_request_network_free": pr_request.get("network_action_performed") is False,
            "pr_request_requires_authenticated_adapter": pr_request.get("requires_authenticated_remote_adapter")
            is True,
            "pr_request_invariants_closed": all(pr_request.get("invariants", {}).values()),
            "pr_request_hash_deterministic": pr_request.get("request_sha256")
            == pr_request_repeat.get("request_sha256"),
            "negative_accounting_closed": negative_pass_count == len(negative) == 9,
            "failed_commit_head_restored": rollback_state["head_sha"] == rollback_head,
            "failed_commit_index_restored": rollback_state["staged_paths"] == [],
            "failed_commit_worktree_preserved": rollback_state["worktree_text"] == "beta\n",
        }

        payload: dict[str, Any] = {
            "schema_version": "mucaro-coding-lifecycle-live-cert/1.0",
            "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
            "source_head_sha": source_head_before,
            "positive": {
                "branch_receipt": branch_receipt,
                "commit_receipt": commit_receipt,
                "pr_request": pr_request,
            },
            "negative_cases": negative,
            "negative_case_count": len(negative),
            "negative_pass_count": negative_pass_count,
            "rollback_state": rollback_state,
            "invariants": invariants,
            "limitations": [
                "This receipt certifies local branch/commit/PR-request safety in temporary Git repositories.",
                "It performs no remote branch push, pull-request creation/update, merge, tag, or force operation.",
                "Authenticated remote Git authority remains a separate credential-last gate.",
            ],
        }
        payload["receipt_sha256"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute live governed branch/commit/PR-request safety certification.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = run_certification()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(payload["status"])
    if payload["status"] != STATUS_OK:
        print("failed_invariants=" + json.dumps(
            sorted(key for key, value in payload["invariants"].items() if not value)
        ))
        print("failed_negative_cases=" + json.dumps(
            {
                key: {
                    "expected": value["expected"],
                    "observed": value["observed"],
                }
                for key, value in payload["negative_cases"].items()
                if not value["passed"]
            },
            sort_keys=True,
        ))
    print(f"receipt_sha256={payload['receipt_sha256']}")
    return 0 if payload["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
