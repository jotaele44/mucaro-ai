#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def build_manifest() -> dict:
    version = load_version()
    required = [
        "scripts/launch_app.py",
        "scripts/bootstrap_macos.sh",
        "scripts/bootstrap_windows.ps1",
        "frontend/package-lock.json",
        "pyproject.toml",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    return {
        "status": "DESKTOP_PACKAGE_READY" if not missing else "DESKTOP_PACKAGE_INCOMPLETE",
        "version": version,
        "entrypoint": "scripts/launch_app.py",
        "required_files": required,
        "missing_files": missing,
        "outputs": ["frontend/dist", "local Python editable install", "launcher smoke path"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or validate the local desktop package manifest.")
    parser.add_argument("--self-test", action="store_true", help="Validate packaging inputs without writing an artifact.")
    args = parser.parse_args()

    manifest = build_manifest()
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    if manifest["status"] != "DESKTOP_PACKAGE_READY":
        return 2
    if not args.self_test:
        target = ROOT / "data" / "exports" / f"desktop_package_{manifest['version']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"DESKTOP_PACKAGE_MANIFEST_WRITTEN path={target}")
    print(manifest["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
