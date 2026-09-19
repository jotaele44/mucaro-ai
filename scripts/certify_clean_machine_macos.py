#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.clean_machine_macos import CleanMachineMacOSError, certify_clean_machine_macos


def main() -> int:
    parser=argparse.ArgumentParser(description="Run distinct-version clean-machine Mucaro macOS certification.")
    parser.add_argument("--current-app", type=Path, required=True)
    parser.add_argument("--previous-app", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    args=parser.parse_args()
    try:
        receipt=certify_clean_machine_macos(args.current_app,args.previous_app,expected_head=args.head,install_root=args.install_root,output_path=args.output,port=args.port)
    except CleanMachineMacOSError as exc:
        print(json.dumps({"status":exc.status,"detail":exc.detail},sort_keys=True))
        return 2
    print(json.dumps(receipt,sort_keys=True))
    return 0 if receipt["status"] == "CLEAN_MACHINE_MACOS_VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
