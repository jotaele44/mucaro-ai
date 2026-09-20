from __future__ import annotations

import json
import plistlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from backend.app.macos_app_store import build_macos_app_store_readiness

SOURCE_ROOT = Path(__file__).resolve().parents[2]
POLICY_FILES = (
    "_architecture/macos_app_store_security_profile.json",
    "packaging/macos/MucaroAI-AppStore.entitlements",
    "packaging/macos/PrivacyInfo.xcprivacy",
    "docs/APPLE_APP_STORE_SECURITY.md",
)


def _write_policy_fixture(root: Path) -> None:
    for relative in POLICY_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SOURCE_ROOT / relative).read_bytes())
    packaging_path = root / "backend" / "app" / "packaging.py"
    packaging_path.parent.mkdir(parents=True, exist_ok=True)
    packaging_path.write_text(
        'REPO_ROOT = "fixture"\nSCOPE = "source_bound_local_macos_app_wrapper"\n',
        encoding="utf-8",
    )


def _write_candidate(root: Path) -> Path:
    app = root / "Mucaro AI.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ai.mucaro.app",
                "CFBundleName": "Mucaro AI",
                "CFBundleExecutable": "MucaroAI",
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": "0.1.23",
                "CFBundleVersion": "1",
                "LSMinimumSystemVersion": "13.0",
                "NSHumanReadableCopyright": "Copyright Mucaro AI",
                "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": False},
            }
        )
    )
    (resources / "PrivacyInfo.xcprivacy").write_bytes(
        (SOURCE_ROOT / "packaging" / "macos" / "PrivacyInfo.xcprivacy").read_bytes()
    )
    executable = contents / "MacOS" / "MucaroAI"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (contents / "embedded.provisionprofile").write_bytes(b"fixture-profile")
    (resources / "mucaro-app-store-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "mucaro_mac_app_store_candidate_receipt_v1",
                "scope": "self_contained_mac_app_store_candidate",
                "build_profile": "mac_app_store",
                "bundle_identifier": "ai.mucaro.app",
                "source_bound": False,
                "runtime_origin": "embedded",
                "source_head_sha": "a" * 40,
            }
        ),
        encoding="utf-8",
    )
    return app


def _runner(
    *,
    extra_entitlements: dict[str, object] | None = None,
    provisioning_updates: dict[str, object] | None = None,
):
    required_entitlements = {
        "com.apple.security.app-sandbox": True,
        "com.apple.security.files.user-selected.read-write": True,
        "com.apple.security.files.bookmarks.app-scope": True,
        "com.apple.security.network.client": True,
        "com.apple.application-identifier": "TEAM123.ai.mucaro.app",
        "com.apple.developer.team-identifier": "TEAM123",
    }
    required_entitlements.update(extra_entitlements or {})
    provisioning = {
        "Name": "Mucaro AI Mac App Store",
        "TeamIdentifier": ["TEAM123"],
        "ExpirationDate": datetime(2099, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        "Entitlements": {
            "com.apple.application-identifier": "TEAM123.ai.mucaro.app",
            "com.apple.security.app-sandbox": True,
            "com.apple.security.get-task-allow": False,
        },
    }
    provisioning.update(provisioning_updates or {})

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[0] == "security":
            output = plistlib.dumps(provisioning).decode("utf-8")
        elif "--entitlements" in command:
            output = plistlib.dumps(required_entitlements).decode("utf-8")
        elif "-dv" in command:
            output = (
                "Identifier=ai.mucaro.app\n"
                "Authority=Apple Distribution: Example (TEAM123)\n"
                "TeamIdentifier=TEAM123\n"
            )
        else:
            output = "valid on disk\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    return run


def test_source_policy_is_open_without_candidate(tmp_path: Path):
    _write_policy_fixture(tmp_path)

    report = build_macos_app_store_readiness(tmp_path)
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_OPEN"
    assert report["counts"]["fail"] == 0
    assert checks["current_local_wrapper_store_eligibility"]["status"] == "OPEN"
    assert checks["candidate_bundle"]["status"] == "OPEN"
    assert {gate["state"] for gate in report["external_gates"]} == {"BLOCKED"}


def test_compliant_candidate_is_only_provisional(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_CANDIDATE_PROVISIONAL"
    assert report["counts"]["fail"] == 0
    assert checks["candidate_receipt"]["status"] == "PASS"
    assert checks["candidate_executable"]["status"] == "PASS"
    assert checks["candidate_signed_identity_entitlements"]["status"] == "PASS"
    assert checks["candidate_provisioning_application_id"]["status"] == "PASS"
    assert checks["candidate_provisioning_team"]["status"] == "PASS"
    assert checks["candidate_forbidden_entitlements"]["status"] == "PASS"


def test_candidate_rejects_forbidden_entitlement(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(extra_entitlements={"com.apple.security.cs.allow-jit": True}),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_forbidden_entitlements"]["status"] == "FAIL"
    assert "com.apple.security.cs.allow-jit" in checks["candidate_forbidden_entitlements"]["detail"]


def test_candidate_rejects_source_bound_local_receipt(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)
    (app / "Contents" / "Resources" / "mucaro-app-receipt.json").write_text(
        json.dumps({"scope": "source_bound_local_macos_app_wrapper"}),
        encoding="utf-8",
    )

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_source_bound_marker"]["status"] == "FAIL"


def test_candidate_rejects_development_provisioning_profile(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(provisioning_updates={"ProvisionedDevices": ["DEVICE-ID"]}),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_provisioning_distribution"]["status"] == "FAIL"


def test_missing_security_profile_fails_closed(tmp_path: Path):
    report = build_macos_app_store_readiness(tmp_path)

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert report["failed_checks"] == ["security_profile"]


def test_profile_rejects_bundle_path_escape(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    profile_path = tmp_path / "_architecture" / "macos_app_store_security_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["bundle_requirements"]["privacy_manifest_relative_path"] = "../PrivacyInfo.xcprivacy"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    report = build_macos_app_store_readiness(tmp_path)
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["bundle_requirement_contract"]["status"] == "FAIL"
