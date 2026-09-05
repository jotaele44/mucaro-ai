from __future__ import annotations

import subprocess
import sys

import pytest

import scripts.certify_fresh_clone as certify_fresh_clone
import scripts.certify_release as certify_release
import scripts.launch_app as launch_app


def test_launch_app_self_test():
    proc = subprocess.run(
        [sys.executable, "scripts/launch_app.py", "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0
    assert "LAUNCH_APP_SELF_TEST_OK" in proc.stdout


def test_package_desktop_self_test():
    proc = subprocess.run(
        [sys.executable, "scripts/package_desktop.py", "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    assert proc.returncode == 0
    assert "DESKTOP_PACKAGE_READY" in proc.stdout


def test_certify_release_includes_package_artifact_gate(monkeypatch, capsys):
    calls = []

    def fake_tracked_files():
        return []

    def fake_gate(name, status="PASS", detail="ok"):
        return certify_release.Gate(name, status, detail)

    def fake_gate_command(name, cmd, *, cwd=certify_release.ROOT):
        calls.append((name, cmd, cwd))
        return fake_gate(name)

    def fake_write_release_receipt(*args, **kwargs):
        del args, kwargs
        return certify_release.ROOT / "data" / "exports" / "release_receipts" / "local_release-test.json"

    monkeypatch.setattr(certify_release, "tracked_files", fake_tracked_files)
    monkeypatch.setattr(certify_release, "gate_worktree_clean", lambda: fake_gate("worktree_clean"))
    monkeypatch.setattr(certify_release, "gate_no_prior_names", lambda files: fake_gate("prior_name_residue"))
    monkeypatch.setattr(certify_release, "gate_no_secret_patterns", lambda files: fake_gate("secret_pattern_scan"))
    monkeypatch.setattr(certify_release, "gate_no_large_files", lambda files: fake_gate("large_file_scan"))
    monkeypatch.setattr(certify_release, "gate_no_generated_tracked", lambda files: fake_gate("generated_artifact_scan"))
    monkeypatch.setattr(certify_release, "gate_command", fake_gate_command)
    monkeypatch.setattr(certify_release, "write_release_receipt", fake_write_release_receipt)
    monkeypatch.setattr(
        certify_release,
        "gate_package_artifact",
        lambda: (fake_gate("package_artifact", detail="manifest_member_count=1"), {"status": "PACKAGE_ARTIFACT_READY"}),
    )
    monkeypatch.setattr(
        certify_release,
        "gate_package_artifact_verification",
        lambda package_artifact: fake_gate("package_artifact_verify", detail="manifest_member_count=1"),
    )

    assert certify_release.main() == 0
    output = capsys.readouterr().out
    assert "PASS package_artifact manifest_member_count=1" in output
    assert "PASS package_artifact_verify manifest_member_count=1" in output
    assert "CERTIFY_RELEASE_OK" in output


def test_fresh_clone_uses_shallow_branch_clone_for_named_refs(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, *, cwd, check=True):
        del check
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "")

    monkeypatch.setattr(certify_fresh_clone, "run", fake_run)

    clone_dir = tmp_path / "mucaro-ai"
    certify_fresh_clone.clone_ref("https://github.com/example/mucaro-ai.git", "main", tmp_path, clone_dir)

    assert calls == [
        (
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "main",
                "https://github.com/example/mucaro-ai.git",
                str(clone_dir),
            ],
            tmp_path,
        )
    ]


def test_fresh_clone_fetches_exact_commit_sha(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, *, cwd, check=True):
        del check
        calls.append((cmd, cwd))
        return subprocess.CompletedProcess(cmd, 0, "")

    monkeypatch.setattr(certify_fresh_clone, "run", fake_run)

    clone_dir = tmp_path / "mucaro-ai"
    commit_sha = "8e8cf79b03daebaf12caa51050f6143f52fc6e44"
    certify_fresh_clone.clone_ref("https://github.com/example/mucaro-ai.git", commit_sha, tmp_path, clone_dir)

    assert calls == [
        (["git", "clone", "--no-checkout", "https://github.com/example/mucaro-ai.git", str(clone_dir)], tmp_path),
        (["git", "fetch", "--depth", "1", "origin", commit_sha], clone_dir),
        (["git", "checkout", "--detach", commit_sha], clone_dir),
    ]


def test_launch_app_reports_missing_frontend_deps(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(launch_app, "ROOT", tmp_path)

    with pytest.raises(SystemExit) as exc:
        launch_app.ensure_frontend_dist()

    assert str(exc.value) == "ERROR_FRONTEND_DEPS_MISSING run: cd frontend && npm ci"


def test_launch_app_rejects_non_mucaro_port_conflict(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["launch_app.py", "--smoke-test", "--no-browser", "--port", "54321"])
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda skip_build=False: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: True)
    monkeypatch.setattr(launch_app, "health_ok", lambda port: False)

    assert launch_app.main() == 5
    error_output = capsys.readouterr().err
    assert "ERROR_PORT_IN_USE_NON_MUCARO_AI port=54321" in error_output
