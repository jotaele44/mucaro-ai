from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .portable_runtime import PortableRuntimeError, verify_portable_macos_app

EXTERNAL_EVIDENCE_SCHEMA = "mucaro-external-release-evidence/1.0"
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class MacOSDistributionError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )


def _run(command: list[str], runner: Runner) -> dict[str, Any]:
    try:
        completed = runner(command)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "returncode": None, "status": "FAIL", "output": str(exc)[:65536]}
    output = str(completed.stdout or "")[:65536]
    return {
        "command": command,
        "returncode": completed.returncode,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "output": output,
    }


def verify_signed_notarized_macos_app(
    app_path: str | Path,
    *,
    expected_head: str,
    output_path: str | Path | None = None,
    platform_name: str = sys.platform,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    if platform_name != "darwin":
        raise MacOSDistributionError(
            "ERROR_MACOS_DISTRIBUTION_PLATFORM",
            "signed/notarized macOS verification must execute on macOS",
        )
    app = Path(app_path).expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise MacOSDistributionError("ERROR_MACOS_DISTRIBUTION_APP", str(app))
    if not isinstance(expected_head, str) or len(expected_head) != 40:
        raise MacOSDistributionError("ERROR_MACOS_DISTRIBUTION_HEAD", "expected_head must be a full 40-character Git SHA")

    try:
        structural = verify_portable_macos_app(app, expected_head=expected_head)
    except PortableRuntimeError as exc:
        raise MacOSDistributionError(exc.status, exc.detail) from exc
    if structural.get("status") != "PORTABLE_APP_VERIFIED":
        raise MacOSDistributionError(
            "ERROR_MACOS_DISTRIBUTION_STRUCTURE",
            ",".join(structural.get("failed_checks") or []),
        )

    checks = {
        "codesign_verify": _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], runner),
        "codesign_details": _run(["codesign", "-dv", "--verbose=4", str(app)], runner),
        "gatekeeper_assess": _run(["spctl", "--assess", "--type", "execute", "--verbose=4", str(app)], runner),
        "notarization_staple": _run(["xcrun", "stapler", "validate", str(app)], runner),
    }
    signed = checks["codesign_verify"]["status"] == "PASS" and checks["codesign_details"]["status"] == "PASS"
    notarized = checks["gatekeeper_assess"]["status"] == "PASS" and checks["notarization_staple"]["status"] == "PASS"
    installer_verified = structural.get("status") == "PORTABLE_APP_VERIFIED"

    receipt: dict[str, Any] = {
        "schema_version": EXTERNAL_EVIDENCE_SCHEMA,
        "head_sha": expected_head,
        "evidence": {
            "signed": signed,
            "notarized": notarized,
            "installer_verified": installer_verified,
        },
        "macos_distribution": {
            "app_path": str(app),
            "portable_manifest_sha256": structural.get("manifest_sha256"),
            "checks": checks,
        },
        "basis": (
            "verification-only receipt: codesign integrity/details, Gatekeeper assessment, "
            "stapled notarization validation, and exact-head portable bundle verification"
        ),
        "limitations": [
            "This verifier does not sign the application or submit it to Apple notarization.",
            "Apple Developer credentials and signing identities remain operator-controlled final-stage inputs.",
            "A passing receipt applies only to the exact app bytes and expected Git head verified on this Mac.",
        ],
    }
    receipt["status"] = "MACOS_DISTRIBUTION_VERIFIED" if signed and notarized and installer_verified else "MACOS_DISTRIBUTION_BLOCKED"
    if output_path is not None:
        target = Path(output_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt["output_path"] = str(target)
    return receipt
