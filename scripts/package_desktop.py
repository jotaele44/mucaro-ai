#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.packaging import build_desktop_package_manifest, build_macos_app_bundle, verify_macos_app_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or validate the local desktop package manifest.")
    parser.add_argument("--self-test", action="store_true", help="Validate packaging inputs without writing an artifact.")
    parser.add_argument("--build-app", action="store_true", help="Build the local double-clickable macOS Application bundle.")
    parser.add_argument("--verify-app", type=Path, help="Verify an existing macOS Application bundle.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for generated desktop app artifacts.")
    parser.add_argument("--python-executable", help="Python executable to embed in the app launcher.")
    args = parser.parse_args()

    if args.verify_app is not None:
        report = verify_macos_app_bundle(args.verify_app, root=ROOT)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(report["status"])
        return 0 if report["status"] == "MACOS_APP_BUNDLE_VERIFIED" else 2

    if args.build_app:
        report = build_macos_app_bundle(ROOT, output_dir=args.output_dir, python_executable=args.python_executable)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print(report["status"])
        return 0 if report["status"] == "MACOS_APP_BUNDLE_READY" else 2

    manifest = build_desktop_package_manifest(ROOT)
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
