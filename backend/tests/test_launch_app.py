from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

import scripts.certify_fresh_clone as certify_fresh_clone
import scripts.certify_python_distribution as certify_python_distribution
import scripts.certify_release as certify_release
import scripts.certify_source_artifact as certify_source_artifact
import scripts.launch_app as launch_app
import scripts.release_check as release_check
from backend.app.packaging import build_macos_app_bundle, verify_macos_app_bundle


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


def test_build_macos_app_bundle_creates_double_clickable_wrapper(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "scripts" / "launch_app.py").write_text("# launcher\n", encoding="utf-8")
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<div>Mucaro</div>\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "mucaro-ai"\nversion = "0.1.23"\n', encoding="utf-8")

    result = build_macos_app_bundle(tmp_path, output_dir=tmp_path / "apps", python_executable=sys.executable)

    assert result["status"] == "MACOS_APP_BUNDLE_READY"
    app_path = tmp_path / "apps" / "Mucaro AI.app"
    executable = app_path / "Contents" / "MacOS" / "mucaro-ai"
    receipt = app_path / "Contents" / "Resources" / "mucaro-app-receipt.json"
    assert app_path.is_dir()
    assert executable.exists()
    assert executable.stat().st_mode & 0o111
    assert "scripts/launch_app.py" in executable.read_text(encoding="utf-8")
    assert '"$@"' in executable.read_text(encoding="utf-8")
    assert json.loads(receipt.read_text(encoding="utf-8"))["scope"] == "source_bound_local_macos_app_wrapper"

    verification = verify_macos_app_bundle(app_path, root=tmp_path)
    assert verification["status"] == "MACOS_APP_BUNDLE_VERIFIED"


def test_verify_macos_app_bundle_rejects_missing_bundle(tmp_path):
    result = verify_macos_app_bundle(tmp_path / "Missing.app", root=tmp_path)

    assert result["status"] == "MACOS_APP_BUNDLE_VERIFY_FAILED"
    failed_names = {check["name"] for check in result["checks"] if check["status"] == "FAIL"}
    assert "app_bundle_exists" in failed_names
    assert "launcher_executable" in failed_names


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


def test_certify_release_npm_audit_uses_working_node_fallback(monkeypatch):
    def fake_gate_command(name, cmd, *, cwd=certify_release.ROOT):
        del cmd, cwd
        return certify_release.Gate(name, "FAIL", "broken system npm")

    def fake_run(cmd, *, cwd=certify_release.ROOT, check=True):
        del cwd, check
        assert cmd == ["/tmp/node", "/tmp/npm-cli.js", "audit", "--audit-level=moderate"]
        return subprocess.CompletedProcess(cmd, 0, "found 0 vulnerabilities\n")

    monkeypatch.setattr(certify_release, "gate_command", fake_gate_command)
    monkeypatch.setattr(certify_release, "_working_node", lambda: "/tmp/node")
    monkeypatch.setattr(certify_release, "_npm_cli_candidates", lambda: [Path("/tmp/npm-cli.js")])
    monkeypatch.setattr(certify_release, "run", fake_run)

    result = certify_release.gate_npm_audit()

    assert result.status == "PASS"
    assert "fallback_node=/tmp/node" in result.detail


def test_certify_release_frontend_build_uses_working_node_fallback(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("// vite\n", encoding="utf-8")

    def fake_gate_command(name, cmd, *, cwd=certify_release.ROOT):
        del cmd, cwd
        return certify_release.Gate(name, "FAIL", "broken system node")

    def fake_run(cmd, *, cwd=certify_release.ROOT, check=True):
        del cwd, check
        assert cmd == ["/tmp/node", str(vite), "build"]
        return subprocess.CompletedProcess(cmd, 0, "built\n")

    monkeypatch.setattr(certify_release, "ROOT", tmp_path)
    monkeypatch.setattr(certify_release, "gate_command", fake_gate_command)
    monkeypatch.setattr(certify_release, "_working_node", lambda: "/tmp/node")
    monkeypatch.setattr(certify_release, "run", fake_run)

    result = certify_release.gate_frontend_build()

    assert result.status == "PASS"
    assert "fallback_node=/tmp/node" in result.detail
    assert "node_modules/vite/bin/vite.js" in result.detail


def test_release_check_frontend_build_uses_working_node_fallback(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    vite = frontend / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True)
    vite.write_text("// vite\n", encoding="utf-8")
    calls = []

    def fake_run(cmd, *, env=None, cwd=release_check.ROOT):
        calls.append((cmd, env, cwd))
        if cmd == ["npm", "run", "build"]:
            raise subprocess.CalledProcessError(-6, cmd)

    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "run", fake_run)
    monkeypatch.setattr(release_check.release_certification, "_working_node", lambda: "/tmp/node")

    release_check.run_frontend_build({"PATH": "/usr/bin"})

    primary, fallback = calls
    assert primary == (["npm", "run", "build"], {"PATH": "/usr/bin"}, frontend)
    assert fallback[0] == ["/tmp/node", str(vite), "build"]
    assert fallback[1]["PATH"].split(release_check.os.pathsep)[0] == "/tmp"
    assert fallback[1]["NODE"] == "/tmp/node"
    assert fallback[1]["npm_config_node"] == "/tmp/node"
    assert fallback[2] == frontend


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


