from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from backend.app.coding_review import CodingReviewError, build_code_review_receipt
from backend.app.config import Settings

STATUS_OK = "CODING_REVIEW_LIVE_CERT_OK"
STATUS_FAILED = "CODING_REVIEW_LIVE_CERT_FAILED"


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data/input",
        processed_dir=root / "data/processed",
        export_dir=root / "data/exports",
        index_dir=root / "data/indexes",
        sqlite_path=root / "data/processed/review-cert.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute live coding-review evidence certification.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="mucaro-review-cert-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        cfg = _settings(root)

        _run_git(repo, "init")
        _run_git(repo, "config", "user.email", "mucaro-cert@example.invalid")
        _run_git(repo, "config", "user.name", "Mucaro Review Certification")

        original = "def f():\n    return 1\n"
        changed = "def f():\n    value = 2\n    return value\n"
        unchanged = "sentinel = 1\n"
        (repo / "app.py").write_text(original, encoding="utf-8")
        (repo / "unchanged.py").write_text(unchanged, encoding="utf-8")
        _run_git(repo, "add", "app.py", "unchanged.py")
        _run_git(repo, "commit", "-m", "initial")
        base_head = _run_git(repo, "rev-parse", "HEAD")

        (repo / "app.py").write_text(changed, encoding="utf-8")
        report = build_code_review_receipt(
            repo,
            [
                {
                    "severity": "high",
                    "path": "app.py",
                    "start_line": 2,
                    "end_line": 3,
                    "title": "Changed value path",
                    "detail": "Review evidence must bind to exact changed lines.",
                    "falsification": "A source change altering the excerpt hash invalidates this receipt.",
                }
            ],
            cfg=cfg,
        )

        expected_excerpt = "    value = 2\n    return value"
        expected_source_sha = _sha256_bytes(changed.encode("utf-8"))
        expected_excerpt_sha = _sha256_bytes(expected_excerpt.encode("utf-8"))
        finding = report["findings"][0]

        negative: dict[str, str] = {}
        try:
            build_code_review_receipt(
                repo,
                [
                    {
                        "severity": "medium",
                        "path": "unchanged.py",
                        "start_line": 1,
                        "end_line": 1,
                        "title": "Unchanged path",
                        "detail": "Must fail closed.",
                        "falsification": "Path becomes changed.",
                    }
                ],
                cfg=cfg,
            )
            negative["unchanged_path"] = "UNEXPECTED_ACCEPT"
        except CodingReviewError as exc:
            negative["unchanged_path"] = exc.status

        try:
            build_code_review_receipt(
                repo,
                [
                    {
                        "severity": "medium",
                        "path": "app.py",
                        "start_line": 2,
                        "end_line": 99,
                        "title": "Invalid line range",
                        "detail": "Must fail closed.",
                        "falsification": "Range becomes valid.",
                    }
                ],
                cfg=cfg,
            )
            negative["invalid_line_range"] = "UNEXPECTED_ACCEPT"
        except CodingReviewError as exc:
            negative["invalid_line_range"] = exc.status

        invariants = {
            "status_terminal": report["status"] == "CODING_REVIEW_FINDINGS",
            "finding_count_closed": report["finding_count"] == 1,
            "changed_source_bound": report["invariants"]["changed_sources_bound"] is True,
            "severity_accounting_closed": report["invariants"]["severity_accounting_closed"] is True,
            "compatibility_line_binding_closed": report["invariants"]["compatibility_line_binding_closed"] is True,
            "source_sha256_exact": finding["source_sha256"] == expected_source_sha,
            "excerpt_exact": finding["evidence_excerpt"] == expected_excerpt,
            "excerpt_sha256_exact": finding["excerpt_sha256"] == expected_excerpt_sha,
            "unchanged_path_rejected": negative["unchanged_path"] == "ERROR_CODING_REVIEW_PATH_NOT_CHANGED",
            "invalid_line_rejected": negative["invalid_line_range"] == "ERROR_CODING_REVIEW_LINE",
            "head_conserved": _run_git(repo, "rev-parse", "HEAD") == base_head,
        }

        result: dict[str, Any] = {
            "schema_version": "mucaro-coding-review-live-cert/1.0",
            "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
            "scope": "CODE-08_REVIEW_EVIDENCE_FILE_LINE_BINDING",
            "repo_head_sha": base_head,
            "report": report,
            "negative_cases": negative,
            "invariants": invariants,
            "limitations": [
                "This certification covers changed UTF-8 text files and exact file/line evidence binding.",
                "Deleted files and binary/non-UTF8 review evidence remain outside this adapter.",
                "No-findings is never treated as proof that no defects exist.",
            ],
        }
        result["receipt_sha256"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(result["status"])
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
