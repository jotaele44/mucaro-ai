#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import certify_release as release_certification  # noqa: E402


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    print("RUN", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env or os.environ.copy(), check=True)


def node_first_env(base_env: dict[str, str], node: str) -> dict[str, str]:
    env = base_env.copy()
    node_dir = str(Path(node).parent)
    existing_path = env.get("PATH", "")
    env["PATH"] = f"{node_dir}{os.pathsep}{existing_path}" if existing_path else node_dir
    env["NODE"] = node
    env["npm_config_node"] = node
    return env


def run_frontend_build(env: dict[str, str]) -> None:
    frontend = ROOT / "frontend"
    try:
        run(["npm", "run", "build"], env=env, cwd=frontend)
        return
    except subprocess.CalledProcessError:
        node = release_certification._working_node()
        vite_bin = frontend / "node_modules" / "vite" / "bin" / "vite.js"
        if not node or not vite_bin.exists():
            raise
        fallback_env = node_first_env(env, node)
        run([node, str(vite_bin), "build"], env=fallback_env, cwd=frontend)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT}:{ROOT / 'backend'}"
    sample = ROOT / "data" / "input" / "sample_source_lineage.md"
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_text(
        "# Source Lineage Sample\n\nThis sample explains that every answer must cite retrieved chunk IDs and source IDs. "
        "The Execution Gate enforces planning, readiness scoring, mode selection, approval lock, and run trace before execution.\n",
        encoding="utf-8",
    )
    run([sys.executable, "scripts/preflight.py", "--create-env", "--strict-imports"], env=env)
    run([sys.executable, "scripts/extract_reference_patterns.py"], env=env)
    run([sys.executable, "scripts/validate_json.py"], env=env)
    run([sys.executable, "-m", "pytest", "backend/tests", "-q"], env=env)
    run([sys.executable, "scripts/ingest_folder.py", "data/input"], env=env)
    run([sys.executable, "scripts/ask.py", "How does the Execution Gate protect execution?"], env=env)
    run([sys.executable, "scripts/validate_json.py"], env=env)
    package = ROOT / "frontend" / "package.json"
    node_modules = ROOT / "frontend" / "node_modules"
    if package.exists() and node_modules.exists():
        run_frontend_build(env)
    else:
        print("FRONTEND_BUILD_SKIPPED node_modules_not_installed")
    print("RELEASE_CHECK_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"RELEASE_CHECK_FAILED command={exc.cmd} returncode={exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)
