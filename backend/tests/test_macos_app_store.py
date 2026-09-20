from __future__ import annotations

import hashlib
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
    "_architecture/schemas/macos-app-store-candidate-receipt.schema.json",
    "_architecture/schemas/macos-app-store-vulnerability-scan.schema.json",
    "_architecture/schemas/macos-app-store-malware-scan.schema.json",
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
    executable_sha256 = hashlib.sha256(executable.read_bytes()).hexdigest()
    (contents / "embedded.provisionprofile").write_bytes(b"fixture-profile")
    security_dir = resources / "Security"
    security_dir.mkdir()
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sbom_path = security_dir / "mucaro-sbom.cdx.json"
    vulnerability_path = security_dir / "mucaro-vulnerability-scan.json"
    malware_path = security_dir / "mucaro-malware-scan.json"
    sbom_path.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "serialNumber": "urn:uuid:8c269718-d281-4fed-9c44-c32a99395fe0",
                "metadata": {
                    "component": {"type": "application", "name": "mucaro-ai", "version": "0.1.23"},
                    "properties": [
                        {"name": "mucaro:source-head-sha", "value": "a" * 40},
                        {"name": "mucaro:bundle-identifier", "value": "ai.mucaro.app"},
                        {"name": "mucaro:candidate-executable-sha256", "value": executable_sha256},
                    ],
                },
                "components": [
                    {
                        "type": "library",
                        "name": "fastapi",
                        "version": "0.128.2",
                        "purl": "pkg:pypi/fastapi@0.128.2",
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    vulnerability_path.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_mac_app_store_vulnerability_scan_v1",
                "status": "PASS",
                "scope": "exact_mac_app_store_candidate",
                "source_head_sha": "a" * 40,
                "bundle_identifier": "ai.mucaro.app",
                "app_version": "0.1.23",
                "candidate_executable_sha256": executable_sha256,
                "scanned_at": now,
                "scanner": {"name": "fixture-vulnerability-scanner", "version": "1.0", "database_updated_at": now},
                "summary": {
                    "critical": 0,
                    "high": 0,
                    "moderate": 0,
                    "low": 0,
                    "unknown": 0,
                    "total": 0,
                    "known_patchable": 0,
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    malware_path.write_text(
        json.dumps(
            {
                "schema_version": "mucaro_mac_app_store_malware_scan_v1",
                "status": "PASS",
                "scope": "exact_mac_app_store_candidate",
                "source_head_sha": "a" * 40,
                "bundle_identifier": "ai.mucaro.app",
                "app_version": "0.1.23",
                "candidate_executable_sha256": executable_sha256,
                "scanned_at": now,
                "scanner": {"name": "fixture-malware-scanner", "version": "1.0", "signatures_updated_at": now},
                "summary": {"files_scanned": 12, "detections": 0, "errors": 0},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
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
                "security_evidence": {
                    "sbom": {
                        "relative_path": "Contents/Resources/Security/mucaro-sbom.cdx.json",
                        "sha256": hashlib.sha256(sbom_path.read_bytes()).hexdigest(),
                    },
                    "vulnerability_scan": {
                        "relative_path": "Contents/Resources/Security/mucaro-vulnerability-scan.json",
                        "sha256": hashlib.sha256(vulnerability_path.read_bytes()).hexdigest(),
                    },
                    "malware_scan": {
                        "relative_path": "Contents/Resources/Security/mucaro-malware-scan.json",
                        "sha256": hashlib.sha256(malware_path.read_bytes()).hexdigest(),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return app


def _rewrite_security_evidence(app: Path, name: str, mutate) -> None:
    receipt_path = app / "Contents" / "Resources" / "mucaro-app-store-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["security_evidence"][name]
    evidence_path = app / evidence["relative_path"]
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    mutate(payload)
    evidence_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


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
    assert checks["candidate_security_evidence_hashes"]["status"] == "PASS"
    assert checks["candidate_sbom_contract"]["status"] == "PASS"
    assert checks["candidate_vulnerability_scan_contract"]["status"] == "PASS"
    assert checks["candidate_malware_scan_contract"]["status"] == "PASS"
    assert checks["candidate_security_scan_freshness"]["status"] == "PASS"


def test_candidate_rejects_tampered_security_evidence(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)
    sbom_path = app / "Contents" / "Resources" / "Security" / "mucaro-sbom.cdx.json"
    sbom_path.write_text(sbom_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_security_evidence_hashes"]["status"] == "FAIL"
    assert "sbom" in checks["candidate_security_evidence_hashes"]["detail"]


def test_candidate_rejects_oversized_security_evidence(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    profile_path = tmp_path / "_architecture" / "macos_app_store_security_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["supply_chain_requirements"]["maximum_evidence_file_bytes"]["sbom"] = 1
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    app = _write_candidate(tmp_path)

    report = build_macos_app_store_readiness(tmp_path, app_path=app, platform_name="darwin", runner=_runner())
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_security_evidence_sizes"]["status"] == "FAIL"
    assert "sbom" in checks["candidate_security_evidence_sizes"]["detail"]


def test_candidate_rejects_known_patchable_vulnerability(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    def add_finding(payload: dict[str, object]) -> None:
        summary = payload["summary"]
        assert isinstance(summary, dict)
        summary["critical"] = 1
        summary["total"] = 1
        summary["known_patchable"] = 1

    _rewrite_security_evidence(app, "vulnerability_scan", add_finding)

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_security_evidence_hashes"]["status"] == "PASS"
    assert checks["candidate_vulnerability_scan_contract"]["status"] == "FAIL"


def test_candidate_rejects_stale_security_scan(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    def make_stale(payload: dict[str, object]) -> None:
        payload["scanned_at"] = "2020-01-01T00:00:00Z"

    _rewrite_security_evidence(app, "malware_scan", make_stale)

    report = build_macos_app_store_readiness(
        tmp_path,
        app_path=app,
        platform_name="darwin",
        runner=_runner(),
    )
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_security_scan_freshness"]["status"] == "FAIL"
    assert "malware_scanned_at" in checks["candidate_security_scan_freshness"]["detail"]


def test_candidate_rejects_malware_detection(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    def add_detection(payload: dict[str, object]) -> None:
        summary = payload["summary"]
        assert isinstance(summary, dict)
        summary["detections"] = 1

    _rewrite_security_evidence(app, "malware_scan", add_detection)
    report = build_macos_app_store_readiness(tmp_path, app_path=app, platform_name="darwin", runner=_runner())
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_malware_scan_contract"]["status"] == "FAIL"


def test_candidate_rejects_cross_build_scan_evidence(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    app = _write_candidate(tmp_path)

    def change_executable_binding(payload: dict[str, object]) -> None:
        payload["candidate_executable_sha256"] = "b" * 64

    _rewrite_security_evidence(app, "vulnerability_scan", change_executable_binding)
    report = build_macos_app_store_readiness(tmp_path, app_path=app, platform_name="darwin", runner=_runner())
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["candidate_security_evidence_hashes"]["status"] == "PASS"
    assert checks["candidate_vulnerability_scan_contract"]["status"] == "FAIL"


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


def test_profile_rejects_supply_chain_path_escape(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    profile_path = tmp_path / "_architecture" / "macos_app_store_security_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["supply_chain_requirements"]["sbom_relative_path"] = "../mucaro-sbom.cdx.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    report = build_macos_app_store_readiness(tmp_path)
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["supply_chain_requirement_contract"]["status"] == "FAIL"


def test_profile_rejects_invalid_supply_chain_schema(tmp_path: Path):
    _write_policy_fixture(tmp_path)
    schema_path = tmp_path / "_architecture" / "schemas" / "macos-app-store-malware-scan.schema.json"
    schema_path.write_text(json.dumps({"type": "not-a-json-schema-type"}), encoding="utf-8")

    report = build_macos_app_store_readiness(tmp_path)
    checks = {check["name"]: check for check in report["checks"]}

    assert report["status"] == "MAC_APP_STORE_READINESS_FAIL"
    assert checks["supply_chain_schemas"]["status"] == "FAIL"
