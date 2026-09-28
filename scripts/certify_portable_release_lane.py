from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.portable_runtime import PortableRuntimeError, verify_portable_macos_app

SCHEMA_VERSION = "mucaro-portable-release-lane/1.0"
STATUS_OK = "PORTABLE_RELEASE_LANE_VERIFIED"
STATUS_FAILED = "PORTABLE_RELEASE_LANE_BLOCKED"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: expected JSON object")
    return payload


def certify_portable_lane(
    *,
    current_app: Path,
    previous_app: Path,
    runtime_dir: Path,
    clean_machine_receipt: Path,
    expected_head: str,
    output: Path | None = None,
) -> dict:
    current_app = current_app.resolve()
    previous_app = previous_app.resolve()
    runtime_dir = runtime_dir.resolve()
    clean_machine_receipt = clean_machine_receipt.resolve()
    if len(expected_head) != 40:
        raise ValueError("expected_head must be a full 40-character Git SHA")

    current = verify_portable_macos_app(current_app, expected_head=expected_head)
    previous = verify_portable_macos_app(previous_app)
    current_manifest = _json(current_app / "Contents/Resources/portable-manifest.json")
    previous_manifest = _json(previous_app / "Contents/Resources/portable-manifest.json")
    runtime_receipt_path = runtime_dir / "runtime-provenance.json"
    runtime_receipt = _json(runtime_receipt_path)
    clean = _json(clean_machine_receipt)

    clean_evidence = clean.get("evidence") if isinstance(clean.get("evidence"), dict) else {}
    previous_head = str(previous_manifest.get("head_sha") or "")
    checks = {
        "portable_embedded_runtime": (
            current.get("status") == "PORTABLE_APP_VERIFIED"
            and runtime_receipt.get("status") == "PORTABLE_RUNTIME_VERIFIED"
            and current_manifest.get("head_sha") == expected_head
        ),
        "portable_install_reinstall_smoke": (
            clean.get("head_sha") == expected_head
            and clean_evidence.get("install") is True
            and clean_evidence.get("reinstall") is True
        ),
        "distinct_version_upgrade_rollback": (
            bool(previous_head)
            and previous_head != expected_head
            and previous.get("status") == "PORTABLE_APP_VERIFIED"
            and clean_evidence.get("rollback") is True
        ),
        "clean_machine_macos": (
            clean.get("schema_version") == "mucaro-external-release-evidence/1.0"
            and clean.get("status") == "CLEAN_MACHINE_MACOS_VERIFIED"
            and clean_evidence.get("launch") is True
            and clean_evidence.get("representative_workflow") is True
        ),
        "post_launch_bundle_immutable": clean_evidence.get("post_launch_bundle_immutable") is True,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_OK if all(checks.values()) else STATUS_FAILED,
        "head_sha": expected_head,
        "previous_head_sha": previous_head,
        "checks": checks,
        "runtime": {
            "receipt_path": str(runtime_receipt_path),
            "receipt_sha256": _sha256(runtime_receipt_path),
            "status": runtime_receipt.get("status"),
        },
        "current_app": {
            "path": str(current_app),
            "manifest_sha256": current.get("manifest_sha256"),
            "status": current.get("status"),
        },
        "previous_app": {
            "path": str(previous_app),
            "manifest_sha256": previous.get("manifest_sha256"),
            "status": previous.get("status"),
        },
        "clean_machine_receipt": {
            "path": str(clean_machine_receipt),
            "sha256": _sha256(clean_machine_receipt),
            "status": clean.get("status"),
        },
        "limitations": [
            "This certifies the credential-free portable embedded-runtime release lane only.",
            "Developer ID signing and Apple notarization remain separate external gates.",
            "A hosted runner is an isolated fresh runner, but literal physical-device double-click evidence remains a separate release requirement.",
        ],
    }
    if output is not None:
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify the credential-free portable macOS release lane.")
    parser.add_argument("--current-app", type=Path, required=True)
    parser.add_argument("--previous-app", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--clean-machine-receipt", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = certify_portable_lane(
            current_app=args.current_app,
            previous_app=args.previous_app,
            runtime_dir=args.runtime_dir,
            clean_machine_receipt=args.clean_machine_receipt,
            expected_head=args.head,
            output=args.output,
        )
    except (OSError, ValueError, json.JSONDecodeError, PortableRuntimeError) as exc:
        print(json.dumps({"status": STATUS_FAILED, "detail": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
