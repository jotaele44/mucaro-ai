#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.app.packaging import build_desktop_package_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or validate the local desktop package manifest.")
    parser.add_argument("--self-test", action="store_true", help="Validate packaging inputs without writing an artifact.")
    args = parser.parse_args()

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
