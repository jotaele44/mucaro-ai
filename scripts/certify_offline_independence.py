#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.offline_independence import (  # noqa: E402
    STATUS_PASS,
    build_offline_independence_receipt,
    load_runtime_independence_contract,
    write_offline_independence_receipt,
)


def _object(path: Path, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"ERROR_{label}_INVALID {path}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"ERROR_{label}_INVALID {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate externally collected clean-machine evidence against Mucaro's offline-independence contract."
    )
    parser.add_argument("--platform", required=True, help="Bound platform label, e.g. macos-arm64.")
    parser.add_argument("--head", required=True, help="Exact 40-character source HEAD SHA.")
    parser.add_argument("--artifact", type=Path, required=True, help="Prepared release artifact whose bytes are being certified.")
    parser.add_argument(
        "--offline-manifest",
        type=Path,
        required=True,
        help="Offline capability manifest captured from the tested runtime.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="External clean-machine/network-isolation evidence object.",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "_architecture" / "runtime_independence_v1.json",
        help="Runtime independence contract.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Receipt destination.")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        help="Override/adopt a required offline capability for this platform release; repeat as needed.",
    )
    args = parser.parse_args()

    artifact = args.artifact.expanduser().resolve()
    if not artifact.is_file():
        print(f"ERROR_OFFLINE_INDEPENDENCE_ARTIFACT_MISSING {artifact}", file=sys.stderr)
        return 2

    contract = load_runtime_independence_contract(args.contract)
    manifest = _object(args.offline_manifest, "OFFLINE_MANIFEST")
    evidence = _object(args.evidence, "OFFLINE_EVIDENCE")
    receipt = build_offline_independence_receipt(
        contract=contract,
        offline_manifest=manifest,
        evidence=evidence,
        platform=args.platform,
        head_sha=args.head,
        artifact_sha256=_sha256(artifact),
        declared_capabilities=args.capabilities,
    )
    output = write_offline_independence_receipt(receipt, args.output)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "platform": receipt["platform"],
                "head_sha": receipt["head_sha"],
                "artifact_sha256": receipt["artifact_sha256"],
                "blocking": receipt["blocking"],
                "receipt_sha256": receipt["receipt_sha256"],
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == STATUS_PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
