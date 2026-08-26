from __future__ import annotations

import subprocess
import sys

import pytest

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
