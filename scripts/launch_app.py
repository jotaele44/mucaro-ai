#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.env_file import merge_env_file  # noqa: E402

BACKEND_APP = "backend.app.main:app"


def build_env() -> dict[str, str]:
    # Load Mucaro settings from .env, then replace every host-controlled Python setting.
    merged = merge_env_file(os.environ, ROOT / ".env")
    env = {key: value for key, value in merged.items() if not key.startswith("PYTHON")}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "backend")])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def health_url(port: int) -> str:
    return f"{url_for(port)}/health"


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(health_url(port), timeout=1.5) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200 and '"status":"OK"' in body.replace(" ", "")
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def run_checked(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("RUN", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env or build_env(), check=True)


def ensure_frontend_dist(skip_build: bool = False) -> None:
    dist_index = ROOT / "frontend" / "dist" / "index.html"
    if dist_index.exists():
        print(f"OK_FRONTEND_DIST {dist_index}")
        return
    if skip_build:
        raise SystemExit("ERROR_FRONTEND_DIST_MISSING run: cd frontend && npm ci && npm run build")
    node_modules = ROOT / "frontend" / "node_modules"
    package_json = ROOT / "frontend" / "package.json"
    if not package_json.exists():
        raise SystemExit("ERROR_FRONTEND_PACKAGE_MISSING frontend/package.json")
    if not node_modules.exists():
        raise SystemExit("ERROR_FRONTEND_DEPS_MISSING run: cd frontend && npm ci")
    run_checked(["npm", "run", "build"], cwd=ROOT / "frontend")
    if not dist_index.exists():
        raise SystemExit("ERROR_FRONTEND_BUILD_NO_DIST frontend/dist/index.html")
    print(f"OK_FRONTEND_DIST {dist_index}")


def wait_for_health(port: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if health_ok(port):
            return True
        time.sleep(0.25)
    return False


def start_backend(port: int) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-s",
        "-P",
        "-B",
        "-m",
        "uvicorn",
        BACKEND_APP,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    print("STARTING_DESKTOP_BACKEND", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=ROOT, env=build_env())


def stop_backend(proc: subprocess.Popen, port: int) -> bool:
    if proc.poll() is None:
        print("STOPPING_DESKTOP_BACKEND")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)
    deadline = time.time() + 4
    while time.time() < deadline:
        if not port_open(port):
            print("DESKTOP_BACKEND_PORT_CLOSED")
            return True
        time.sleep(0.2)
    print(f"ERROR_BACKEND_PORT_STILL_OPEN port={port}", file=sys.stderr)
    return False


def find_fallback_port(port: int, span: int = 20) -> int | None:
    """Return the first later port that is free or already serves a healthy Mucaro AI backend."""
    for candidate in range(port + 1, port + span + 1):
        if not port_open(candidate) or health_ok(candidate):
            return candidate
    return None


def _raise_keyboard_interrupt(signum, frame) -> None:
    del frame
    if signum == getattr(signal, "SIGHUP", None):
        # The terminal is gone; printing to it would fail before the backend is stopped.
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    raise KeyboardInterrupt


def install_stop_signals() -> dict[int, object]:
    # Closing the Terminal window sends SIGHUP; treat it (and SIGTERM) like Ctrl+C so the backend is stopped.
    previous: dict[int, object] = {}
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous[sig] = signal.signal(sig, _raise_keyboard_interrupt)
        except (OSError, ValueError):
            continue
    return previous


def restore_signals(previous: dict[int, object]) -> None:
    for sig, handler in previous.items():
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError, TypeError):
            continue


