#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.macos_distribution import MacOSDistributionError, verify_signed_notarized_macos_app


def main() -> int:
    parser=argparse.ArgumentParser(description="Verify an exact-head signed and notarized Mucaro macOS app.")
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    try:
        receipt=verify_signed_notarized_macos_app(args.app, expected_head=args.head, output_path=args.output)
    except MacOSDistributionError as exc:
        print(json.dumps({"status":exc.status,"detail":exc.detail},sort_keys=True))
        return 2
    print(json.dumps(receipt,sort_keys=True))
    return 0 if receipt["status"] == "MACOS_DISTRIBUTION_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
