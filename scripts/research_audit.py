"""Offline development entry point; never issues release certification."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.artifact_identity import sha256_bytes
from backend.app.research_benchmark import prepare_blind_pairs, summarize_reviews
from backend.app.research_evidence import audit_packet, read_json_strict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("packet", type=Path)
    audit.add_argument("--root", type=Path, required=True)
    blind = sub.add_parser("blind")
    blind.add_argument("cases", type=Path)
    blind.add_argument("--private-key", type=Path, required=True)
    review = sub.add_parser("review")
    review.add_argument("packet", type=Path)
    review.add_argument("reviews", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "audit":
            packet_bytes = args.packet.read_bytes()
            result = audit_packet(args.root, read_json_strict(packet_bytes))
            result["packet_sha256"] = sha256_bytes(packet_bytes)
        elif args.command == "blind":
            result, key = prepare_blind_pairs(read_json_strict(args.cases.read_bytes()))
            # Exclusive creation prevents accidentally replacing an earlier sealed mapping.
            fd = os.open(args.private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(key, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            result = summarize_reviews(
                read_json_strict(args.packet.read_bytes()), read_json_strict(args.reviews.read_bytes())
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return 1 if result.get("integrity_status") == "FAIL" else 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc), "release_certification": "WITHHELD"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
