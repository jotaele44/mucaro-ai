from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_review import CodingReviewError, build_code_review_receipt
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding review tests")


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "initial")
    (repo / "app.py").write_text("def f():\n    value = 2\n    return value\n", encoding="utf-8")
    return repo


def test_review_receipt_binds_findings_to_changed_file_and_lines(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    report = build_code_review_receipt(
        repo,
        [
            {
                "severity": "medium",
                "path": "app.py",
                "start_line": 2,
                "end_line": 3,
                "title": "Example finding",
                "detail": "Validate the changed value path.",
                "falsification": "A regression test proving the intended value path would falsify this finding.",
            }
        ],
        cfg=cfg,
    )
    assert report["status"] == "CODING_REVIEW_FINDINGS"
    assert report["finding_count"] == 1
    assert report["severity_counts"]["medium"] == 1
    assert report["findings"][0]["source_sha256"]
    assert report["findings"][0]["excerpt_sha256"]
    assert report["findings"][0]["evidence_excerpt"] == "    value = 2\n    return value"
    assert all(report["invariants"].values())


def test_review_rejects_unchanged_paths_and_invalid_ranges(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "unchanged.py").write_text("x = 1\n", encoding="utf-8")

    finding = {
        "severity": "high",
        "path": "unchanged.py",
        "start_line": 1,
        "end_line": 1,
        "title": "Not changed",
        "detail": "Should be rejected.",
        "falsification": "Path enters changed candidate set.",
    }
    with pytest.raises(CodingReviewError) as unchanged:
        build_code_review_receipt(repo, [finding], cfg=cfg)
    assert unchanged.value.status == "ERROR_CODING_REVIEW_PATH_NOT_CHANGED"

    finding["path"] = "app.py"
    finding["end_line"] = 99
    with pytest.raises(CodingReviewError) as bad_line:
        build_code_review_receipt(repo, [finding], cfg=cfg)
    assert bad_line.value.status == "ERROR_CODING_REVIEW_LINE"
