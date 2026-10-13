"""Windows branches of the native window, exercised with fake Win32 calls on any OS."""

from __future__ import annotations

import ctypes
import runpy
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import launch_app, native_window

ROOT = Path(__file__).resolve().parents[2]
STILL_ACTIVE = 259


class FakeKernel32:
    def __init__(self, *, handle=1, exit_code=STILL_ACTIVE):
        self.handle = handle
        self.exit_code = exit_code
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        return self.handle

    def GetExitCodeProcess(self, handle, ref):
        ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(native_window.sys, "platform", "win32")
    monkeypatch.setattr(native_window.os, "kill", lambda *args: pytest.fail("os.kill terminates processes on Windows"))
    libraries = {}
    monkeypatch.setattr(native_window, "_windll", lambda name: libraries[name])
    return libraries


def test_dialog_uses_a_message_box(windows):
    shown = []
    windows["user32"] = SimpleNamespace(MessageBoxW=lambda owner, text, title, flags: shown.append((text, title)))
    native_window.show_dialog("Mucaro AI could not start (code 2).")
    assert shown == [("Mucaro AI could not start (code 2).", "Mucaro AI")]


@pytest.mark.parametrize("exit_code,alive", [(STILL_ACTIVE, True), (0, False)])
def test_pid_alive_asks_for_the_exit_code(windows, exit_code, alive):
    kernel32 = FakeKernel32(exit_code=exit_code)
    windows["kernel32"] = kernel32
    assert native_window.pid_alive(4242) is alive
    assert kernel32.closed == [1]


@pytest.mark.parametrize("error,alive", [(5, True), (87, False)])
def test_pid_alive_when_the_process_cannot_be_opened(windows, monkeypatch, error, alive):
    windows["kernel32"] = FakeKernel32(handle=0)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert native_window.pid_alive(4242) is alive


def test_existing_window_is_found_by_process_and_brought_forward(windows, tmp_path):
    pid_file = tmp_path / "window.pid"
    pid_file.write_text("4242\n", encoding="utf-8")
    windows["kernel32"] = FakeKernel32()
    owners = {101: 77, 102: 4242}
    calls = []

    def enum_windows(callback, lparam):
        for hwnd in owners:
            if not callback(hwnd, lparam):
                break
        return 1

    def owner_of(hwnd, ref):
        ref._obj.value = owners[hwnd]
        return 1

    windows["user32"] = SimpleNamespace(
        EnumWindows=enum_windows,
        GetWindowThreadProcessId=owner_of,
        IsWindowVisible=lambda hwnd: 1,
        ShowWindow=lambda hwnd, cmd: calls.append(("show", hwnd, cmd)),
        SetForegroundWindow=lambda hwnd: calls.append(("front", hwnd)) or 1,
    )
    assert native_window.activate_existing_window(pid_file) is True
    assert calls == [("show", 102, 9), ("front", 102)]


def test_signal_routing_is_skipped_on_windows(windows, monkeypatch):
    monkeypatch.setattr(native_window.os, "pipe", lambda: pytest.fail("select() cannot wait on pipes on Windows"))
    with native_window._stop_on_signal(lambda: None):
        pass


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def _fake_webview(monkeypatch):
    started = []
    window = SimpleNamespace(events=SimpleNamespace(shown=FakeEvent()), destroy=lambda: None)
    module = types.ModuleType("webview")
    module.create_window = lambda *args, **kwargs: window
    module.start = lambda **kwargs: started.append(kwargs)
    module.settings = {"ALLOW_DOWNLOADS": False}
    monkeypatch.setitem(sys.modules, "webview", module)
    return started


def test_window_uses_webview2_when_it_is_installed(windows, monkeypatch, tmp_path):
    started = _fake_webview(monkeypatch)
    windows["shell32"] = SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=lambda app_id: None)
    monkeypatch.setattr(native_window, "windows_renderer", lambda: "edgechromium")
    native_window.run_window("http://127.0.0.1:8000", storage_dir=tmp_path / "webview", pid_file=tmp_path / "window.pid")
    assert started and started[0]["gui"] == "edgechromium"
    assert sys.modules["webview"].settings["ALLOW_DOWNLOADS"] is True  # WebView2 cancels downloads otherwise
    assert not (tmp_path / "window.pid").exists()


@pytest.mark.parametrize("renderer", ["mshtml", None])
def test_window_never_opens_with_internet_explorer(windows, monkeypatch, tmp_path, renderer):
    # Without WebView2, pywebview silently falls back to MSHTML, which cannot run the interface.
    started = _fake_webview(monkeypatch)
    monkeypatch.setattr(native_window, "windows_renderer", lambda: renderer)
    with pytest.raises(native_window.WebView2Missing):
        native_window.run_window("http://127.0.0.1:8000", storage_dir=tmp_path / "webview", pid_file=tmp_path / "window.pid")
    assert started == []


@pytest.fixture
def native_backend(monkeypatch):
    stopped = []
    monkeypatch.setattr(launch_app, "ensure_frontend_dist", lambda **kwargs: None)
    monkeypatch.setattr(launch_app, "port_open", lambda port: False)
    monkeypatch.setattr(launch_app, "start_backend", lambda port: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(launch_app, "wait_for_health", lambda *args: True)
    monkeypatch.setattr(launch_app, "stop_backend", lambda *args: stopped.append(args) or True)
    monkeypatch.setattr(launch_app.webbrowser, "open", lambda *args: pytest.fail("opened browser"))
    return stopped


def test_missing_webview2_gets_its_own_exit_code_and_message(native_backend, monkeypatch):
    dialogs = []
    monkeypatch.delenv("MUCARO_SILENT_LAUNCH", raising=False)
    monkeypatch.setattr(native_window, "show_dialog", dialogs.append)

    def missing(*args, **kwargs):
        raise native_window.WebView2Missing("not installed")

    monkeypatch.setattr(native_window, "run_window", missing)
    assert launch_app.main(["--native"]) == 11
    assert len(native_backend) == 1
    assert len(dialogs) == 1 and "WebView2" in dialogs[0] and native_window.WEBVIEW2_DOWNLOAD in dialogs[0]


def test_windows_setup_installs_and_checks_the_window_dependencies(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    module = runpy.run_path(str(ROOT / "scripts/one_click.py"), run_name="windows_native_dependencies_test")
    assert "desktop" in module["PYTHON_EXTRAS"].split(",")
    assert {"webview", "clr"} <= set(module["KEY_MODULES"])
    assert "AppKit" not in module["KEY_MODULES"]


def test_linux_setup_does_not_install_the_window(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    module = runpy.run_path(str(ROOT / "scripts/one_click.py"), run_name="linux_native_dependencies_test")
    assert "desktop" not in module["PYTHON_EXTRAS"].split(",")
    assert "webview" not in module["KEY_MODULES"]
