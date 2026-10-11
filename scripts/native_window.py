"""The native Mucaro AI window and the few pieces of user feedback a launch without a Terminal needs.

Used by ``scripts/launch_app.py --native`` (what ``Mucaro AI.app`` and ``Mucaro AI.exe`` run). The
window is pywebview: WKWebView on macOS, Microsoft Edge WebView2 on Windows. No browser is involved,
and this module never falls back to one: if the window cannot open, the caller reports it with a
dialog instead.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import select
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

APP_NAME = "Mucaro AI"
APP_USER_MODEL_ID = "ai.mucaro.source-launcher"
WINDOW_SIZE = (1280, 840)
WINDOW_MIN_SIZE = (420, 560)
WINDOWS_ICON = Path(__file__).resolve().parents[1] / "packaging" / "icons" / "mucaro.ico"
WEBVIEW2_DOWNLOAD = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


class WebView2Missing(RuntimeError):
    """The Microsoft Edge WebView2 Runtime that draws the window on Windows is not installed."""

_DIALOG_SCRIPT = (
    "on run argv",
    'display dialog (item 1 of argv) with title "Mucaro AI" buttons {"OK"} default button "OK" with icon caution',
    "end run",
)
_NOTIFY_SCRIPT = (
    "on run argv",
    'display notification (item 1 of argv) with title "Mucaro AI"',
    "end run",
)


# The link carries the access token, so it reaches this script through the environment, never through argv (which
# any local user can list) and never through a log file.
_PHONE_LINK_VARIABLE = "MUCARO_PHONE_LINK"
_PHONE_SCRIPT = (
    "on run argv",
    'set answer to button returned of (display dialog (item 1 of argv) with title "Mucaro AI" '
    'buttons {"Copy link", "OK"} default button "OK" with icon note)',
    f'if answer is "Copy link" then set the clipboard to (system attribute "{_PHONE_LINK_VARIABLE}")',
    "end run",
)


def _osascript(script: tuple[str, ...], message: str) -> None:
    if sys.platform != "darwin":
        return
    cmd = ["/usr/bin/osascript"]
    for line in script:
        cmd += ["-e", line]
    # The message travels as an argument, never inside the script text, so it cannot break out of it.
    cmd += ["--", message]
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600, check=False)


def _windll(name: str):  # pragma: no cover - replaced by fakes in tests off Windows
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


def show_dialog(message: str) -> None:
    """Tell the user something went wrong. With no Terminal or console, stderr is invisible."""
    if sys.platform == "win32":
        mb_ok_iconwarning_setforeground = 0x0 | 0x30 | 0x10000
        with contextlib.suppress(Exception):
            _windll("user32").MessageBoxW(None, message, APP_NAME, mb_ok_iconwarning_setforeground)
        return
    _osascript(_DIALOG_SCRIPT, message)


def notify(message: str) -> None:
    _osascript(_NOTIFY_SCRIPT, message)


def _windows_phone_dialog(message: str, link: str) -> None:
    """Windows has no scriptable dialog with a Copy button: ask Yes or No, then copy with clip.exe.

    The link goes to the clipboard on request and is never part of the dialog text or a log.
    """
    mb_yesno, mb_iconinformation, mb_setforeground, id_yes = 0x4, 0x40, 0x10000, 6
    with contextlib.suppress(Exception):
        if not link:
            _windll("user32").MessageBoxW(None, message, APP_NAME, mb_iconinformation | mb_setforeground)
            return
        answer = _windll("user32").MessageBoxW(
            None, f"{message}\n\nCopy the link to the clipboard?", APP_NAME, mb_yesno | mb_iconinformation | mb_setforeground
        )
        if answer == id_yes:
            subprocess.run(["clip"], input=link, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)


def show_phone_link(message: str, link: str) -> None:
    """Say how a phone can reach Mucaro and offer to copy the link. Does not wait: the app window opens meanwhile.

    An empty link shows the message alone (phone access is on but there is nothing to open yet).
    """
    if sys.platform == "win32":
        # A message box waits for an answer, so it gets its own thread while the window opens.
        threading.Thread(target=_windows_phone_dialog, args=(message, link), name="mucaro-phone-link", daemon=True).start()
        return
    if sys.platform != "darwin":
        return
    cmd = ["/usr/bin/osascript"]
    for line in _PHONE_SCRIPT if link else _DIALOG_SCRIPT:
        cmd += ["-e", line]
    cmd += ["--", message]
    env = {**os.environ, _PHONE_LINK_VARIABLE: link} if link else None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _windows_pid_alive(pid: int) -> bool:
    # os.kill(pid, 0) would terminate the process on Windows; ask for its exit code instead.
    process_query_limited_information = 0x1000
    still_active = 259
    error_access_denied = 5
    kernel32 = _windll("kernel32")
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == error_access_denied
    try:
        code = ctypes.c_ulong(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
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


def _windows_activate(pid: int) -> bool:
    user32 = _windll("user32")
    found: list[object] = []
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    enum_proc = callback_type(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def check(hwnd, _lparam):
        owner = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return 0
        return 1

    user32.EnumWindows(enum_proc(check), 0)
    if not found:
        return False
    sw_restore = 9
    user32.ShowWindow(found[0], sw_restore)
    return bool(user32.SetForegroundWindow(found[0]))


def activate_existing_window(pid_file: Path) -> bool:
    """Bring an already-open Mucaro AI window to the front. False when none is open."""
    pid = read_window_pid(pid_file)
    if pid is None or pid == os.getpid():
        return False
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            return _windows_activate(pid)
        return False
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication  # type: ignore[import-not-found]

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except ImportError:
        return False


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


def windows_renderer() -> str | None:
    """The engine pywebview will draw with on Windows.

    pywebview decides when its WinForms backend is imported: "edgechromium" when the WebView2
    Runtime (and .NET 4.6.2+) is installed, otherwise it silently falls back to Internet Explorer
    ("mshtml"), which cannot run the Mucaro AI interface.
    """
    from webview.platforms import winforms  # type: ignore[import-not-found]  # loads pythonnet

    return getattr(winforms, "renderer", None)


def _windows_identity() -> None:
    """Group the window under Mucaro AI in the taskbar, not under Python."""
    with contextlib.suppress(Exception):
        _windll("shell32").SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def _set_windows_icon(window) -> None:
    """Show the Mucaro AI owl on the window and in the taskbar (best effort, like the Dock icon)."""
    with contextlib.suppress(Exception):
        from System import Action  # type: ignore[import-not-found]  # pythonnet, installed with pywebview
        from System.Drawing import Icon  # type: ignore[import-not-found]

        form = window.native
        icon = Icon(str(WINDOWS_ICON))
        form.Invoke(Action(lambda: setattr(form, "Icon", icon)))


@contextlib.contextmanager
def _stop_on_signal(on_signal: Callable[[], None]) -> Iterator[None]:
    """Deliver SIGTERM/SIGINT to a worker thread even while AppKit owns the main thread."""
    if sys.platform == "win32":
        # Windows has no select() on pipes and no SIGTERM from other processes; closing the
        # window is how the app stops there.
        yield
        return
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    previous_fd = signal.set_wakeup_fd(write_fd)
    previous_handlers = {}
    stopped = threading.Event()

    def watch() -> None:
        while not stopped.is_set():
            ready, _, _ = select.select([read_fd], [], [], 0.1)
            if ready:
                os.read(read_fd, 1)
                on_signal()
                return

    worker = threading.Thread(target=watch, name="mucaro-signal", daemon=True)
    try:
        for signum in {signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", signal.SIGTERM)}:
            previous_handlers[signum] = signal.signal(signum, lambda *_: None)
        worker.start()
        yield
    finally:
        signal.set_wakeup_fd(previous_fd)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        stopped.set()
        if worker.ident is not None:
            worker.join(timeout=1)
        os.close(read_fd)
        os.close(write_fd)


def _gui() -> str | None:
    if sys.platform == "darwin":
        return "cocoa"
    if sys.platform == "win32":
        return "edgechromium"
    return None


def run_window(url: str, *, storage_dir: Path, pid_file: Path) -> None:
    """Show the app in a native window and return once it is closed.

    Raises ImportError if pywebview is missing, and WebView2Missing on Windows when the WebView2
    Runtime is not installed (checked before any window opens).
    """
    import webview  # type: ignore[import-not-found]

    _name_the_app()
    _set_dock_icon()
    if sys.platform == "win32":
        if windows_renderer() != "edgechromium":
            raise WebView2Missing(f"The Microsoft Edge WebView2 Runtime is not installed (install it from {WEBVIEW2_DOWNLOAD}).")
        _windows_identity()
    # Save and Share in the interface download files; pywebview drops downloads unless this is on, and then asks
    # where to save each one (an NSSavePanel on macOS, a Save File dialog on Windows).
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_NAME, url=url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE)
    if sys.platform == "win32":
        window.events.shown += lambda: _set_windows_icon(window)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        with _stop_on_signal(window.destroy):
            webview.start(gui=_gui(), private_mode=False, storage_path=str(storage_dir))
    finally:
        with contextlib.suppress(FileNotFoundError):
            pid_file.unlink()
