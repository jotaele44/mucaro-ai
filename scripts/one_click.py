#!/usr/bin/env python3
"""Stage 1 of the double-click setup: project dependencies, UI build, first-run data, launch.

Started with the project ``.venv`` interpreter by ``scripts/one_click.sh`` (macOS/Linux) or
``scripts/one_click_windows.ps1`` (Windows), after those scripts provisioned the private uv,
Node, and Python runtimes in ``.mucaro-runtime/``. Each step records a fingerprint of its inputs
in ``.mucaro-runtime/state.json`` and is skipped when nothing it depends on changed, so later
double-clicks go straight to launch and an interrupted setup resumes where it stopped.

Only the standard library is used before the Python dependencies are installed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_app, lock_python_deps  # noqa: E402

# desktop (pywebview) draws the native window that Mucaro AI.app (macOS) and Mucaro AI.exe (Windows) open.
NATIVE_WINDOW_PLATFORMS = ("darwin", "win32")
NATIVE_WINDOW_MODULES = {
    "darwin": ("webview", "AppKit", "Foundation", "WebKit"),
    "win32": ("webview", "clr"),  # clr: pythonnet, which pywebview's WebView2 backend loads
}
PYTHON_EXTRAS = "dev,anthropic,desktop" if sys.platform in NATIVE_WINDOW_PLATFORMS else "dev,anthropic"
# Import names that prove the dependency install is usable (runtime, geospatial, live mode, preflight).
KEY_MODULES = (
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "python_multipart",
    "jsonschema",
    "pypdf",
    "shapely",
    "pyproj",
    "rasterio",
    "geographiclib",
    "anthropic",
    "httpx",
    "pytest",
) + NATIVE_WINDOW_MODULES.get(sys.platform, ())
NPM_REGISTRY = "https://registry.npmjs.org/"
LOG_KEEP = 10
TOTAL_STEPS = 6
STATE_SCHEMA = "mucaro-one-click-state/1"
# First start after an install compiles bytecode and loads new native libraries, so allow more than the default 20s.
# --frontend-verified: step 3 already fingerprinted the build inputs, so the launcher must not
# re-check timestamps (and must never reach for a PATH npm).
LAUNCH_ARGS = ["--auto-port", "--exit-if-running", "--frontend-verified", "--startup-timeout", "120"]


class SetupError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code} {detail}")
        self.code = code
        self.detail = detail


def lock_target(system: str | None = None, machine: str | None = None, interpreter_platform: str | None = None) -> str | None:
    """The packaging/one_click lock for this interpreter (see scripts/lock_python_deps.py), or None.

    Follows the interpreter's own architecture: x64 Python running under emulation on ARM64
    Windows uses the x64 lock, because the wheels it installs must match the interpreter.
    """
    system = system or sys.platform
    if system == "darwin":
        return {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}.get(machine or platform.machine())
    if system == "win32":
        return {"win-amd64": "x86_64-pc-windows-msvc"}.get(interpreter_platform or sysconfig.get_platform())
    if system.startswith("linux"):
        return {"x86_64": "x86_64-unknown-linux-gnu"}.get(machine or platform.machine())
    return None


def runtime_dir() -> Path:
    return Path(os.environ.get("MUCARO_RUNTIME_DIR") or ROOT / ".mucaro-runtime")


def read_pins(path: Path | None = None) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in (path or ROOT / "scripts" / "one_click_pins.env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            pins[key.strip()] = value.strip()
    return pins


def fingerprint(parts: Iterable[str], files: Iterable[Path] = ()) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(b"part\0" + part.encode("utf-8") + b"\0")
    for path in sorted(set(files)):
        name = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
        digest.update(b"file\0" + name.encode("utf-8") + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def frontend_source_files() -> list[Path]:
    return launch_app.frontend_source_files(ROOT)


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        return {}
    return payload


def save_state(path: Path, state: dict[str, Any]) -> None:
    # Write-then-rename so an interrupted setup never leaves a half-written state file.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({**state, "schema": STATE_SCHEMA}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def missing_modules(names: Iterable[str] = KEY_MODULES) -> list[str]:
    importlib.invalidate_caches()
    return [name for name in names if importlib.util.find_spec(name) is None]


def node_layout(node_dir: Path) -> tuple[Path, Path]:
    """Return (node executable, npm-cli.js) for an official Node.js archive layout."""
    if os.name == "nt":
        return node_dir / "node.exe", node_dir / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return node_dir / "bin" / "node", node_dir / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"


def node_env(base: Mapping[str, str], node_dir: Path, runtime: Path) -> dict[str, str]:
    # Drop the user's npm settings (a dead registry, a root-owned ~/.npm cache) so npm only
    # sees the project .npmrc plus the values below.
    env = {
        key: value
        for key, value in base.items()
        if not key.lower().startswith("npm_config_") and key != "NODE_OPTIONS"
    }
    node_exe, _ = node_layout(node_dir)
    env["PATH"] = os.pathsep.join(part for part in (str(node_exe.parent), base.get("PATH", "")) if part)
    userconfig = runtime / "npmrc"
    userconfig.parent.mkdir(parents=True, exist_ok=True)
    if not userconfig.exists():
        userconfig.write_text("", encoding="utf-8")
    env.update(
        {
            "npm_config_cache": str(runtime / "cache" / "npm"),
            "npm_config_userconfig": str(userconfig),
            "npm_config_update_notifier": "false",
            "npm_config_fund": "false",
            "npm_config_audit": "false",
        }
    )
    return env


def release_lock() -> None:
    lock = os.environ.get("MUCARO_SETUP_LOCK")
    token = os.environ.get("MUCARO_SETUP_LOCK_TOKEN")
    if not lock or not token:
        return
    lock_dir = Path(lock)
    try:
        holder = (lock_dir / "pid").read_text(encoding="utf-8").strip().lstrip("﻿")
    except OSError:
        return
    if holder == token:
        shutil.rmtree(lock_dir, ignore_errors=True)


def prune_logs(log_dir: Path, keep: int = LOG_KEEP) -> None:
    for pattern in ("setup-*.log", "launch-*.log"):
        for old in sorted(log_dir.glob(pattern))[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass


class SetupLog:
    """Echo setup output to the console and append it to the setup log file."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._handle = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8")

    def raw(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        if self._handle is not None:
            self._handle.write(text)
            self._handle.flush()

    def line(self, text: str = "") -> None:
        self.raw(text + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def run_logged(cmd: list[str], *, log: SetupLog, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> int:
    log.line("RUN " + " ".join(str(part) for part in cmd))
    try:
        proc = subprocess.Popen(
            [str(part) for part in cmd],
            cwd=str(cwd or ROOT),
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        log.line(f"ERROR_COMMAND_START {exc}")
        return 127
    if proc.stdout is None:
        log.line("ERROR_COMMAND_START stdout pipe unavailable")
        proc.kill()
        return 127
    for chunk in iter(proc.stdout.readline, b""):
        log.raw(chunk.decode("utf-8", errors="replace"))
    return proc.wait()


def run_with_retries(
    label: str,
    cmd: list[str],
    *,
    log: SetupLog,
    attempts: int,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    delay = 2.0
    returncode = 0
    for attempt in range(1, attempts + 1):
        returncode = run_logged(cmd, log=log, cwd=cwd, env=env)
        if returncode == 0:
            return
        if attempt < attempts:
            log.line(f"WARN_RETRY step={label} attempt={attempt} returncode={returncode} wait={delay:.0f}s")
            sleep(delay)
            delay *= 2
    raise SetupError(f"ERROR_{label.upper()}_FAILED", f"returncode={returncode} after {attempts} attempt(s); see the output above")


class OneClickSetup:
    def __init__(self, log: SetupLog, runtime: Path, pins: dict[str, str]) -> None:
        self.log = log
        self.runtime = runtime
        self.pins = pins
        self.state_path = runtime / "state.json"
        self.state = load_state(self.state_path)
        self.frontend = ROOT / "frontend"
        exe = ".exe" if os.name == "nt" else ""
        self.uv = Path(os.environ.get("MUCARO_UV_BIN") or runtime / "uv" / pins["UV_VERSION"] / f"uv{exe}")
        self.node_dir = Path(os.environ.get("MUCARO_NODE_DIR") or runtime / "node" / pins["NODE_VERSION"])
        self.node, self.npm_cli = node_layout(self.node_dir)
        self.ran: list[str] = []

    def _step(self, number: int, title: str) -> None:
        self.log.line(f"==> [{number}/{TOTAL_STEPS}] {title}")

    def _record(self, key: str, value: Any) -> None:
        self.state[key] = value
        save_state(self.state_path, self.state)

    def check_stage0(self) -> None:
        if sys.prefix == sys.base_prefix:
            raise SetupError("ERROR_NOT_IN_VENV", "start Mucaro AI by double-clicking Mucaro AI Setup.app (macOS) or Mucaro AI.exe (Windows)")
        for label, path in (("uv", self.uv), ("node", self.node), ("npm", self.npm_cli)):
            if not path.is_file():
                raise SetupError(
                    "ERROR_STAGE0_MISSING",
                    f"{label} not found at {path}; start Mucaro AI by double-clicking Mucaro AI Setup.app (macOS) or Mucaro AI.exe (Windows)",
                )

    def python_deps(self) -> None:
        self._step(1, "Python packages (first run: about 1-2 minutes)")
        target = lock_target()
        lock_dir = ROOT / "packaging" / "one_click"
        lock = lock_python_deps.lock_path(target, lock_dir) if target else None
        if lock is None or not lock.is_file():
            raise SetupError(
                "ERROR_PLATFORM_UNSUPPORTED",
                f"no Python lock for {sys.platform} {platform.machine()} ({sysconfig.get_platform()}) in {lock_dir}",
            )
        fp = fingerprint(
            [
                f"schema={self.pins['SETUP_SCHEMA']}",
                f"python={platform.python_version()}",
                f"extras={PYTHON_EXTRAS}",
                f"lock={target}",
                f"uv={self.pins['UV_VERSION']}",
            ],
            [ROOT / "pyproject.toml", lock],
        )
        missing = missing_modules()
        if self.state.get("python_deps") == fp and not missing:
            self.log.line("PY_DEPS_CACHED")
            return
        if missing:
            self.log.line("PY_DEPS_MISSING " + ",".join(missing))
        # Every package comes from the hash-pinned lock (scripts/lock_python_deps.py), including
        # setuptools, which then builds the editable project install without fetching anything else.
        self.log.line(f"PY_DEPS_LOCK {lock.relative_to(ROOT)}")
        run_with_retries(
            "py_deps",
            [str(self.uv), "pip", "install", "--python", sys.executable, "--require-hashes", "--no-deps", "-r", str(lock)],
            log=self.log,
            attempts=3,
            cwd=ROOT,
        )
        run_with_retries(
            "py_deps",
            [str(self.uv), "pip", "install", "--python", sys.executable, "--no-deps", "--no-build-isolation", "-e", "."],
            log=self.log,
            attempts=3,
            cwd=ROOT,
        )
        missing = missing_modules()
        if missing:
            raise SetupError("ERROR_PY_IMPORT_MISSING", ",".join(missing))
        self._record("python_deps", fp)
        self.ran.append("python_deps")
        self.log.line("PY_DEPS_OK")

    def frontend_deps(self) -> None:
        self._step(2, "Web interface packages")
        fp = fingerprint(
            [
                f"schema={self.pins['SETUP_SCHEMA']}",
                f"node={self.pins['NODE_VERSION']}",
                f"platform={sys.platform}-{platform.machine()}",
            ],
            [self.frontend / "package-lock.json", self.frontend / "package.json", self.frontend / ".npmrc"],
        )
        installed = (self.frontend / "node_modules" / ".package-lock.json").is_file() and self._vite_js().is_file()
        if self.state.get("frontend_deps") == fp and installed:
            self.log.line("FRONTEND_DEPS_CACHED")
            return
        run_with_retries(
            "npm_ci",
            [str(self.node), str(self.npm_cli), "ci", "--no-audit", "--no-fund", f"--registry={NPM_REGISTRY}"],
            log=self.log,
            attempts=3,
            cwd=self.frontend,
            env=node_env(os.environ, self.node_dir, self.runtime),
        )
        if not self._vite_js().is_file():
            raise SetupError("ERROR_FRONTEND_DEPS_INCOMPLETE", str(self._vite_js()))
        self._record("frontend_deps", fp)
        self.ran.append("frontend_deps")
        self.log.line("FRONTEND_DEPS_OK")

    def _vite_js(self) -> Path:
        return self.frontend / "node_modules" / "vite" / "bin" / "vite.js"

    def frontend_build(self) -> None:
        self._step(3, "Web interface build")
        fp = fingerprint(
            [f"schema={self.pins['SETUP_SCHEMA']}", f"node={self.pins['NODE_VERSION']}"],
            frontend_source_files(),
        )
        dist_index = self.frontend / "dist" / "index.html"
        if self.state.get("frontend_build") == fp and dist_index.is_file():
            self.log.line("FRONTEND_BUILD_CACHED")
            return
        # vite.js is run with the private node directly: no shell, no PATH lookup, no shebang.
        run_with_retries(
            "frontend_build",
            [str(self.node), str(self._vite_js()), "build"],
            log=self.log,
            attempts=1,
            cwd=self.frontend,
            env=node_env(os.environ, self.node_dir, self.runtime),
        )
        if not dist_index.is_file():
            raise SetupError("ERROR_FRONTEND_BUILD_NO_DIST", str(dist_index))
        self._record("frontend_build", fp)
        self.ran.append("frontend_build")
        self.log.line("FRONTEND_BUILD_OK")

    def settings_and_preflight(self) -> None:
        self._step(4, "Settings and readiness check")
        if not self.ran and (ROOT / ".env").is_file():
            self.log.line("PREFLIGHT_CACHED")
            return
        returncode = run_logged(
            [sys.executable, "scripts/preflight.py", "--create-env", "--strict-imports", "--skip-tool-probe"],
            log=self.log,
            env=launch_app.build_env(),
        )
        if returncode != 0:
            raise SetupError("ERROR_PREFLIGHT_FAILED", f"returncode={returncode}; see the ERROR lines above")
        self.ran.append("preflight")

    def seed_samples(self) -> None:
        self._step(5, "Sample documents")
        if self.state.get("samples_seeded"):
            self.log.line("SAMPLES_CACHED")
            return
        if (ROOT / "data" / "processed" / "source_manifest.json").is_file():
            # Existing library: never add samples on top of the user's own sources.
            self.log.line("SAMPLES_SKIPPED existing sources found")
        else:
            returncode = run_logged(
                [sys.executable, "scripts/ingest_folder.py", "data/input"],
                log=self.log,
                env=launch_app.build_env(),
            )
            if returncode != 0:
                raise SetupError("ERROR_SAMPLE_INGEST_FAILED", f"returncode={returncode}")
            self.ran.append("samples")
        self._record("samples_seeded", True)

    def run(self) -> list[str]:
        self.check_stage0()
        self.python_deps()
        self.frontend_deps()
        self.frontend_build()
        self.settings_and_preflight()
        self.seed_samples()
        return self.ran


def _reconfigure_streams() -> None:
    # Tool output can contain characters the console code page cannot show (Windows cp1252).
    # Line buffering keeps launcher lines in order with the backend's output when stdout is a pipe (CI, logs).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace", line_buffering=True)  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    parser = argparse.ArgumentParser(
        description="Double-click setup, stage 1. Unrecognized options are passed to scripts/launch_app.py.",
    )
    parser.add_argument("--setup-only", action="store_true", help="Prepare everything, then exit without starting the app.")
    args, launch_args = parser.parse_known_args(argv)

    runtime = runtime_dir()
    log_env = os.environ.get("MUCARO_SETUP_LOG")
    log_path = Path(log_env) if log_env else runtime / "logs" / f"setup-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log = SetupLog(log_path)
    started = time.monotonic()
    try:
        log.line(f"ONE_CLICK_STAGE1_START python={platform.python_version()} root={ROOT}")
        ran = OneClickSetup(log, runtime, read_pins()).run()
        elapsed = time.monotonic() - started
        if ran:
            log.line(f"ONE_CLICK_SETUP_OK steps={','.join(ran)} elapsed={elapsed:.0f}s")
        else:
            log.line(f"ONE_CLICK_FAST_PATH elapsed={elapsed:.1f}s")
    except SetupError as exc:
        log.line("")
        log.line(f"{exc.code} {exc.detail}")
        log.line("")
        log.line("Mucaro AI setup did not finish.")
        log.line(f"Details: {log_path}")
        log.line(
            "Nothing outside this folder was changed. Fix the problem named above (usually the internet "
            "connection), then double-click again; setup resumes where it stopped."
        )
        return 2
    except KeyboardInterrupt:
        log.line("")
        log.line("SETUP_INTERRUPTED double-click again to resume where it stopped")
        return 130
    finally:
        release_lock()
        log.close()
        prune_logs(log_path.parent)

    if args.setup_only:
        print("ONE_CLICK_SETUP_ONLY_OK")
        return 0
    print(f"==> [{TOTAL_STEPS}/{TOTAL_STEPS}] Starting Mucaro AI", flush=True)
    return launch_app.main(LAUNCH_ARGS + launch_args)


if __name__ == "__main__":
    raise SystemExit(main())
