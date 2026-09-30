from __future__ import annotations

import os
import io
import json
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import launch_app, native_window

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("code", [0, 23])
@pytest.mark.skipif(os.name == "nt", reason="macOS shell entry point")
def test_finder_setup_is_silent_and_forces_native_window(tmp_path, code):
    root = tmp_path / "folder with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/one_click.sh").write_text(
        '#!/bin/bash\nprintf "ARGS=%s SILENT=%s\\n" "$*" "$MUCARO_SILENT_LAUNCH"\n'
        f'echo "setup error" >&2\nexit {code}\n', encoding="utf-8",
    )
    proc = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/native_start.sh"), str(root), "--setup-only"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == code
    assert proc.stdout == proc.stderr == ""
    logs = list((root / ".mucaro-runtime/logs").glob("launch-*.log"))
    assert len(logs) == 1
    assert "ARGS=--native --setup-only SILENT=1" in logs[0].read_text()
    assert "setup error" in logs[0].read_text()


@pytest.mark.skipif(os.name == "nt", reason="macOS shell entry point")
def test_source_app_is_bound_to_silent_launcher_and_can_move_with_checkout(tmp_path):
    root = tmp_path / "moved checkout"
    app = root / "Mucaro AI Setup.app"
    shutil.copytree(ROOT / "Mucaro AI Setup.app", app)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleIdentifier"] == "ai.mucaro.source-launcher"
    assert info["CFBundleName"] == "Mucaro AI Setup"
    assert info["CFBundleDisplayName"] == "Mucaro AI Setup"
    exe = app / "Contents/MacOS" / info["CFBundleExecutable"]
    assert os.access(exe, os.X_OK)
    (root / "scripts").mkdir()
    result = root / "result.txt"
    (root / "scripts/native_start.sh").write_text('printf "%s" "$1" > "$1/result.txt"\n')
    proc = subprocess.run(["/bin/bash", str(exe)], capture_output=True, check=False)
    assert proc.returncode == 0
    assert result.read_text() == str(root)


@pytest.fixture
def native_backend(monkeypatch):
    stopped = []
    proc = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda **kwargs: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: False)
    monkeypatch.setattr(launch_app, "start_backend", lambda port: proc)
    monkeypatch.setattr(launch_app, "wait_for_health", lambda *args: True)
    monkeypatch.setattr(launch_app, "stop_backend", lambda *args: stopped.append(args) or True)
    monkeypatch.setattr(launch_app.webbrowser, "open", lambda *args: pytest.fail("opened browser"))
    return stopped


def test_native_close_stops_owned_backend(native_backend, monkeypatch):
    opened = []
    monkeypatch.setattr(native_window, "run_window", lambda url, **kwargs: opened.append(url))
    assert launch_app.main(["--native"]) == 0
    assert opened == ["http://127.0.0.1:8000"]
    assert len(native_backend) == 1


@pytest.mark.parametrize("error", [ImportError("missing webview"), RuntimeError("Cocoa failed")])
def test_native_failure_reports_error_without_browser_fallback(native_backend, monkeypatch, error):
    dialogs = []
    monkeypatch.delenv("MUCARO_SILENT_LAUNCH", raising=False)
    monkeypatch.setattr(native_window, "show_dialog", dialogs.append)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(native_window, "run_window", fail)
    assert launch_app.main(["--native"]) == 10
    assert len(native_backend) == 1
    assert len(dialogs) == 1


def test_wrapper_owns_failure_dialog_so_python_does_not_duplicate_it(native_backend, monkeypatch):
    monkeypatch.setenv("MUCARO_SILENT_LAUNCH", "1")
    monkeypatch.setattr(native_window, "show_dialog", lambda *args: pytest.fail("duplicate dialog"))
    monkeypatch.setattr(native_window, "run_window", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failure")))
    assert launch_app.main(["--native"]) == 10


def test_native_reuse_preserves_external_backend(native_backend, monkeypatch):
    monkeypatch.setattr(launch_app, "port_open", lambda port: True)
    monkeypatch.setattr(launch_app, "health_ok", lambda port: True)
    monkeypatch.setattr(native_window, "activate_existing_window", lambda path: True)
    monkeypatch.setattr(native_window, "run_window", lambda *args, **kwargs: pytest.fail("duplicate window"))
    assert launch_app.main(["--native", "--exit-if-running"]) == 0
    assert native_backend == []


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.skipif(os.name == "nt", reason="POSIX signal lifecycle")
def test_window_cleans_pid_signal_handlers_and_worker(tmp_path, monkeypatch, fail):
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    pid = tmp_path / "window.pid"

    def start(**kwargs):
        assert pid.read_text().strip() == str(os.getpid())
        if fail:
            raise RuntimeError("window failed")

    fake = SimpleNamespace(create_window=lambda *args, **kwargs: SimpleNamespace(destroy=lambda: None), start=start)
    monkeypatch.setitem(sys.modules, "webview", fake)
    if fail:
        with pytest.raises(RuntimeError):
            native_window.run_window("http://127.0.0.1:8000", storage_dir=tmp_path / "webview", pid_file=pid)
    else:
        native_window.run_window("http://127.0.0.1:8000", storage_dir=tmp_path / "webview", pid_file=pid)
    assert not pid.exists()
    assert all(signal.getsignal(sig) == handler for sig, handler in before.items())
    assert not any(t.name == "mucaro-signal" for t in threading.enumerate())


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal lifecycle")
def test_signal_requests_window_close_and_restores_wakeup_fd():
    destroyed = threading.Event()
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    previous = signal.set_wakeup_fd(write_fd)
    try:
        with native_window._stop_on_signal(destroyed.set):
            os.kill(os.getpid(), signal.SIGTERM)
            assert destroyed.wait(1)
        restored = signal.set_wakeup_fd(previous)
        assert restored == write_fd
    finally:
        signal.set_wakeup_fd(previous)
        os.close(read_fd)
        os.close(write_fd)


def test_macos_setup_requests_and_checks_native_dependencies(monkeypatch):
    # Run a fresh module import so platform-specific constants reflect the target Mac.
    import runpy

    monkeypatch.setattr(sys, "platform", "darwin")
    module = runpy.run_path(str(ROOT / "scripts/one_click.py"), run_name="native_dependencies_test")
    assert "desktop" in module["PYTHON_EXTRAS"].split(",")
    assert {"webview", "AppKit", "Foundation", "WebKit"} <= set(module["KEY_MODULES"])


@pytest.mark.parametrize("body,expected", [
    ({"status": "OK", "project_root": str(ROOT)}, True),
    ({"status": "OK"}, False),
    ({"status": "OK", "project_root": "/other/checkout"}, False),
    (["OK"], False),
])
def test_health_reuse_requires_this_checkout(monkeypatch, body, expected):
    response = io.BytesIO(json.dumps(body).encode())
    response.status = 200
    monkeypatch.setattr(launch_app.urllib.request, "urlopen", lambda *args, **kwargs: response)
    assert launch_app.health_ok(8000) is expected
