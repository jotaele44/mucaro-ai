"""Desktop shell: the double-clickable Mucaro app (GOAL-001, TGT-001, TGT-017).

Opening the app starts the local service in-process on a random loopback port,
protected by a per-launch token, and shows the GUI in a native window
(pywebview/WKWebView on macOS). No terminal, browser, source tree or manual
server start is involved. Closing the window stops the service.

User data lives outside the (replaceable, read-only) bundle:
``~/Library/Application Support/Mucaro AI`` on macOS. A single-instance lock
keeps two copies from writing the same data; ``instance.json`` (mode 0600)
tells local tooling where the running service is.

The per-launch token reaches the window in the URL fragment, which browsers
never send to the server or write to access logs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is not a supported desktop target
    fcntl = None  # type: ignore[assignment]

APP_NAME = "Mucaro AI"
EXIT_ALREADY_RUNNING = 3
EXIT_START_FAILED = 4


def default_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":  # pragma: no cover
        return Path(os.environ.get("APPDATA", home)) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "mucaro-ai"


def bundle_root() -> Path | None:
    """Resource root when running from a frozen (PyInstaller) bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if getattr(sys, "frozen", False) and meipass else None


class AlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "instance.lock"
        self.handle: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        if fcntl is None:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise AlreadyRunning(str(self.path)) from exc

    def release(self) -> None:
        if self.handle is not None:
            if fcntl is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def prepare_environment(data_dir: Path, token: str) -> None:
    """Must run before backend.app.config is imported: settings are read at import time."""
    os.environ["MUCARO_AI_DATA_DIR"] = str(data_dir)
    os.environ["MUCARO_AI_OPERATOR_TOKEN"] = token
    os.environ.setdefault("MUCARO_AI_DEPLOYMENT_PROFILE", "local")
    root = bundle_root()
    if root is not None:
        os.environ["MUCARO_AI_ROOT"] = str(root)


def _bind_loopback() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


