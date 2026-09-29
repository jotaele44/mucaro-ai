from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.portable_runtime import PortableRuntimeError, verify_portable_macos_app
from backend.app.portable_runtime_source import PortableRuntimeSourceError, verify_materialized_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MANIFEST = ROOT / "packaging/runtime/macos-x86_64.json"

SCHEMA_VERSION = "mucaro-portable-release-lane/1.0"
STATUS_OK = "PORTABLE_RELEASE_LANE_VERIFIED"
STATUS_FAILED = "PORTABLE_RELEASE_LANE_BLOCKED"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path}: expected JSON object")
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
    runtime_verification = verify_materialized_runtime(ROOT, runtime_dir, RUNTIME_MANIFEST)
    clean = _json(clean_machine_receipt)

    clean_evidence = clean.get("evidence") if isinstance(clean.get("evidence"), dict) else {}
    clean_lifecycle = clean.get("clean_machine") if isinstance(clean.get("clean_machine"), dict) else {}
    previous_head = str(previous_manifest.get("head_sha") or "")
    current_version = str(current_manifest.get("release_version") or "")
    previous_version = str(previous_manifest.get("release_version") or "")
    checks = {
        "portable_embedded_runtime": (
            current.get("status") == "PORTABLE_APP_VERIFIED"
            and runtime_receipt.get("status") == "PORTABLE_RUNTIME_SOURCE_READY"
            and runtime_verification.get("status") == "PORTABLE_RUNTIME_VERIFIED"
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
            and bool(current_version)
            and bool(previous_version)
            and previous_version != current_version
            and previous.get("status") == "PORTABLE_APP_VERIFIED"
            and clean_evidence.get("rollback") is True
            and clean_lifecycle.get("current_version") == current_version
            and clean_lifecycle.get("previous_version") == previous_version
        ),
        "packaged_lifecycle_macos": (
            clean.get("schema_version") == "mucaro-external-release-evidence/1.0"
            and clean.get("status") == "PACKAGED_LIFECYCLE_VERIFIED"
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
        "release_versions": {
            "current": current_version,
            "previous": previous_version,
        },
        "checks": checks,
        "runtime": {
            "receipt_path": str(runtime_receipt_path),
            "receipt_sha256": _sha256(runtime_receipt_path),
            "source_status": runtime_receipt.get("status"),
            "verification_status": runtime_verification.get("status"),
            "verification_failed_checks": runtime_verification.get("failed_checks"),
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
        "packaged_lifecycle_receipt": {
            "path": str(clean_machine_receipt),
            "sha256": _sha256(clean_machine_receipt),
            "status": clean.get("status"),
        },
        "scope": {
            "runtime_manifest": str(RUNTIME_MANIFEST.relative_to(ROOT)),
            "runtime_architecture": "x86_64",
            "clean_host_certified": False,
            "physical_input_certified": False,
            "signing_notarization_certified": False,
        },
        "limitations": [
            "This certifies the credential-free x86_64 portable embedded-runtime packaged lifecycle on the reported hosted macOS runner only.",
            "It does not independently certify host cleanliness or literal physical-device double-click input.",
            "Developer ID signing, Apple notarization, and the Apple Silicon/arm64 release target remain separate gates.",
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
    except (OSError, TypeError, ValueError, json.JSONDecodeError, PortableRuntimeError, PortableRuntimeSourceError) as exc:
        print(json.dumps({"status": STATUS_FAILED, "detail": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
