"""Mucaro AI Setup.app mode of the double-click setup (macOS): setup progress, then the app, in one window.

Mucaro AI Setup.app runs ``scripts/one_click.sh --desktop``. That provisions the private runtimes (stage 0)
and starts ``scripts/one_click.py --desktop``, which hands over to :func:`run` here:

1. make sure pywebview (the ``desktop`` extra in pyproject.toml) is installed, so a window can open;
2. open the Mucaro AI window at once, showing setup progress (``scripts/one_click_window.html``);
3. run the remaining setup steps (``OneClickSetup``) in a worker thread;
4. start the local service in this process (``backend.app.desktop_shell.LocalService``: loopback only,
   a fresh token per launch, the ``production`` security profile) on this folder's ``data/`` library,
   and show the app in the same window.

No terminal and no browser are involved. A failure is shown in the window with its error code, a
hint and the setup log, and can be retried. Closing the window stops setup or the app.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib
import importlib.util
import json
import os
import platform
import secrets
import signal
import subprocess
import sys
import threading
import time
import tomllib
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import desktop_shell  # noqa: E402
from backend.app.env_file import read_env_file  # noqa: E402
from scripts import one_click  # noqa: E402

APP_TITLE = desktop_shell.APP_NAME
WINDOW_PAGE = Path(__file__).with_name("one_click_window.html")
# The page's last step: the setup is done and the app itself is loading.
OPENING_STEP = one_click.TOTAL_STEPS
OSASCRIPT = Path("/usr/bin/osascript")

RETRY_HINT = "Check the internet connection, then choose Try again. Setup resumes where it stopped."
# What the window says about each failure; docs/ONE_CLICK_SETUP.md has the full error table.
ERROR_HINTS = {
    "ERROR_PY_DEPS_FAILED": RETRY_HINT,
    "ERROR_NPM_CI_FAILED": RETRY_HINT,
    "ERROR_FRONTEND_DEPS_INCOMPLETE": RETRY_HINT,
    "ERROR_PY_IMPORT_MISSING": "Some Python packages did not install completely. Choose Try again; Show log names them.",
    "ERROR_FRONTEND_BUILD_FAILED": "Building the interface failed. Show log has the reason; please report it.",
    "ERROR_FRONTEND_BUILD_NO_DIST": "Building the interface failed. Show log has the reason; please report it.",
    "ERROR_PREFLIGHT_FAILED": "The readiness check found a problem. Show log lists it on its ERROR lines.",
    "ERROR_SAMPLE_INGEST_FAILED": "Loading the sample documents failed. Show log has the reason.",
    "ERROR_SETUP_UNEXPECTED": "Setup stopped unexpectedly. Choose Try again; if it happens again, Show log has the details.",
    "ERROR_DESKTOP_START_FAILED": "The local service did not start. Show log has the reason.",
}
DEFAULT_HINT = "Choose Try again. If it fails again, Show log names the problem."


def desktop_requirements(pyproject: Path | None = None) -> list[str]:
    """The pinned window library: the `desktop` extra in pyproject.toml, the one place it is pinned."""
    data = tomllib.loads((pyproject or ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(requirement) for requirement in data["project"]["optional-dependencies"]["desktop"]]


def window_library_installed() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("webview") is not None


def ensure_window_library(log: Any, uv: Path, pins: Mapping[str, str]) -> None:
    """Install pywebview ahead of the other packages, so the window can show the rest of the setup.

    Same cutoff as the full install, which later finds it already satisfied."""
    if window_library_installed():
        return
    log.line("==> Installing the Mucaro AI window (one time)")
    one_click.run_with_retries(
        "desktop_window",
        [str(uv), "pip", "install", "--python", sys.executable, "--exclude-newer", pins["PY_EXCLUDE_NEWER"], *desktop_requirements()],
        log=log,
        attempts=3,
        cwd=ROOT,
    )
    if not window_library_installed():
        raise one_click.SetupError("ERROR_DESKTOP_WINDOW_MISSING", "pywebview was installed but cannot be imported")
    log.line("DESKTOP_WINDOW_LIBRARY_OK")


def data_directory(environ: Mapping[str, str] | None = None, env_file: Path | None = None) -> Path:
    """The library this app opens: MUCARO_AI_DATA_DIR (environment first, then .env), else data/ here.

    The same library that Mucaro AI.command and the mucaro CLI use."""
    environ = os.environ if environ is None else environ
    value = (environ.get("MUCARO_AI_DATA_DIR") or read_env_file(env_file or ROOT / ".env").get("MUCARO_AI_DATA_DIR") or "").strip()
    if not value:
        return ROOT / "data"
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise one_click.SetupError("ERROR_DATA_DIR_INVALID", f"MUCARO_AI_DATA_DIR must be an absolute path, not {value!r}")
    return path


def apply_env_file(env_file: Path | None = None, environ: Any = None) -> None:
    """Load .env (live mode, model, API key) for the service; the environment keeps precedence.

    Done only after setup, so setup commands (uv, npm) never see keys from .env. PYTHON* entries are
    skipped, as scripts/launch_app.py does."""
    environ = os.environ if environ is None else environ
    for key, value in read_env_file(env_file or ROOT / ".env").items():
        if not key.startswith("PYTHON"):
            environ.setdefault(key, value)


def app_icon() -> Path:
    bundle = Path(os.environ.get("MUCARO_APP_BUNDLE") or ROOT / "Mucaro AI Setup.app")
    return bundle / "Contents" / "Resources" / "AppIcon.icns"


def alert(message: str) -> None:
    """A dialog for a failure before the window can open (Finder shows no terminal output).

    MUCARO_APP_DIALOGS=0 turns it off for automation, as in scripts/one_click.sh."""
    if os.environ.get("MUCARO_APP_DIALOGS", "1") == "0" or not OSASCRIPT.exists():
        return
    script = [
        "on run argv",
        "set message to item 1 of argv",
        "try",
        'display dialog message buttons {"OK"} default button 1 with title "Mucaro AI" with icon ((POSIX file (item 2 of argv)) as alias)',
        "on error",
        'display dialog message buttons {"OK"} default button 1 with title "Mucaro AI" with icon caution',
        "end try",
        "end run",
    ]
    command = [str(OSASCRIPT)]
    for line in script:
        command += ["-e", line]
    subprocess.run([*command, message, str(app_icon())], capture_output=True, check=False)


def name_the_app() -> None:
    """Show "Mucaro AI" in the menu bar. The process is the private Python, which has no app bundle.

    Must run before pywebview starts Cocoa; the Dock icon is set through webview.start(icon=...)."""
    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]  # pyobjc, installed with pywebview
    except ImportError:
        return
    with contextlib.suppress(Exception):
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_TITLE


class SetupNotice:
    """The "getting ready" dialog that scripts/one_click.sh shows during a slow first run.

    It is a child of this process (the shell script exec'd into Python). A child's pid is not reused
    before it is reaped, and the parent check below keeps any other process out of reach."""

    def __init__(self, pid: str | None) -> None:
        self.pid = int(pid) if pid and pid.strip().isdigit() else None

    def dismiss(self) -> None:
        pid, self.pid = self.pid, None
        if pid is None or not _is_child(pid):
            return
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            os.waitpid(pid, os.WNOHANG)


def _is_child(pid: int) -> bool:
    try:
        result = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, check=False)
    except OSError:
        return False
    return result.stdout.strip() == str(os.getpid())


class DesktopApp:
    """One run of Mucaro AI Setup.app: the window, the setup worker, then the service inside this process."""

    def __init__(self, setup: Any, log: Any, *, log_path: Path, data_dir: Path, notice: SetupNotice) -> None:
        self.setup = setup
        self.log = log
        self.log_path = log_path
        self.data_dir = data_dir
        self.notice = notice
        self.started = time.monotonic()
        # "Try again" restarts from this environment, before .env and the launch token were applied.
        self.initial_env = dict(os.environ)
        self.window: Any = None
        self.service: Any = None
        self.instance: Path | None = None
        self.window_event: dict[str, Any] | None = None
        self.closing = threading.Event()
        self.error_showing = False
        self.failed = False

    # --- window ------------------------------------------------------------------------------

    def open(self, webview: Any) -> int:
        """Show the window and run until it is closed (main thread). Returns the exit code."""
        name_the_app()
        self.window = webview.create_window(
            APP_TITLE,
            html=WINDOW_PAGE.read_text(encoding="utf-8"),
            width=1280,
            height=840,
            min_size=(420, 560),
        )
        self.window.expose(*self.actions())
        self.window.events.shown += self.on_shown
        # Cmd+Q ends the process inside AppKit without unwinding Python, so release resources on closing.
        self.window.events.closing += self.on_closing
        desktop_shell._graceful_sigterm(self.window.destroy)
        icon = app_icon()
        webview.start(
            self.work,
            private_mode=False,
            storage_path=str(self.data_dir / "webview"),
            icon=str(icon) if icon.is_file() else None,
        )
        return 2 if self.failed else 0

    def actions(self) -> tuple[Callable[[], None], ...]:
        """The error screen's buttons. window.expose() makes exactly these names callable from the page
        (unlike a js_api object, whose attributes the page could walk). They act only while the error
        screen shows, so the app page that later loads in the same window cannot use them."""

        def mucaro_retry() -> None:
            if self.error_showing:
                self.restart()

        def mucaro_show_log() -> None:
            if self.error_showing:
                subprocess.run(["/usr/bin/open", "-R", str(self.log_path)], capture_output=True, check=False)

        def mucaro_quit() -> None:
            if self.error_showing:
                self.window.destroy()

        return mucaro_retry, mucaro_show_log, mucaro_quit

    def on_shown(self) -> None:
        self.notice.dismiss()
        self.window_event = {"status": "WINDOW_SHOWN", "seconds_to_window": round(time.monotonic() - self.started, 3)}
        if self.instance is not None:
            desktop_shell.update_instance_file(self.instance, window=self.window_event)

    def on_closing(self) -> None:
        self.closing.set()
        # Stop a setup command that is still running; the next launch resumes where it stopped.
        one_click.terminate_active(timeout=2.0)
        one_click.release_lock()
        self._release_instance()

    def call_page(self, function: str, payload: Mapping[str, Any]) -> None:
        if self.window is None or self.closing.is_set():
            return
        with contextlib.suppress(Exception):
            self.window.evaluate_js(f"window.{function} && window.{function}({json.dumps(payload)})")

    def progress(self, step: int) -> None:
        self.call_page("mucaroProgress", {"step": step})

    # --- worker thread -----------------------------------------------------------------------

    def work(self) -> None:
        # Names an unexpected failure by where it happened: setup, or starting the service.
        phase = "ERROR_SETUP_UNEXPECTED"
        try:
            self.progress(1)
            self.run_setup()
            phase = "ERROR_DESKTOP_START_FAILED"
            self.progress(OPENING_STEP)
            self.start_service()
        except one_click.SetupError as exc:
            if exc.code != "SETUP_CANCELLED":
                self.show_error(exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - every failure must reach the window, not end silently in a thread
            self.log.raw(traceback.format_exc())
            self.show_error(phase, f"{type(exc).__name__}: {exc}")

    def run_setup(self) -> None:
        self.setup.on_step = lambda number, _title: self.progress(number)
        started = time.monotonic()
        try:
            ran = self.setup.run()
        finally:
            one_click.release_lock()
        elapsed = time.monotonic() - started
        if ran:
            self.log.line(f"ONE_CLICK_SETUP_OK steps={','.join(ran)} elapsed={elapsed:.0f}s")
        else:
            self.log.line(f"ONE_CLICK_FAST_PATH elapsed={elapsed:.1f}s")

    def start_service(self) -> None:
        if self.closing.is_set():
            return
        apply_env_file()
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        # Before backend.app.config is first imported (by LocalService): settings are read at import time.
        desktop_shell.prepare_environment(self.data_dir, token)
        self.service = desktop_shell.LocalService(self.data_dir / "logs" / "service.log", on_gui_ready=self.on_gui_ready)
        self.service.start()
        self.instance = desktop_shell.write_instance_file(self.data_dir, self.service.port, token)
        desktop_shell.update_instance_file(self.instance, security=self.service.security, launcher="MUCARO_AI_APP_CHECKOUT")
        if self.window_event is not None:
            desktop_shell.update_instance_file(self.instance, window=self.window_event)
        if self.closing.is_set():
            return
        self.log.line(f"DESKTOP_APP_READY port={self.service.port} data_dir={self.data_dir}")
        # The token travels in the URL fragment, which is never sent to the server or written to logs.
        self.window.load_url(f"http://127.0.0.1:{self.service.port}/#token={token}")

    def on_gui_ready(self, detail: dict[str, Any]) -> None:
        if self.instance is not None:
            desktop_shell.update_instance_file(
                self.instance,
                ui={"status": "GUI_CONNECTED", "seconds_to_ui": round(time.monotonic() - self.started, 3), **detail},
            )

    def show_error(self, code: str, detail: str) -> None:
        self.failed = True
        self.log.line("")
        self.log.line(f"{code} {detail}")
        self.log.line(f"Details: {self.log_path}")
        with contextlib.suppress(OSError):
            (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
            (self.data_dir / "logs" / "startup-error.log").write_text(f"{code} {detail}\n", encoding="utf-8")
        self.error_showing = True
        self.call_page(
            "mucaroError",
            {"code": code, "hint": ERROR_HINTS.get(code, DEFAULT_HINT), "detail": detail[:800], "log": str(self.log_path)},
        )

    # --- shutdown ----------------------------------------------------------------------------

    def restart(self) -> None:
        """Run the whole launch again in this same process, so macOS still sees Mucaro AI Setup.app (same Dock icon)."""
        self.stop_service()
        self.log.line("DESKTOP_APP_RETRY")
        self.log.close()
        os.execve("/bin/bash", ["/bin/bash", str(ROOT / "scripts" / "one_click.sh"), "--desktop"], self.initial_env)

    def stop_service(self) -> None:
        self._release_instance()
        service, self.service = self.service, None
        if service is not None:
            service.stop()

    def _release_instance(self) -> None:
        if self.instance is not None:
            with contextlib.suppress(FileNotFoundError):
                self.instance.unlink()


def _fail_before_window(log: Any, log_path: Path, notice: SetupNotice, code: str, detail: str) -> int:
    log.line("")
    log.line(f"{code} {detail}")
    log.line(f"Details: {log_path}")
    one_click.release_lock()
    log.close()
    notice.dismiss()
    alert(
        f"Mucaro AI could not open its window.\n\n{code} {detail}\n\nDetails: {log_path}\n\n"
        "Fix the problem named above (usually the internet connection), then open Mucaro AI again: setup resumes where it stopped."
    )
    return 2


def run(log: Any, *, log_path: Path, runtime: Path, pins: dict[str, str]) -> int:
    """Entry point from ``scripts/one_click.py --desktop`` on macOS; returns the exit code."""
    notice = SetupNotice(os.environ.get("MUCARO_SETUP_NOTICE_PID"))
    atexit.register(notice.dismiss)
    log.line(f"ONE_CLICK_STAGE1_START python={platform.python_version()} root={ROOT} mode=desktop")
    setup = one_click.OneClickSetup(log, runtime, pins)
    try:
        setup.check_stage0()
        ensure_window_library(log, setup.uv, pins)
        data_dir = data_directory()
        import webview  # type: ignore[import-not-found]  # pywebview, installed just above
    except one_click.SetupError as exc:
        return _fail_before_window(log, log_path, notice, exc.code, exc.detail)
    except Exception as exc:  # noqa: BLE001 - a broken window library must still be reported in a dialog
        log.raw(traceback.format_exc())
        return _fail_before_window(log, log_path, notice, "ERROR_DESKTOP_WINDOW_FAILED", f"{type(exc).__name__}: {exc}")

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    lock = desktop_shell.InstanceLock(data_dir)
    try:
        lock.acquire()
    except desktop_shell.AlreadyRunning:
        log.line(f"ERROR_ALREADY_RUNNING another Mucaro AI window is using {data_dir}")
        one_click.release_lock()
        log.close()
        notice.dismiss()
        alert("Mucaro AI is already open. Switch to it from the Dock.")
        return desktop_shell.EXIT_ALREADY_RUNNING

    app = DesktopApp(setup, log, log_path=log_path, data_dir=data_dir, notice=notice)
    try:
        return app.open(webview)
    except Exception as exc:  # noqa: BLE001 - the window could not be created: say so in a dialog
        log.raw(traceback.format_exc())
        return _fail_before_window(log, log_path, notice, "ERROR_DESKTOP_WINDOW_FAILED", f"{type(exc).__name__}: {exc}")
    finally:
        # Also reached when the window was closed while setup was still running.
        one_click.terminate_active()
        app.stop_service()
        one_click.release_lock()
        lock.release()
        notice.dismiss()
        log.close()
        one_click.prune_logs(log_path.parent)
