from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.security_scan import SecurityScanError, preflight_repository, scan_repository

pytestmark = pytest.mark.skipif(shutil.which("git") is None or os.name != "posix", reason="Git and POSIX no-follow handles required; Windows scan certification is OPEN")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def repo_fixture(tmp_path: Path):
    cfg = Settings(
        project_root=tmp_path, data_dir=tmp_path / "data", input_dir=tmp_path / "data" / "input",
        processed_dir=tmp_path / "data" / "processed", export_dir=tmp_path / "data" / "exports",
        index_dir=tmp_path / "data" / "indexes", sqlite_path=tmp_path / "data" / "processed" / "test.sqlite",
        default_model_provider="mock", default_model_name="mock",
    )
    cfg.ensure_dirs()
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Mucaro")
    (root / "safe.py").write_text('value = eval("1")\n', encoding="utf-8")
    (root / "clean.py").write_text("print('ok')\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return root, cfg


def test_preflight_and_candidate_count(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    ready = preflight_repository(str(root), cfg=cfg)
    assert ready["tracked_file_count"] == 2
    report = scan_repository(str(root), ready["head_sha"], cfg=cfg)
    assert report["status"] == "SCAN_BOUNDED"
    assert report["finding_count"] == 1
    assert report["findings"][0]["rule"] == "DYNAMIC_EVAL"
    assert "eval(" not in str(report)
    assert sum(report["counts"].values()) == report["tracked_file_count"]


def test_negative_head_and_invalid_limits(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    with pytest.raises(SecurityScanError) as exc:
        scan_repository(str(root), "0" * 40, cfg=cfg)
    assert exc.value.status == "ERROR_SCAN_STALE_HEAD"
    with pytest.raises(SecurityScanError) as exc:
        scan_repository(str(root), git(root, "rev-parse", "HEAD"), cfg=cfg, max_files=1)
    assert exc.value.status == "ERROR_SCAN_FILE_LIMIT"


def test_dirty_repo_fail_closed(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    (root / "clean.py").write_text("changed\n", encoding="utf-8")
    with pytest.raises(SecurityScanError) as exc:
        preflight_repository(str(root), cfg=cfg)
    assert exc.value.status == "ERROR_SCAN_DIRTY"


def test_binary_and_symlink_explicitly_excluded(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    (root / "odd.dat").write_bytes(b"\xff\xfe\x00")
    (root / "alias").symlink_to(root / "safe.py")
    git(root, "add", ".")
    git(root, "commit", "-m", "tracked exclusions")
    report = scan_repository(str(root), git(root, "rev-parse", "HEAD"), cfg=cfg)
    assert report["status"] == "SCAN_AUDIT_ONLY_PARTIAL"
    assert report["counts"]["BINARY"] == 1
    assert report["counts"]["NONREGULAR"] == 1


def test_local_api_preflight_scan_and_stale_guard(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    client = TestClient(create_app(cfg))
    ready = client.post("/security-scans/preflight", json={"path": str(root)})
    assert ready.status_code == 200, ready.text
    good = client.post("/security-scans/run", json={"path": str(root), "expected_head": ready.json()["head_sha"]})
    assert good.status_code == 200, good.text
    assert good.json()["finding_count"] == 1
    stale = client.post("/security-scans/run", json={"path": str(root), "expected_head": "0" * 40})
    assert stale.status_code == 409
    assert stale.json()["detail"]["status"] == "ERROR_SCAN_STALE_HEAD"


def test_permission_boundary_rejects_ungranted_external_repo(tmp_path):
    from dataclasses import replace

    root, cfg = repo_fixture(tmp_path)
    restricted = replace(cfg, project_root=tmp_path / "unrelated-project")
    with pytest.raises(SecurityScanError) as exc:
        preflight_repository(str(root), cfg=restricted)
    assert exc.value.status == "ERROR_DIRECTORY_PERMISSION_REQUIRED"


def test_scan_does_not_mutate_worktree(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    status_before = git(root, "status", "--porcelain")
    report = scan_repository(str(root), head, cfg=cfg)
    status_after = git(root, "status", "--porcelain")
    assert report["head_sha"] == head
    assert status_before == status_after == ""
    assert git(root, "rev-parse", "HEAD") == head


def test_remote_scan_request_rejected_even_with_valid_operator_token(tmp_path):
    from dataclasses import replace

    root, cfg = repo_fixture(tmp_path)
    protected_cfg = replace(cfg, operator_token="local-fixture-secret")
    client = TestClient(create_app(protected_cfg), client=("198.51.100.27", 50123))
    response = client.post(
        "/security-scans/preflight",
        json={"path": str(root)},
        headers={"X-Mucaro-Token": "local-fixture-secret"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "ERROR_SCAN_LOCAL_ONLY"


def test_no_implicit_write_access_or_arbitrary_path(tmp_path):
    root, cfg = repo_fixture(tmp_path)
    with pytest.raises(SecurityScanError):
        scan_repository(str(root / "nonexistent"), "0" * 40, cfg=cfg)
