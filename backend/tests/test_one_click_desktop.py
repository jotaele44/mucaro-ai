"""Mucaro AI.app desktop mode (scripts/one_click_desktop.py), with pywebview replaced by a fake window."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import scripts.one_click as one_click
import scripts.one_click_desktop as desktop
from backend.app import desktop_shell

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def no_dialogs_or_signal_handlers(monkeypatch):
    monkeypatch.setenv("MUCARO_APP_DIALOGS", "0")
    # The real helper takes over SIGTERM/SIGINT for the Cocoa run loop; not in the test process.
    monkeypatch.setattr(desktop_shell, "_graceful_sigterm", lambda destroy: None)
    one_click.CANCELLED.clear()
    yield
    one_click.CANCELLED.clear()


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in self.handlers:
            handler()


class FakeWindow:
    def __init__(self, title: str = "Mucaro AI", html: str = "", **options) -> None:
        self.title = title
        self.html = html
        self.options = options
        self.events = types.SimpleNamespace(shown=FakeEvent(), closing=FakeEvent())
        self.exposed = {}
        self.scripts = []
        self.urls = []
        self.destroyed = False

    def expose(self, *functions) -> None:
        for function in functions:
            self.exposed[function.__name__] = function

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)

    def load_url(self, url: str) -> None:
        self.urls.append(url)

    def destroy(self) -> None:
        self.destroyed = True

    def calls(self, function: str) -> list[dict]:
        prefix = f"window.{function} && window.{function}("
        return [json.loads(script[len(prefix) : -1]) for script in self.scripts if script.startswith(prefix)]


class FakeWebview:
    """create_window/start like pywebview: start() shows the window, runs the worker, then closes."""

    def __init__(self, while_open=None) -> None:
        self.windows = []
        self.start_options = {}
        self.while_open = while_open

    def create_window(self, title, html=None, **options):
        window = FakeWindow(title, html, **options)
        self.windows.append(window)
        return window

    def start(self, func=None, **options) -> None:
        self.start_options = options
        window = self.windows[-1]
        window.events.shown.fire()
        if func is not None:
            func()
        if self.while_open is not None:
            self.while_open(window)
        window.events.closing.fire()


class FakeSetup:
    def __init__(self, ran=(), error: Exception | None = None) -> None:
        self.ran = list(ran)
        self.error = error
        self.on_step = None
        self.uv = Path("/private/uv")

    def check_stage0(self) -> None:
        pass

    def run(self) -> list[str]:
        for number in range(1, one_click.TOTAL_STEPS):
            self.on_step(number, f"step {number}")
        if self.error is not None:
            raise self.error
        return self.ran


class FakeService:
    started: list[FakeService] = []

    def __init__(self, log_path: Path, on_gui_ready=None) -> None:
        self.log_path = log_path
        self.on_gui_ready = on_gui_ready
        self.port = 43123
        self.security = {"status": "SECURITY_OK", "deployment_profile": "production"}
        self.stopped = False

    def start(self) -> None:
        FakeService.started.append(self)

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def fake_service(monkeypatch):
    FakeService.started = []
    prepared = []
    monkeypatch.setattr(desktop_shell, "LocalService", FakeService)
    monkeypatch.setattr(desktop_shell, "prepare_environment", lambda data_dir, token: prepared.append((data_dir, token)))
    monkeypatch.setattr(desktop, "apply_env_file", lambda: None)
    return prepared


def _app(tmp_path: Path, setup: FakeSetup, log_path: Path | None = None) -> desktop.DesktopApp:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    log_path = log_path or tmp_path / "setup.log"
    return desktop.DesktopApp(setup, one_click.SetupLog(log_path), log_path=log_path, data_dir=data_dir, notice=desktop.SetupNotice(None))


# --- the window ---------------------------------------------------------------------------------


def test_window_shows_progress_then_loads_the_app_with_a_launch_token(tmp_path, fake_service):
    app = _app(tmp_path, FakeSetup(ran=["python_deps"]))
    seen_while_open = {}
    web = FakeWebview(while_open=lambda window: seen_while_open.update(json.loads((tmp_path / "data" / "instance.json").read_text(encoding="utf-8"))))

    assert app.open(web) == 0

    window = web.windows[0]
    assert window.title == "Mucaro AI"
    assert window.html == desktop.WINDOW_PAGE.read_text(encoding="utf-8")
    assert window.options["min_size"] == (420, 560)
    assert sorted(window.exposed) == ["mucaro_quit", "mucaro_retry", "mucaro_show_log"]
    assert [call["step"] for call in window.calls("mucaroProgress")] == [1, 1, 2, 3, 4, 5, one_click.TOTAL_STEPS]
    assert window.calls("mucaroError") == []

    ((data_dir, token),) = fake_service
    assert data_dir == tmp_path / "data"
    assert len(token) >= 32
    assert window.urls == [f"http://127.0.0.1:43123/#token={token}"]
    assert seen_while_open["token"] == token and seen_while_open["port"] == 43123
    assert seen_while_open["launcher"] == "MUCARO_AI_APP_CHECKOUT"
    assert seen_while_open["window"]["status"] == "WINDOW_SHOWN"
    assert seen_while_open["security"]["status"] == "SECURITY_OK"

    # The Dock shows the owl; the web view keeps its storage next to the library.
    assert web.start_options["icon"] == str(REPO / "Mucaro AI.app" / "Contents" / "Resources" / "AppIcon.icns")
    assert web.start_options["private_mode"] is False
    assert web.start_options["storage_path"] == str(tmp_path / "data" / "webview")

    # Closing the window releases instance.json (the service itself stops in run()).
    assert not (tmp_path / "data" / "instance.json").exists()
    log = (tmp_path / "setup.log").read_text(encoding="utf-8")
    assert "ONE_CLICK_SETUP_OK steps=python_deps" in log
    assert "DESKTOP_APP_READY port=43123" in log
    assert token not in log


def test_gui_connection_is_recorded_like_the_packaged_app(tmp_path, fake_service, monkeypatch):
    app = _app(tmp_path, FakeSetup())
    web = FakeWebview(while_open=lambda window: FakeService.started[0].on_gui_ready({"first_path": "/conversations", "webkit": True}))
    captured = {}
    original = desktop_shell.update_instance_file

    def spy(path, **fields):
        captured.update(fields)
        original(path, **fields)

    monkeypatch.setattr(desktop_shell, "update_instance_file", spy)

    assert app.open(web) == 0

    assert captured["ui"]["status"] == "GUI_CONNECTED"
    assert captured["ui"]["first_path"] == "/conversations"
    assert "ONE_CLICK_FAST_PATH" in (tmp_path / "setup.log").read_text(encoding="utf-8")


def test_setup_failure_shows_the_error_screen_with_a_hint_and_the_log(tmp_path, fake_service):
    error = one_click.SetupError("ERROR_NPM_CI_FAILED", "returncode=1 after 3 attempt(s); see the output above")
    app = _app(tmp_path, FakeSetup(error=error))
    web = FakeWebview()

    assert app.open(web) == 2

    (shown,) = web.windows[0].calls("mucaroError")
    assert shown == {
        "code": "ERROR_NPM_CI_FAILED",
        "hint": desktop.RETRY_HINT,
        "detail": "returncode=1 after 3 attempt(s); see the output above",
        "log": str(tmp_path / "setup.log"),
    }
    assert FakeService.started == []
    assert web.windows[0].urls == []
    assert (tmp_path / "data" / "logs" / "startup-error.log").read_text(encoding="utf-8").startswith("ERROR_NPM_CI_FAILED ")
    assert "ERROR_NPM_CI_FAILED returncode=1" in (tmp_path / "setup.log").read_text(encoding="utf-8")


def test_unexpected_failure_reaches_the_window_instead_of_vanishing_in_a_thread(tmp_path, fake_service):
    app = _app(tmp_path, FakeSetup(error=RuntimeError("disk vanished")))
    web = FakeWebview()

    assert app.open(web) == 2

    (shown,) = web.windows[0].calls("mucaroError")
    assert shown["code"] == "ERROR_SETUP_UNEXPECTED"
    assert shown["detail"] == "RuntimeError: disk vanished"
    assert shown["hint"] == desktop.ERROR_HINTS["ERROR_SETUP_UNEXPECTED"]
    assert FakeService.started == []
    assert "Traceback" in (tmp_path / "setup.log").read_text(encoding="utf-8")


def test_service_failure_is_named_as_such_after_a_good_setup(tmp_path, fake_service, monkeypatch):
    def refuse(self):
        raise RuntimeError("local service did not become healthy")

    monkeypatch.setattr(FakeService, "start", refuse)
    app = _app(tmp_path, FakeSetup())
    web = FakeWebview()

    assert app.open(web) == 2

    window = web.windows[0]
    (shown,) = window.calls("mucaroError")
    assert shown["code"] == "ERROR_DESKTOP_START_FAILED"
    assert shown["detail"] == "RuntimeError: local service did not become healthy"
    assert shown["hint"] == desktop.ERROR_HINTS["ERROR_DESKTOP_START_FAILED"]
    assert [call["step"] for call in window.calls("mucaroProgress")][-1] == one_click.TOTAL_STEPS
    assert window.urls == []


def test_error_screen_buttons_do_nothing_until_the_error_screen_shows(tmp_path, monkeypatch):
    app = _app(tmp_path, FakeSetup())
    app.window = FakeWindow()
    ran = []
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: ran.append(args[0]))
    monkeypatch.setattr(desktop.os, "execve", lambda *args: ran.append(("execve", *args)))
    retry, show_log, quit_app = app.actions()

    retry()
    show_log()
    quit_app()
    assert ran == [] and not app.window.destroyed

    app.error_showing = True
    show_log()
    quit_app()
    retry()
    assert ran[0] == ["/usr/bin/open", "-R", str(tmp_path / "setup.log")]
    assert app.window.destroyed
    execve = ran[1]
    assert execve[0:2] == ("execve", "/bin/bash")
    assert execve[2] == ["/bin/bash", str(REPO / "scripts" / "one_click.sh"), "--desktop"]
    assert execve[3] == app.initial_env


def test_retry_restarts_without_this_launch_token(tmp_path, monkeypatch, fake_service):
    monkeypatch.delenv("MUCARO_AI_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(desktop_shell, "prepare_environment", lambda data_dir, token: monkeypatch.setenv("MUCARO_AI_OPERATOR_TOKEN", token))
    app = _app(tmp_path, FakeSetup())
    app.window = FakeWindow()

    app.start_service()

    assert len(os.environ["MUCARO_AI_OPERATOR_TOKEN"]) >= 32
    assert "MUCARO_AI_OPERATOR_TOKEN" not in app.initial_env
    app.stop_service()


def test_closing_the_window_during_setup_stops_the_running_command(tmp_path, monkeypatch):
    released = []
    monkeypatch.setattr(one_click, "release_lock", lambda: released.append(True))
    stopped = []
    monkeypatch.setattr(one_click, "terminate_active", lambda timeout=5.0: stopped.append(timeout))
    app = _app(tmp_path, FakeSetup())
    app.window = FakeWindow()
    instance = desktop_shell.write_instance_file(tmp_path / "data", 43123, "t" * 43)
    app.instance = instance

    app.on_closing()

    assert app.closing.is_set()
    assert stopped == [2.0]
    assert released == [True]
    assert not instance.exists()
    # Nothing is sent to a window that is closing.
    app.progress(3)
    assert app.window.scripts == []


def test_start_service_is_skipped_when_the_window_closed_during_setup(tmp_path, fake_service):
    app = _app(tmp_path, FakeSetup())
    app.window = FakeWindow()
    app.closing.set()

    app.start_service()

    assert FakeService.started == [] and fake_service == []


# --- helpers ------------------------------------------------------------------------------------


def test_window_library_is_installed_from_the_pyproject_pin_only_when_missing(monkeypatch):
    commands = []
    monkeypatch.setattr(desktop.one_click, "run_with_retries", lambda label, cmd, **kwargs: commands.append((label, cmd, kwargs)))
    installed = iter([False, True])
    monkeypatch.setattr(desktop, "window_library_installed", lambda: next(installed))
    log = one_click.SetupLog(None)
    pins = {"PY_EXCLUDE_NEWER": "2026-09-25T00:00:00Z"}

    desktop.ensure_window_library(log, Path("/private/uv"), pins)

    ((label, cmd, kwargs),) = commands
    assert label == "desktop_window"
    assert cmd[:5] == ["/private/uv", "pip", "install", "--python", sys.executable]
    assert cmd[5:7] == ["--exclude-newer", "2026-09-25T00:00:00Z"]
    assert cmd[7:] == desktop.desktop_requirements()
    assert kwargs["attempts"] == 3

    commands.clear()
    monkeypatch.setattr(desktop, "window_library_installed", lambda: True)
    desktop.ensure_window_library(log, Path("/private/uv"), pins)
    assert commands == []

    monkeypatch.setattr(desktop, "window_library_installed", lambda: False)
    with pytest.raises(one_click.SetupError) as exc:
        desktop.ensure_window_library(log, Path("/private/uv"), pins)
    assert exc.value.code == "ERROR_DESKTOP_WINDOW_MISSING"


def test_window_library_pin_is_the_desktop_extra():
    (requirement,) = desktop.desktop_requirements()
    assert re.fullmatch(r"pywebview==\d+\.\d+\.\d+", requirement)


def test_library_is_this_folders_data_unless_configured(tmp_path):
    env_file = tmp_path / ".env"
    assert desktop.data_directory({}, env_file) == desktop.ROOT / "data"
    env_file.write_text("MUCARO_AI_DATA_DIR=\n", encoding="utf-8")
    assert desktop.data_directory({}, env_file) == desktop.ROOT / "data"
    env_file.write_text("MUCARO_AI_DATA_DIR=/srv/library\n", encoding="utf-8")
    assert desktop.data_directory({}, env_file) == Path("/srv/library")
    assert desktop.data_directory({"MUCARO_AI_DATA_DIR": "/elsewhere"}, env_file) == Path("/elsewhere")
    with pytest.raises(one_click.SetupError) as exc:
        desktop.data_directory({"MUCARO_AI_DATA_DIR": "relative/library"}, env_file)
    assert exc.value.code == "ERROR_DATA_DIR_INVALID"


def test_env_file_fills_gaps_without_overriding_or_touching_python_settings(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MUCARO_AI_LIVE_MODE=1\nMUCARO_AI_MODEL=from-file\nPYTHONPATH=/elsewhere\nMUCARO_AI_DATA_DIR=\n", encoding="utf-8")
    environ = {"MUCARO_AI_MODEL": "from-environment"}

    desktop.apply_env_file(env_file, environ)

    assert environ == {"MUCARO_AI_MODEL": "from-environment", "MUCARO_AI_LIVE_MODE": "1"}


def test_notice_is_closed_once_and_only_when_it_is_our_child(monkeypatch):
    signalled = []
    monkeypatch.setattr(desktop.os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(desktop.os, "waitpid", lambda pid, options: (0, 0))
    monkeypatch.setattr(desktop, "_is_child", lambda pid: pid == 111)

    desktop.SetupNotice("222").dismiss()
    notice = desktop.SetupNotice("111")
    notice.dismiss()
    notice.dismiss()
    desktop.SetupNotice(None).dismiss()
    desktop.SetupNotice("not-a-pid").dismiss()

    assert signalled == [111]


@pytest.mark.skipif(not Path("/bin/ps").exists(), reason="/bin/ps not available")
def test_is_child_tells_our_children_from_other_processes():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert desktop._is_child(child.pid)
        assert not desktop._is_child(1)
    finally:
        child.kill()
        child.wait()


def test_alerts_are_silent_when_dialogs_are_off(monkeypatch):
    monkeypatch.setattr(desktop.subprocess, "run", lambda *args, **kwargs: pytest.fail("no dialog expected"))
    desktop.alert("anything")


def test_window_page_is_self_contained_and_drives_the_error_buttons():
    html = desktop.WINDOW_PAGE.read_text(encoding="utf-8")
    assert "window.mucaroProgress" in html and "window.mucaroError" in html
    for name in ("mucaro_retry", "mucaro_show_log", "mucaro_quit"):
        assert f'call("{name}")' in html
    # No network and no external files; no CSP either, because pywebview's evaluate_js uses eval.
    assert "http://" not in html and "https://" not in html
    assert not re.search(r"<(?:script|link|img)[^>]+(?:src|href)=", html)
    assert "http-equiv" not in html.lower()
    # Error text is placed with textContent, never parsed as HTML.
    assert "innerHTML" not in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_window_page_script_is_valid_javascript(tmp_path):
    html = desktop.WINDOW_PAGE.read_text(encoding="utf-8")
    (script,) = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    path = tmp_path / "page.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), "--check", str(path)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


# --- run() --------------------------------------------------------------------------------------


@pytest.fixture
def stage1(tmp_path, monkeypatch):
    """run() with the setup, window library and pywebview replaced; returns what it produced."""
    data_dir = tmp_path / "library"
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MUCARO_SETUP_NOTICE_PID", raising=False)
    monkeypatch.setattr(desktop, "ensure_window_library", lambda log, uv, pins: None)
    monkeypatch.setattr(desktop.one_click, "OneClickSetup", lambda log, runtime, pins: FakeSetup())
    alerts = []
    monkeypatch.setattr(desktop, "alert", alerts.append)
    web = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", web)
    log_path = tmp_path / "logs" / "setup-20260928-120000.log"
    return types.SimpleNamespace(data_dir=data_dir, alerts=alerts, web=web, log_path=log_path)


def test_run_opens_the_window_on_the_configured_library_and_cleans_up(stage1, fake_service, tmp_path):
    code = desktop.run(one_click.SetupLog(stage1.log_path), log_path=stage1.log_path, runtime=tmp_path, pins={})

    assert code == 0
    assert stage1.alerts == []
    assert fake_service[0][0] == stage1.data_dir
    assert FakeService.started[0].stopped
    assert (stage1.data_dir / "logs").is_dir()
    # The instance lock was released: another launch can take it.
    lock = desktop_shell.InstanceLock(stage1.data_dir)
    lock.acquire()
    lock.release()


def test_run_reports_a_second_window_on_the_same_library(stage1, fake_service, tmp_path):
    stage1.data_dir.mkdir(parents=True)
    held = desktop_shell.InstanceLock(stage1.data_dir)
    held.acquire()
    try:
        code = desktop.run(one_click.SetupLog(stage1.log_path), log_path=stage1.log_path, runtime=tmp_path, pins={})
    finally:
        held.release()

    assert code == desktop_shell.EXIT_ALREADY_RUNNING
    assert stage1.alerts == ["Mucaro AI is already open. Switch to it from the Dock."]
    assert stage1.web.windows == []


def test_run_reports_a_window_library_failure_in_a_dialog(stage1, monkeypatch, tmp_path):
    def fail(log, uv, pins):
        raise one_click.SetupError("ERROR_DESKTOP_WINDOW_FAILED", "returncode=1 after 3 attempt(s); see the output above")

    monkeypatch.setattr(desktop, "ensure_window_library", fail)

    code = desktop.run(one_click.SetupLog(stage1.log_path), log_path=stage1.log_path, runtime=tmp_path, pins={})

    assert code == 2
    (message,) = stage1.alerts
    assert "ERROR_DESKTOP_WINDOW_FAILED returncode=1" in message
    assert str(stage1.log_path) in message
    assert stage1.web.windows == []


# --- the real service, in a child process (it changes the process environment) -----------------

SERVICE_PROBE = r"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

root, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
import scripts.one_click as one_click
import scripts.one_click_desktop as desktop


class Window:
    def __init__(self):
        self.urls = []

    def load_url(self, url):
        self.urls.append(url)


def get(url, token=None, gui=False):
    headers = {"X-Mucaro-Token": token} if token else {}
    if gui:
        headers["X-Mucaro-Client"] = "mucaro-gui"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


desktop.apply_env_file = lambda: None
app = desktop.DesktopApp(None, one_click.SetupLog(None), log_path=data_dir / "setup.log", data_dir=data_dir, notice=desktop.SetupNotice(None))
app.window = Window()
app.on_shown()
app.start_service()
url = app.window.urls[0]
token = url.split("#token=", 1)[1]
base = url.split("/#", 1)[0]
result = {
    "url_prefix": url.split("#token=", 1)[0],
    "token_length": len(token),
    "health": get(base + "/health"),
    "private_without_token": get(base + "/conversations", gui=True),
    "private_with_token": get(base + "/conversations", token=token, gui=True),
}
instance = json.loads((data_dir / "instance.json").read_text(encoding="utf-8"))
result["instance_token_matches"] = instance.pop("token") == token
result["instance"] = instance
app.stop_service()
result["instance_removed"] = not (data_dir / "instance.json").exists()
print(json.dumps(result))
"""


def test_service_opens_the_library_with_a_launch_token_under_the_production_profile(tmp_path):
    data_dir = tmp_path / "library"
    data_dir.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
    env["PYTHONPATH"] = str(REPO)
    env["NO_PROXY"] = "127.0.0.1,localhost"

    proc = subprocess.run(
        [sys.executable, "-c", SERVICE_PROBE, str(REPO), str(data_dir)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr[-4000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    instance = result["instance"]
    assert result["url_prefix"] == f"http://127.0.0.1:{instance['port']}/"
    assert result["token_length"] >= 32 and result["instance_token_matches"]
    assert result["health"] == 200
    assert result["private_without_token"] == 401
    assert result["private_with_token"] == 200
    assert instance["launcher"] == "MUCARO_AI_APP_CHECKOUT"
    assert instance["window"]["status"] == "WINDOW_SHOWN"
    assert instance["ui"]["status"] == "GUI_CONNECTED"
    assert instance["security"]["status"] == "SECURITY_OK"
    assert instance["security"]["deployment_profile"] == "production"
    assert result["instance_removed"]
    assert (data_dir / "logs" / "service.log").exists()
