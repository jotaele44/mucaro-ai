"""The native Mucaro AI window.

Used by ``scripts/launch_app.py --native`` (what ``Mucaro AI.app`` runs). The window is
pywebview/WKWebView, so no browser is involved, and this module never falls back to one:
if the window cannot open, the caller fails (Mucaro AI.app shows the dialog).
"""

from __future__ import annotations

import contextlib
import os
import signal
import threading
from collections.abc import Callable
from pathlib import Path

APP_NAME = "Mucaro AI"
WINDOW_SIZE = (1280, 840)
WINDOW_MIN_SIZE = (420, 560)

def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_window_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None


def activate_existing_window(pid_file: Path) -> bool:
    """Bring an already-open Mucaro AI window to the front. False when none is open."""
    pid = read_window_pid(pid_file)
    if pid is None or pid == os.getpid():
        return False
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication  # type: ignore[import-not-found]

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except ImportError:
        return True  # a window is open; without AppKit it just cannot be raised
    return True


def _name_the_app() -> None:
    """Show "Mucaro AI" instead of "Python" in the menu bar and Dock."""
    with contextlib.suppress(Exception):
        from Foundation import NSBundle  # type: ignore[import-not-found]

        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = APP_NAME


def _set_dock_icon() -> None:
    """Show the Mucaro AI owl in the Dock while the window is open."""
    icon = Path(__file__).resolve().parents[1] / "Mucaro AI.app" / "Contents" / "Resources" / "MucaroAI.png"
    with contextlib.suppress(Exception):
        from AppKit import NSApplication, NSImage  # type: ignore[import-not-found]

        image = NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)


def _stop_on_signal(on_signal: Callable[[], None]) -> None:
    """Deliver SIGTERM/SIGINT to a worker thread even while AppKit owns the main thread."""
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    signal.set_wakeup_fd(write_fd)
    for signum in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", signal.SIGTERM)):
        signal.signal(signum, lambda *_: None)

    def watch() -> None:
        with contextlib.suppress(OSError):
            os.read(read_fd, 1)
        on_signal()

    threading.Thread(target=watch, name="mucaro-signal", daemon=True).start()


def run_window(url: str, *, storage_dir: Path, pid_file: Path) -> None:
    """Show the app in a native window and return once it is closed. Raises ImportError if pywebview is missing."""
    import webview  # type: ignore[import-not-found]

    _name_the_app()
    _set_dock_icon()
    window = webview.create_window(APP_NAME, url=url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE)
    _stop_on_signal(window.destroy)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(storage_dir))
    finally:
        with contextlib.suppress(FileNotFoundError):
            pid_file.unlink()
