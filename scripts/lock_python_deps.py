#!/usr/bin/env python3
"""Generate the hash-pinned Python lock files the double-click setup installs from.

One lock per supported platform, in packaging/one_click/requirements-<target>.lock, each listing
every package (and every file hash) the setup installs on that platform: the project's
dependencies, the extras the setup asks for there (scripts/one_click.py PYTHON_EXTRAS), and
setuptools, which builds the editable project install. They are compiled with the pinned uv
(scripts/one_click_pins.env UV_VERSION) for the pinned Python (PYTHON_VERSION), ignoring anything
published after PY_EXCLUDE_NEWER, so the same inputs always give the same files.

    python scripts/lock_python_deps.py           # regenerate the locks (needs the network)
    python scripts/lock_python_deps.py --check   # regenerate into a temp folder; fail unless identical

The pinned uv is taken from --uv, then MUCARO_UV_BIN, then the copy the double-click setup keeps in
.mucaro-runtime/uv/<UV_VERSION>/. Run the setup once (or pass --uv) before using this script.
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS_FILE = ROOT / "scripts" / "one_click_pins.env"
LOCK_DIR = ROOT / "packaging" / "one_click"
BUILD_REQUIREMENTS = LOCK_DIR / "build-requirements.in"
COMPILE_COMMAND = "python scripts/lock_python_deps.py"
# uv --python-platform target -> extras the setup installs there (desktop draws the native window).
TARGETS = {
    "aarch64-apple-darwin": ("dev", "anthropic", "desktop"),
    "x86_64-apple-darwin": ("dev", "anthropic", "desktop"),
    "x86_64-unknown-linux-gnu": ("dev", "anthropic"),
    "x86_64-pc-windows-msvc": ("dev", "anthropic", "desktop"),
}


def lock_path(target: str, folder: Path = LOCK_DIR) -> Path:
    return folder / f"requirements-{target}.lock"


def read_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in PINS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            pins[key] = value
    return pins


def find_uv(explicit: str | None, version: str) -> Path:
    exe = ".exe" if os.name == "nt" else ""
    candidates = [explicit, os.environ.get("MUCARO_UV_BIN"), str(ROOT / ".mucaro-runtime" / "uv" / version / f"uv{exe}")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            uv = Path(candidate)
            reported = subprocess.run([str(uv), "--version"], capture_output=True, text=True, check=True).stdout.split()
            if len(reported) < 2 or reported[1] != version:
                raise SystemExit(f"ERROR_UV_VERSION {uv} is {' '.join(reported)}; the locks need uv {version}")
            return uv
    raise SystemExit(f"ERROR_UV_MISSING run the double-click setup once, or pass --uv PATH to uv {version}")


def compile_lock(uv: Path, pins: dict[str, str], target: str, output: Path) -> None:
    extras = [f"--extra={extra}" for extra in TARGETS[target]]
    env = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
    env["UV_NO_CONFIG"] = "1"
    security_args: list[str] = []
    for package, version_key, cutoff_key in (
        ("certifi", "PY_SECURITY_CERTIFI_VERSION", "PY_EXCLUDE_NEWER_CERTIFI"),
        ("pillow", "PY_SECURITY_PILLOW_VERSION", "PY_EXCLUDE_NEWER_PILLOW"),
        ("pypdf", "PY_SECURITY_PYPDF_VERSION", "PY_EXCLUDE_NEWER_PYPDF"),
    ):
        version = pins.get(version_key)
        cutoff = pins.get(cutoff_key)
        if version and cutoff:
            security_args.extend(
                [
                    "--exclude-newer-package",
                    f"{package}={cutoff}",
                    "--upgrade-package",
                    f"{package}=={version}",
                ]
            )
    subprocess.run(
        [
            str(uv),
            "pip",
            "compile",
            "pyproject.toml",
            str(BUILD_REQUIREMENTS.relative_to(ROOT)),
            *extras,
            "--python-version",
            pins["PYTHON_VERSION"],
            "--python-platform",
            target,
            "--generate-hashes",
            "--exclude-newer",
            pins["PY_EXCLUDE_NEWER"],
            *security_args,
            "--custom-compile-command",
            COMPILE_COMMAND,
            "--quiet",
            "-o",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Regenerate into a temp folder and compare with the committed locks.")
    parser.add_argument("--uv", help="Path to the pinned uv (default: MUCARO_UV_BIN, then .mucaro-runtime/uv/<UV_VERSION>/).")
    args = parser.parse_args(argv)

    pins = read_pins()
    uv = find_uv(args.uv, pins["UV_VERSION"])
    if not args.check:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        for target in TARGETS:
            compile_lock(uv, pins, target, lock_path(target))
            print(f"LOCKED {lock_path(target).relative_to(ROOT)}")
        print("PYTHON_LOCKS_OK")
        return 0

    ok = True
    with tempfile.TemporaryDirectory(prefix="mucaro-locks-") as tmp:
        for target in TARGETS:
            fresh = lock_path(target, Path(tmp))
            compile_lock(uv, pins, target, fresh)
            committed = lock_path(target)
            same = committed.is_file() and committed.read_bytes() == fresh.read_bytes()
            ok = ok and same
            print(f"{'MATCH' if same else 'MISMATCH'} {committed.relative_to(ROOT)}")
            if not same:
                committed_text = committed.read_text(encoding="utf-8").splitlines(keepends=True) if committed.is_file() else []
                fresh_text = fresh.read_text(encoding="utf-8").splitlines(keepends=True)
                diff = difflib.unified_diff(
                    committed_text,
                    fresh_text,
                    fromfile=str(committed.relative_to(ROOT)),
                    tofile=f"generated/{fresh.name}",
                    n=3,
                )
                for index, line in enumerate(diff):
                    if index >= 200:
                        print("... DIFF_TRUNCATED ...")
                        break
                    print(line, end="")
    print("PYTHON_LOCKS_REPRODUCIBLE_OK" if ok else "PYTHON_LOCKS_REPRODUCIBLE_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
