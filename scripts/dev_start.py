#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.env_file import merge_env_file  # noqa: E402


def build_env() -> dict[str, str]:
    # Shell-exported variables win over values from the project .env file.
    env = merge_env_file(os.environ, ROOT / ".env")
    existing = env.get("PYTHONPATH", "")
    paths = [str(ROOT), str(ROOT / "backend")]
    env["PYTHONPATH"] = os.pathsep.join(paths + ([existing] if existing else []))
    return env


def start_process(name: str, cmd: list[str], cwd: Path) -> subprocess.Popen:
    print(f"STARTING_{name.upper()} {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=cwd, env=build_env())


def main() -> int:
    parser = argparse.ArgumentParser(description="Start Mucaro AI dev services.")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--backend-port", default="8000")
    parser.add_argument("--frontend-port", default="5173")
    args = parser.parse_args()

    if args.backend_only and args.frontend_only:
        print("ERROR_MODE_CONFLICT choose only one of --backend-only or --frontend-only", file=sys.stderr)
        return 2

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        if not args.frontend_only:
            procs.append((
                "backend",
                start_process(
                    "backend",
                    [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--reload", "--host", "127.0.0.1", "--port", args.backend_port],
                    ROOT,
                ),
            ))
            print(f"BACKEND_URL http://127.0.0.1:{args.backend_port}")

        if not args.backend_only:
            if shutil.which("npm") is None:
                print("ERROR_NPM_MISSING install Node 20+ or run --backend-only", file=sys.stderr)
                return 3
            if not (ROOT / "frontend" / "node_modules").exists():
                print("ERROR_FRONTEND_DEPS_MISSING run: cd frontend && npm ci", file=sys.stderr)
                return 4
            procs.append((
                "frontend",
                start_process("frontend", ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", args.frontend_port], ROOT / "frontend"),
            ))
            print(f"FRONTEND_URL http://127.0.0.1:{args.frontend_port}")

        print("DEV_START_OK press Ctrl+C to stop")
        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"ERROR_PROCESS_EXITED name={name} returncode={code}", file=sys.stderr)
                    return code or 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("STOP_REQUESTED")
        return 0
    finally:
        for name, proc in procs:
            if proc.poll() is None:
                print(f"STOPPING_{name.upper()}")
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=8)
        print("DEV_STOP_OK")


if __name__ == "__main__":
    raise SystemExit(main())
