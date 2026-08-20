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
BACKEND_APP = "backend.app.main:app"


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    paths = [str(ROOT), str(ROOT / "backend")]
    env["PYTHONPATH"] = os.pathsep.join(paths + ([existing] if existing else []))
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Mucaro AI as a desktop-style local app.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Do not build frontend/dist if missing.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    print(f"DESKTOP_LAUNCH_START root={ROOT}")
    ensure_frontend_dist(skip_build=args.skip_build)

    url = url_for(args.port)
    proc: subprocess.Popen | None = None

    if port_open(args.port):
        if health_ok(args.port):
            print(f"REUSING_EXISTING_BACKEND {health_url(args.port)}")
        else:
            print(f"ERROR_PORT_IN_USE_NON_MUCARO_AI port={args.port}", file=sys.stderr)
            return 5
    else:
        proc = start_backend(args.port)
        if not wait_for_health(args.port, args.startup_timeout):
            if proc.poll() is not None:
                print(f"ERROR_BACKEND_EXITED_EARLY returncode={proc.returncode}", file=sys.stderr)
                return proc.returncode or 6
            print(f"ERROR_BACKEND_HEALTH_TIMEOUT url={health_url(args.port)}", file=sys.stderr)
            return 7
        print(f"BACKEND_READY {health_url(args.port)}")

    if not args.no_browser:
        print(f"OPENING_BROWSER {url}")
        webbrowser.open(url)

    print("DESKTOP_LAUNCH_OK press Ctrl+C to stop")
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
            print("STOPPING_DESKTOP_BACKEND")
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=8)
        print("DESKTOP_STOP_OK")


if __name__ == "__main__":
    raise SystemExit(main())