class LocalService:
    """uvicorn serving the app on a pre-bound loopback socket in a background thread."""

    def __init__(self, log_path: Path, on_gui_ready: Any = None) -> None:
        import uvicorn

        from .config import Settings
        from .main import create_app

        self.socket = _bind_loopback()
        self.port = self.socket.getsockname()[1]
        app: Any = create_app(Settings.load())
        if on_gui_ready is not None:
            app = GuiReadyRecorder(app, on_gui_ready)
        config = uvicorn.Config(app, log_level="warning", access_log=False, lifespan="off")
        self.server = uvicorn.Server(config)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        for name in ("uvicorn", "uvicorn.error"):
            logging.getLogger(name).addHandler(handler)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.socket]}, name="mucaro-service", daemon=True)

    def start(self, timeout: float = 20.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.healthy():
                return
            time.sleep(0.1)
        raise RuntimeError("local service did not become healthy")

    def healthy(self) -> bool:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                return response.status == 200
        except OSError:
            return False

    def stop(self, timeout: float = 10.0) -> None:
        self.server.should_exit = True
        self.thread.join(timeout)
        with contextlib.suppress(OSError):
            self.socket.close()


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def write_instance_file(data_dir: Path, port: int, token: str) -> Path:
    path = data_dir / "instance.json"
    _write_private_json(path, {"pid": os.getpid(), "port": port, "token": token, "url": f"http://127.0.0.1:{port}/"})
    return path


_INSTANCE_LOCK = threading.Lock()


def update_instance_file(path: Path, **fields: Any) -> None:
    """Merge fields into instance.json (window and GUI readiness); never widens its mode."""
    with _INSTANCE_LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        payload.update(fields)
        _write_private_json(path, payload)


GUI_CLIENT_HEADER = b"x-mucaro-client"
GUI_CLIENT_VALUE = b"mucaro-gui"


class GuiReadyRecorder:
    """Pure ASGI wrapper noting the first authorized private API call made by the GUI.

    The GUI marks its requests with ``X-Mucaro-Client: mucaro-gui``. A successful private call
    proves the window loaded the app, ran it, and holds this launch's token. (A JavaScript probe
    is not possible: pywebview's evaluate_js needs eval, which the app's CSP forbids.)
    """

    def __init__(self, app: Any, on_ready: Any) -> None:
        self.app = app
        self.on_ready = on_ready
        self.done = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if self.done or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        from .auth import is_public_api_path

        headers = dict(scope.get("headers") or [])
        path = str(scope.get("path") or "")
        if headers.get(GUI_CLIENT_HEADER) != GUI_CLIENT_VALUE or is_public_api_path(path):
            await self.app(scope, receive, send)
            return
        user_agent = headers.get(b"user-agent", b"").decode("latin-1")

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and int(message.get("status", 500)) < 400 and not self.done:
                self.done = True
                self.on_ready({"first_path": path[:80], "webkit": "AppleWebKit" in user_agent})
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _graceful_sigterm(on_signal: Any) -> None:
    """Deliver SIGTERM/SIGINT to a worker thread even while AppKit owns the main thread.

    Python-level handlers only run when the main thread executes bytecode, which never happens
    while the Cocoa run loop is idle; the wakeup fd is written by the C-level handler instead.
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    signal.set_wakeup_fd(write_fd)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: None)

    def watch() -> None:
        with contextlib.suppress(OSError):
            os.read(read_fd, 1)
        on_signal()

    threading.Thread(target=watch, name="mucaro-signal", daemon=True).start()


def _open_window(url: str, data_dir: Path, *, instance: Path, started: float, on_closing: Any) -> str:
    try:
        import webview  # type: ignore[import-not-found]  # pywebview (desktop extra)
    except ImportError:
        # Documented fallback when the native window library is unavailable (not the release path).
        webbrowser.open(url)
        return "SYSTEM_BROWSER_FALLBACK"
    window = webview.create_window(APP_NAME, url=url, width=1280, height=840, min_size=(420, 560))

    def on_shown() -> None:
        update_instance_file(instance, window={"status": "WINDOW_SHOWN", "seconds_to_window": round(time.monotonic() - started, 3)})

    window.events.shown += on_shown
    # Cmd+Q ends the process inside AppKit without unwinding Python, so release resources on closing.
    window.events.closing += on_closing
    _graceful_sigterm(window.destroy)
    webview.start(private_mode=False, storage_path=str(data_dir / "webview"))
    return "NATIVE_WINDOW_CLOSED"


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mucaro-desktop", description="Start the Mucaro desktop app.")
    parser.add_argument("--data-dir", help="Override the user data directory.")
    parser.add_argument("--no-window", action="store_true", help="Serve without a window until SIGTERM (automation).")
    parser.add_argument("--smoke", action="store_true", help="Start, verify health, stop and exit 0 (packaging checks).")
    args, _unknown = parser.parse_known_args(argv)  # LaunchServices may add -psn_* arguments

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)
    lock = InstanceLock(data_dir)
    try:
        lock.acquire()
    except AlreadyRunning:
        print("ERROR_ALREADY_RUNNING Mucaro is already running for this data directory.", file=sys.stderr)
        return EXIT_ALREADY_RUNNING

    started = time.monotonic()
    token = secrets.token_urlsafe(32)
    prepare_environment(data_dir, token)
    service: LocalService | None = None
    instance: Path | None = None

    def release_instance() -> None:
        if instance is not None:
            with contextlib.suppress(FileNotFoundError):
                instance.unlink()

    def on_gui_ready(detail: dict[str, Any]) -> None:
        if instance is not None:
            update_instance_file(instance, ui={"status": "GUI_CONNECTED", "seconds_to_ui": round(time.monotonic() - started, 3), **detail})

    try:
        service = LocalService(data_dir / "logs" / "service.log", on_gui_ready=on_gui_ready)
        service.start()
        instance = write_instance_file(data_dir, service.port, token)
        url = f"http://127.0.0.1:{service.port}/#token={token}"
        if args.smoke:
            print(f"DESKTOP_SMOKE_OK port={service.port} data_dir={data_dir}")
            return 0
        if args.no_window:
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            print(f"DESKTOP_SERVING port={service.port}", flush=True)
            stop.wait()
            return 0
        outcome = _open_window(url, data_dir, instance=instance, started=started, on_closing=release_instance)
        if outcome == "SYSTEM_BROWSER_FALLBACK":
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            stop.wait()
        return 0
    except Exception as exc:  # noqa: BLE001 - report any startup failure with a status and log it
        (data_dir / "logs" / "startup-error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        print(f"ERROR_DESKTOP_START_FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_START_FAILED
    finally:
        release_instance()
        if service is not None:
            service.stop()
        lock.release()


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
