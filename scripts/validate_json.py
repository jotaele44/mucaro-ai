#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    targets = [root / "schemas", root / "data" / "processed", root / "data" / "exports"]
    checked = 0
    try:
        for folder in targets:
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.json")):
                with path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
                checked += 1
                if folder.name == "schemas":
                    jsonschema.validators.validator_for(payload).check_schema(payload)
        print(f"JSON_OK checked={checked}")
        return 0
    except Exception as exc:
        print(f"JSON_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
