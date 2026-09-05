from __future__ import annotations

import io
import subprocess
import sys
import tarfile

import pytest

import scripts.certify_fresh_clone as certify_fresh_clone
import scripts.certify_python_distribution as certify_python_distribution
import scripts.certify_release as certify_release
import scripts.certify_source_artifact as certify_source_artifact
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


def test_source_artifact_extracts_expected_archive_root(tmp_path):
    archive_path = tmp_path / "source.zip"
    with certify_source_artifact.zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("mucaro-ai-0.1.23/README.md", "# Mucaro AI\n")
    manifest = {
        "package_name": "mucaro-ai",
        "version": "0.1.23",
        "archive_path": str(archive_path),
    }

    source_dir = certify_source_artifact.extract_source_archive(manifest, tmp_path / "extract")

    assert source_dir == tmp_path / "extract" / "mucaro-ai-0.1.23"
    assert (source_dir / "README.md").read_text(encoding="utf-8") == "# Mucaro AI\n"


def test_source_artifact_rejects_unexpected_archive_root(tmp_path):
    archive_path = tmp_path / "source.zip"
    with certify_source_artifact.zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("wrong-root/README.md", "# Mucaro AI\n")
    manifest = {
        "package_name": "mucaro-ai",
        "version": "0.1.23",
        "archive_path": str(archive_path),
    }

    with pytest.raises(ValueError, match="unexpected archive root"):
        certify_source_artifact.extract_source_archive(manifest, tmp_path / "extract")


def test_python_distribution_summarizes_wheel_artifact(tmp_path):
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    with certify_python_distribution.zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("backend/app/__init__.py", "")
        archive.writestr("mucaro_ai-0.1.23.dist-info/METADATA", "Name: mucaro-ai\n")

    summary = certify_python_distribution.dist_summary(wheel)

    assert summary["path"] == str(wheel)
    assert summary["size_bytes"] == wheel.stat().st_size
    assert summary["member_count"] == 2
    assert isinstance(summary["sha256"], str)
    assert len(summary["sha256"]) == 64


def test_python_distribution_verifies_wheel_metadata(tmp_path):
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    with certify_python_distribution.zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("backend/app/cli.py", "")
        archive.writestr(
            "mucaro_ai-0.1.23.dist-info/METADATA",
            "Name: mucaro-ai\nVersion: 0.1.23\nRequires-Python: >=3.11\n",
        )
        archive.writestr("mucaro_ai-0.1.23.dist-info/entry_points.txt", "[console_scripts]\nmucaro = backend.app.cli:main\n")

    result = certify_python_distribution.verify_wheel_metadata(
        wheel,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "PASS"
    assert result["errors"] == []
    assert result["metadata"]["mucaro_entrypoint"] == "backend.app.cli:main"


def test_python_distribution_rejects_bad_wheel_entrypoint(tmp_path):
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    with certify_python_distribution.zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("backend/app/cli.py", "")
        archive.writestr(
            "mucaro_ai-0.1.23.dist-info/METADATA",
            "Name: mucaro-ai\nVersion: 0.1.23\nRequires-Python: >=3.11\n",
        )
        archive.writestr("mucaro_ai-0.1.23.dist-info/entry_points.txt", "[console_scripts]\nmucaro = wrong.module:main\n")

    result = certify_python_distribution.verify_wheel_metadata(
        wheel,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "FAIL"
    assert result["errors"] == ["mucaro_entrypoint=wrong.module:main expected=backend.app.cli:main"]


def test_python_distribution_verifies_sdist_members(tmp_path):
    sdist = tmp_path / "mucaro_ai-0.1.23.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name, text in {
            "mucaro_ai-0.1.23/PKG-INFO": "Name: mucaro-ai\nVersion: 0.1.23\n",
            "mucaro_ai-0.1.23/pyproject.toml": "",
            "mucaro_ai-0.1.23/README.md": "# Mucaro AI\n",
            "mucaro_ai-0.1.23/backend/app/cli.py": "",
            "mucaro_ai-0.1.23/backend/app/packaging.py": "",
        }.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    result = certify_python_distribution.verify_sdist_members(
        sdist,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "PASS"
    assert result["missing_members"] == []


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