def test_fresh_clone_npm_ci_uses_working_node_fallback(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    calls = []

    def fake_run(cmd, *, cwd, check=True, env=None):
        del check
        calls.append((cmd, cwd, env))
        if cmd == ["npm", "ci", "--registry=https://registry.npmjs.org/"]:
            return subprocess.CompletedProcess(cmd, 134, "dyld missing libsimdutf\n")
        return subprocess.CompletedProcess(cmd, 0, "added packages\n")

    monkeypatch.setattr(certify_fresh_clone, "run", fake_run)
    monkeypatch.setattr(certify_fresh_clone.release_certification, "_working_node", lambda: "/tmp/node")
    monkeypatch.setattr(
        certify_fresh_clone.release_certification,
        "_npm_cli_candidates",
        lambda: [Path("/tmp/npm-cli.js")],
    )

    certify_fresh_clone.run_npm_ci(frontend)

    primary, fallback = calls
    assert primary == (["npm", "ci", "--registry=https://registry.npmjs.org/"], frontend, None)
    assert fallback[0] == ["/tmp/node", "/tmp/npm-cli.js", "ci", "--registry=https://registry.npmjs.org/"]
    assert fallback[1] == frontend
    assert fallback[2]["PATH"].split(certify_fresh_clone.os.pathsep)[0] == "/tmp"
    assert fallback[2]["NODE"] == "/tmp/node"
    assert fallback[2]["npm_config_node"] == "/tmp/node"


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


def test_source_artifact_npm_ci_uses_working_node_fallback(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    calls = []

    def fake_run(cmd, *, cwd, check=True, env=None):
        del check
        calls.append((cmd, cwd, env))
        if cmd == ["npm", "ci", "--registry=https://registry.npmjs.org/"]:
            return subprocess.CompletedProcess(cmd, 134, "dyld missing libsimdutf\n")
        return subprocess.CompletedProcess(cmd, 0, "added packages\n")

    monkeypatch.setattr(certify_source_artifact, "run", fake_run)
    monkeypatch.setattr(certify_source_artifact.release_certification, "_working_node", lambda: "/tmp/node")
    monkeypatch.setattr(
        certify_source_artifact.release_certification,
        "_npm_cli_candidates",
        lambda: [Path("/tmp/npm-cli.js")],
    )

    certify_source_artifact.run_npm_ci(frontend)

    primary, fallback = calls
    assert primary == (["npm", "ci", "--registry=https://registry.npmjs.org/"], frontend, None)
    assert fallback[0] == ["/tmp/node", "/tmp/npm-cli.js", "ci", "--registry=https://registry.npmjs.org/"]
    assert fallback[1] == frontend
    assert fallback[2]["PATH"].split(certify_source_artifact.os.pathsep)[0] == "/tmp"
    assert fallback[2]["NODE"] == "/tmp/node"
    assert fallback[2]["npm_config_node"] == "/tmp/node"


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
        for path in certify_python_distribution.GOVERNED_GRID_ASSETS:
            archive.writestr(path, (certify_python_distribution.ROOT / path).read_bytes())

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
        for path in certify_python_distribution.GOVERNED_GRID_ASSETS:
            archive.writestr(path, (certify_python_distribution.ROOT / path).read_bytes())

    result = certify_python_distribution.verify_wheel_metadata(
        wheel,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "FAIL"
    assert result["errors"] == ["mucaro_entrypoint=wrong.module:main expected=backend.app.cli:main"]


def test_python_distribution_rejects_missing_governed_grid_asset(tmp_path):
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    with certify_python_distribution.zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("backend/app/cli.py", "")
        archive.writestr(
            "mucaro_ai-0.1.23.dist-info/METADATA",
            "Name: mucaro-ai\nVersion: 0.1.23\nRequires-Python: >=3.11\n",
        )
        archive.writestr("mucaro_ai-0.1.23.dist-info/entry_points.txt", "[console_scripts]\nmucaro = backend.app.cli:main\n")
        for path in certify_python_distribution.GOVERNED_GRID_ASSETS:
            if path.endswith("us_noaa_pvhpgn.tif"):
                continue
            archive.writestr(path, (certify_python_distribution.ROOT / path).read_bytes())

    result = certify_python_distribution.verify_wheel_metadata(
        wheel,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "FAIL"
    assert result["errors"] == ["backend/app/gis_grids/us_noaa_pvhpgn.tif missing from wheel"]


def test_python_distribution_verifies_sdist_members(tmp_path):
    sdist = tmp_path / "mucaro_ai-0.1.23.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        members = {
            "mucaro_ai-0.1.23/PKG-INFO": b"Name: mucaro-ai\nVersion: 0.1.23\n",
            "mucaro_ai-0.1.23/pyproject.toml": b"",
            "mucaro_ai-0.1.23/README.md": b"# Mucaro AI\n",
            "mucaro_ai-0.1.23/backend/app/cli.py": b"",
            "mucaro_ai-0.1.23/backend/app/packaging.py": b"",
        }
        for path in certify_python_distribution.GOVERNED_GRID_ASSETS:
            members[f"mucaro_ai-0.1.23/{path}"] = (certify_python_distribution.ROOT / path).read_bytes()
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    result = certify_python_distribution.verify_sdist_members(
        sdist,
        {"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert result["status"] == "PASS"
    assert result["missing_members"] == []


def test_python_distribution_install_verification_is_isolated(tmp_path, monkeypatch):
    install_env = tmp_path / "install-env"
    install_python = install_env / "bin" / "python"
    install_mucaro = install_env / "bin" / "mucaro"
    install_mucaro.parent.mkdir(parents=True)
    install_mucaro.write_text("#!/bin/sh\n", encoding="utf-8")
    install_mucaro.chmod(0o755)
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    workdir = tmp_path / "isolated-workdir"
    workdir.mkdir()
    calls = []

    def fake_run(command, *, cwd, check=True):
        calls.append((command, cwd, check))
        output = "ISOLATED_WHEEL_IMPORT_OK" if command[1:2] == ["-c"] else "installed"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr(certify_python_distribution, "run", fake_run)

    result = certify_python_distribution.certify_installed_wheel(
        install_python=install_python,
        install_mucaro=install_mucaro,
        install_env=install_env,
        wheel=wheel,
        workdir=workdir,
        expected={"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
    )

    assert all(cwd == workdir for _, cwd, _ in calls)
    assert "--force-reinstall" in calls[0][0]
    assert "--no-cache-dir" in calls[0][0]
    assert "module_path.relative_to(prefix)" in calls[-1][0][-1]
    assert "grid_path.stat().st_size == 3644" in calls[-1][0][-1]
    assert result["entrypoint"] == "bin/mucaro"
    assert result["module_origin_check"] == "backend.app.cli resolves under isolated sys.prefix"


def test_python_distribution_install_verification_requires_console_script(tmp_path, monkeypatch):
    install_env = tmp_path / "install-env"
    install_python = install_env / "bin" / "python"
    install_mucaro = install_env / "bin" / "mucaro"
    install_mucaro.parent.mkdir(parents=True)
    (install_mucaro.parent / "python").write_text("", encoding="utf-8")
    wheel = tmp_path / "mucaro_ai-0.1.23-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    monkeypatch.setattr(
        certify_python_distribution,
        "run",
        lambda command, *, cwd, check=True: subprocess.CompletedProcess(command, 0, stdout="pip install output"),
    )

    with pytest.raises(FileNotFoundError) as exc:
        certify_python_distribution.certify_installed_wheel(
            install_python=install_python,
            install_mucaro=install_mucaro,
            install_env=install_env,
            wheel=wheel,
            workdir=tmp_path,
            expected={"name": "mucaro-ai", "version": "0.1.23", "requires_python": ">=3.11"},
        )

    assert "installed console script missing or non-executable" in str(exc.value)
    assert "bin_members=python" in str(exc.value)
    assert "pip_output_tail=pip install output" in str(exc.value)


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


def test_launch_app_build_env_loads_project_env_without_overriding_shell(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "MUCARO_AI_LIVE_MODE=1\nMUCARO_AI_MODEL=from-file\nMUCARO_AI_OPERATOR_TOKEN=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launch_app, "ROOT", tmp_path)
    monkeypatch.setenv("MUCARO_AI_MODEL", "from-shell")
    monkeypatch.delenv("MUCARO_AI_LIVE_MODE", raising=False)
    monkeypatch.delenv("MUCARO_AI_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)

    env = launch_app.build_env()

    assert env["MUCARO_AI_LIVE_MODE"] == "1"
    assert env["MUCARO_AI_MODEL"] == "from-shell"
    assert "MUCARO_AI_OPERATOR_TOKEN" not in env
    assert env["PYTHONPATH"].split(launch_app.os.pathsep) == [str(tmp_path), str(tmp_path / "backend")]


def test_release_check_pythonpath_uses_platform_separator(tmp_path, monkeypatch):
    calls = []

    class StopAfterFirstRun(Exception):
        pass

    def fake_run(cmd, *, env=None, cwd=release_check.ROOT):
        calls.append(env)
        raise StopAfterFirstRun

    monkeypatch.setattr(release_check, "ROOT", tmp_path)
    monkeypatch.setattr(release_check, "run", fake_run)
    # Simulate Windows, where ":" would corrupt drive-letter paths.
    monkeypatch.setattr(release_check.os, "pathsep", ";")

    with pytest.raises(StopAfterFirstRun):
        release_check.main()

    assert calls[0]["PYTHONPATH"] == f"{tmp_path};{tmp_path / 'backend'}"