def self_test() -> int:
    checks = {
        "ROOT_PRESENT": ROOT.exists(),
        "BACKEND_MAIN_PRESENT": (ROOT / "backend" / "app" / "main.py").exists(),
        "FRONTEND_PACKAGE_PRESENT": (ROOT / "frontend" / "package.json").exists(),
        "SCRIPTS_PRESENT": (ROOT / "scripts").exists(),
    }
    for name, ok in checks.items():
        print(("OK_" if ok else "ERROR_") + name)
    if not all(checks.values()):
        return 2
    print("LAUNCH_APP_SELF_TEST_OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch Mucaro AI as a desktop-style local app.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Do not build frontend/dist if missing.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="Start, verify, stop, and exit without opening a long-running session.")
    parser.add_argument("--hold-seconds", type=float, default=0.5, help="Seconds to hold a successful smoke-test backend before stopping.")
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="If the port is used by another program, use the next free port (up to 20 higher) instead of failing.",
    )
    parser.add_argument(
        "--exit-if-running",
        action="store_true",
        help="If Mucaro AI is already running, open it in the browser and exit instead of waiting.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    previous_signals = install_stop_signals()
    try:
        return _launch(args)
    finally:
        restore_signals(previous_signals)


def _launch(args: argparse.Namespace) -> int:
    print(f"DESKTOP_LAUNCH_START root={ROOT}")
    ensure_frontend_dist(skip_build=args.skip_build)

    port = args.port
    if args.auto_port and port_open(port) and not health_ok(port):
        fallback = find_fallback_port(port)
        if fallback is None:
            print(f"ERROR_PORT_IN_USE_NON_MUCARO_AI port={port} no free port up to {port + 20}", file=sys.stderr)
            return 5
        print(f"PORT_FALLBACK from={port} to={fallback}")
        port = fallback

    url = url_for(port)
    proc: subprocess.Popen | None = None
    try:
        if port_open(port):
            if health_ok(port):
                print(f"REUSING_EXISTING_BACKEND {health_url(port)}")
            else:
                print(f"ERROR_PORT_IN_USE_NON_MUCARO_AI port={port}", file=sys.stderr)
                return 5
        else:
            proc = start_backend(port)
            if not wait_for_health(port, args.startup_timeout):
                if proc.poll() is not None:
                    print(f"ERROR_BACKEND_EXITED_EARLY returncode={proc.returncode}", file=sys.stderr)
                    return proc.returncode or 6
                print(f"ERROR_BACKEND_HEALTH_TIMEOUT url={health_url(port)}", file=sys.stderr)
                return 7
            print(f"BACKEND_READY {health_url(port)}")

        if not args.no_browser:
            print(f"OPENING_BROWSER {url}")
            webbrowser.open(url)

        if proc is None and args.exit_if_running and not args.smoke_test:
            print(f"DESKTOP_LAUNCH_REUSED {url}")
            print(f"Mucaro AI is already running at {url} (in another window). This window can be closed.")
            return 0

        print("DESKTOP_LAUNCH_OK press Ctrl+C to stop")
        if args.smoke_test:
            time.sleep(max(0.0, args.hold_seconds))
            print("DESKTOP_SMOKE_READY")
            if proc is not None:
                ok = stop_backend(proc, port)
                proc = None
                if not ok:
                    return 9
            print("DESKTOP_SMOKE_OK")
            return 0

        print("")
        print(f"Mucaro AI is running at {url}")
        print("Keep this window open while you use it. To stop, close this window or press Ctrl+C.")
        print("", flush=True)
        try:
            while True:
                if proc is not None and proc.poll() is not None:
                    print(f"ERROR_BACKEND_EXITED returncode={proc.returncode}", file=sys.stderr)
                    return proc.returncode or 8
                time.sleep(1)
        except KeyboardInterrupt:
            print("DESKTOP_STOP_REQUESTED")
            return 0
        finally:
            if proc is not None and proc.poll() is None:
                stop_backend(proc, port)
                proc = None
            print("DESKTOP_STOP_OK")
    finally:
        # Covers Ctrl+C, a closed window, or an error while the backend was still starting.
        if proc is not None and proc.poll() is None:
            stop_backend(proc, port)


if __name__ == "__main__":
    raise SystemExit(main())
