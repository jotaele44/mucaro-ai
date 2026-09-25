#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_REQ = ROOT / "backend" / "requirements.txt"
FRONTEND_PACKAGE = ROOT / "frontend" / "package.json"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"
REQUIRED_DIRS = [
    "backend/app",
    "backend/tests",
    "frontend/src",
    "schemas",
    "scripts",
    "docs",
    "data/input",
    "data/processed",
    "data/exports",
    "data/indexes",
]
REQUIRED_FILES = [
    "README.md",
    ".env.example",
    "backend/requirements.txt",
    "backend/app/main.py",
    "scripts/release_check.py",
    "schemas/execution_gate.schema.json",
]
PY_IMPORTS = ["fastapi", "pydantic", "jsonschema", "pytest", "pypdf"]
# .env files copied from older .env.example versions may still pin these retired model ids.
RETIRED_MODEL_IDS = {"claude-3-5-sonnet-latest"}
CURRENT_DEFAULT_MODEL = "claude-sonnet-5"


def status_line(level: str, code: str, detail: str) -> None:
    print(f"{level} {code} {detail}")


def command_version(cmd: str, args: list[str] | None = None) -> tuple[bool, str]:
    executable = shutil.which(cmd)
    if executable is None:
        return False, "not_found"
    try:
        proc = subprocess.run(
            [executable] + (args or ["--version"]),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    output = (proc.stdout or "").strip().splitlines()
    version = output[0] if output else "version_unknown"
    return proc.returncode == 0, version


def warn_retired_env_model(env_file: Path) -> None:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        model = value.split("#", 1)[0].strip().strip("\"'")
        if sep and key.strip() == "MUCARO_AI_MODEL" and model in RETIRED_MODEL_IDS:
            status_line("WARN", "ENV_MODEL_RETIRED", f"{model} in {env_file.name}; set MUCARO_AI_MODEL={CURRENT_DEFAULT_MODEL}")


def ensure_env_file(create_env: bool) -> bool:
    if ENV_FILE.exists():
        status_line("OK", "ENV_PRESENT", str(ENV_FILE))
        warn_retired_env_model(ENV_FILE)
        return True
    if not ENV_EXAMPLE.exists():
        status_line("ERROR", "ENV_EXAMPLE_MISSING", str(ENV_EXAMPLE))
        return False
    if create_env:
        ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        status_line("OK", "ENV_CREATED", str(ENV_FILE))
        return True
    status_line("WARN", "ENV_MISSING", "Run bootstrap to create .env from .env.example")
    return True


def check_paths() -> bool:
    ok = True
    for rel in REQUIRED_DIRS:
        path = ROOT / rel
        if path.is_dir():
            status_line("OK", "DIR_PRESENT", rel)
        else:
            status_line("ERROR", "DIR_MISSING", rel)
            ok = False
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if path.is_file():
            status_line("OK", "FILE_PRESENT", rel)
        else:
            status_line("ERROR", "FILE_MISSING", rel)
            ok = False
    for rel in ["data/input", "data/processed", "data/exports", "data/indexes"]:
        path = ROOT / rel
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            status_line("OK", "DIR_WRITABLE", rel)
        except OSError as exc:
            status_line("ERROR", "DIR_NOT_WRITABLE", f"{rel}: {exc}")
            ok = False
    return ok


def check_python(strict_imports: bool) -> bool:
    ok = True
    version = sys.version_info
    if version >= (3, 11):
        status_line("OK", "PYTHON_VERSION", platform.python_version())
    else:
        status_line("ERROR", "PYTHON_TOO_OLD", f"found={platform.python_version()} required>=3.11")
        ok = False
    in_venv = sys.prefix != sys.base_prefix or bool(os.environ.get("VIRTUAL_ENV"))
    if in_venv:
        status_line("OK", "VENV_ACTIVE", os.environ.get("VIRTUAL_ENV", sys.prefix))
    else:
        status_line("WARN", "VENV_NOT_ACTIVE", "bootstrap will create/use .venv")
    if strict_imports:
        for name in PY_IMPORTS:
            if importlib.util.find_spec(name) is None:
                status_line("ERROR", "PY_IMPORT_MISSING", name)
                ok = False
            else:
                status_line("OK", "PY_IMPORT_PRESENT", name)
    else:
        missing = [name for name in PY_IMPORTS if importlib.util.find_spec(name) is None]
        if missing:
            status_line("WARN", "PY_IMPORTS_NOT_YET_INSTALLED", ",".join(missing))
        else:
            status_line("OK", "PY_IMPORTS_PRESENT", ",".join(PY_IMPORTS))
    return ok


def check_tools(strict_frontend: bool) -> bool:
    ok = True
    for cmd, label in [("git", "GIT"), ("node", "NODE"), ("npm", "NPM")]:
        found, version = command_version(cmd)
        if found:
            status_line("OK", f"{label}_FOUND", version)
        elif cmd in {"node", "npm"} and not strict_frontend:
            status_line("WARN", f"{label}_MISSING", "frontend setup will be skipped until installed")
        else:
            status_line("ERROR", f"{label}_MISSING", f"install {cmd} and rerun bootstrap")
            ok = False
    if FRONTEND_PACKAGE.exists():
        if (ROOT / "frontend" / "node_modules").exists():
            status_line("OK", "FRONTEND_DEPS_PRESENT", "frontend/node_modules")
        else:
            status_line("WARN", "FRONTEND_DEPS_MISSING", "run npm ci from frontend/ or bootstrap")
    return ok


def check_sample_data() -> bool:
    sample = ROOT / "data" / "input" / "sample_mucaro_ai.txt"
    if sample.exists():
        status_line("OK", "SAMPLE_DATA_PRESENT", str(sample.relative_to(ROOT)))
        return True
    status_line("WARN", "SAMPLE_DATA_MISSING", "bootstrap/release_check can still create sample_source_lineage.md")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight checks for Mucaro AI.")
    parser.add_argument("--create-env", action="store_true", help="Create .env from .env.example when missing.")
    parser.add_argument("--strict-imports", action="store_true", help="Fail if Python dependencies are not importable.")
    parser.add_argument("--strict-frontend", action="store_true", help="Fail if Node/npm/frontend dependencies are missing.")
    args = parser.parse_args()

    print(f"PREFLIGHT_ROOT {ROOT}")
    checks = [
        check_paths(),
        ensure_env_file(args.create_env),
        check_python(args.strict_imports),
        check_tools(args.strict_frontend),
        check_sample_data(),
    ]
    if all(checks):
        print("PREFLIGHT_OK")
        return 0
    print("PREFLIGHT_FAILED")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
