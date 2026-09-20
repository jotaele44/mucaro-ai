from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROFILE_SCHEMA_VERSION = "mucaro_macos_app_store_security_profile_v1"
PROFILE_PATH = Path("_architecture") / "macos_app_store_security_profile.json"
ENTITLEMENTS_TEMPLATE_PATH = Path("packaging") / "macos" / "MucaroAI-AppStore.entitlements"
PRIVACY_TEMPLATE_PATH = Path("packaging") / "macos" / "PrivacyInfo.xcprivacy"
ALLOWED_GATE_STATES = {"PASS", "FAIL", "OPEN", "BLOCKED", "PROVISIONAL"}
EXTERNAL_GATE_IDS = {"MAS-SIGNING-001", "MAS-UPLOAD-001", "MAS-REVIEW-001"}
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected JSON object: {path}")
    return payload


def _load_plist(path: Path) -> dict[str, Any]:
    payload = plistlib.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise TypeError(f"expected property-list dictionary: {path}")
    return payload


def _run(command: list[str], runner: Runner) -> dict[str, Any]:
    try:
        completed = runner(command)
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "command": command,
            "returncode": None,
            "status": "FAIL",
            "output": str(exc)[:65536],
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "output": str(completed.stdout or "")[:65536],
    }


def _extract_plist(text: str) -> dict[str, Any]:
    start = text.find("<?xml")
    end_marker = "</plist>"
    end = text.rfind(end_marker)
    if start < 0 or end < start:
        raise ValueError("codesign output did not contain an XML entitlement plist")
    payload = plistlib.loads(text[start : end + len(end_marker)].encode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("codesign entitlement plist must be a dictionary")
    return payload


def _bundle_relative_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] != "Contents":
        return None
    return path


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _git_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _escaping_symlinks(app: Path) -> list[str]:
    app_root = app.resolve()
    escaping: list[str] = []
    for path in app.rglob("*"):
        if not path.is_symlink():
            continue
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(app_root)
        except ValueError:
            escaping.append(str(path.relative_to(app)))
    return sorted(escaping)


def _validate_source_profile(root: Path, profile: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "profile_schema",
            "PASS" if profile.get("schema_version") == PROFILE_SCHEMA_VERSION else "FAIL",
            str(profile.get("schema_version")),
        )
    )
    checks.append(
        _check(
            "profile_identity",
            "PASS" if profile.get("profile_id") == "mac_app_store" else "FAIL",
            str(profile.get("profile_id")),
        )
    )
    expected_bundle_identifier = profile.get("expected_bundle_identifier")
    checks.append(
        _check(
            "bundle_identifier_policy",
            "PASS" if isinstance(expected_bundle_identifier, str) and expected_bundle_identifier else "FAIL",
            str(expected_bundle_identifier),
        )
    )

    gates = profile.get("gates")
    gate_rows = gates if isinstance(gates, list) else []
    gate_ids = [row.get("id") for row in gate_rows if isinstance(row, dict)]
    gate_states = [row.get("state") for row in gate_rows if isinstance(row, dict)]
    external_rows = [row for row in gate_rows if isinstance(row, dict) and row.get("id") in EXTERNAL_GATE_IDS]
    checks.extend(
        [
            _check("gate_rows", "PASS" if gate_rows and len(gate_rows) == len(gates or []) else "FAIL", f"count={len(gate_rows)}"),
            _check("gate_ids_unique", "PASS" if len(gate_ids) == len(set(gate_ids)) else "FAIL", f"count={len(gate_ids)}"),
            _check("gate_states", "PASS" if gate_states and set(gate_states) <= ALLOWED_GATE_STATES else "FAIL", ",".join(sorted({str(state) for state in gate_states}))),
            _check(
                "external_gates_not_preclaimed",
                "PASS" if {row.get("id") for row in external_rows} == EXTERNAL_GATE_IDS and all(row.get("state") == "BLOCKED" for row in external_rows) else "FAIL",
                "signing, upload, and review must all exist and remain BLOCKED without external evidence",
            ),
        ]
    )

    bundle_requirements = profile.get("bundle_requirements")
    bundle_requirements = bundle_requirements if isinstance(bundle_requirements, dict) else {}
    required_info_keys = bundle_requirements.get("required_info_plist_keys")
    required_info_keys = required_info_keys if isinstance(required_info_keys, list) else []
    privacy_relative_path = _bundle_relative_path(bundle_requirements.get("privacy_manifest_relative_path"))
    provision_relative_path = _bundle_relative_path(bundle_requirements.get("provisioning_profile_relative_path"))
    receipt_relative_path = _bundle_relative_path(bundle_requirements.get("candidate_receipt_relative_path"))
    bundle_contract_valid = (
        bool(bundle_requirements)
        and privacy_relative_path is not None
        and provision_relative_path is not None
        and receipt_relative_path is not None
        and bool(required_info_keys)
        and len(required_info_keys) == len(set(required_info_keys))
        and all(_nonempty_string(key) for key in required_info_keys)
        and bundle_requirements.get("reject_source_bound_wrapper") is True
        and bundle_requirements.get("reject_escaping_symlinks") is True
        and bundle_requirements.get("reject_arbitrary_transport_loads") is True
    )
    checks.append(
        _check(
            "bundle_requirement_contract",
            "PASS" if bundle_contract_valid else "FAIL",
            "bundle-relative paths, required Info.plist keys, containment, and transport policy",
        )
    )

    references = profile.get("apple_references")
    reference_rows = references if isinstance(references, list) else []
    checks.append(
        _check(
            "official_apple_references",
            "PASS" if reference_rows and all(isinstance(url, str) and url.startswith("https://developer.apple.com/") for url in reference_rows) else "FAIL",
            f"count={len(reference_rows)}",
        )
    )

    required_entitlements = profile.get("required_entitlements")
    conditional_entitlements = profile.get("conditionally_allowed_entitlements")
    forbidden_entitlements = profile.get("forbidden_entitlements")
    entitlement_template_path = root / ENTITLEMENTS_TEMPLATE_PATH
    privacy_template_path = root / PRIVACY_TEMPLATE_PATH
    entitlement_template: dict[str, Any] = {}
    privacy_template: dict[str, Any] = {}
    try:
        entitlement_template = _load_plist(entitlement_template_path)
        entitlement_error = None
    except (OSError, TypeError, ValueError, plistlib.InvalidFileException) as exc:
        entitlement_error = str(exc)
    try:
        privacy_template = _load_plist(privacy_template_path)
        privacy_error = None
    except (OSError, TypeError, ValueError, plistlib.InvalidFileException) as exc:
        privacy_error = str(exc)

    entitlement_contract_valid = (
        isinstance(required_entitlements, dict)
        and required_entitlements.get("com.apple.security.app-sandbox") is True
        and required_entitlements.get("com.apple.security.files.user-selected.read-write") is True
        and isinstance(conditional_entitlements, list)
        and isinstance(forbidden_entitlements, list)
        and "com.apple.security.get-task-allow" in forbidden_entitlements
        and "com.apple.security.cs.allow-jit" in forbidden_entitlements
        and "com.apple.security.files.user-selected.executable" in forbidden_entitlements
        and not (set(required_entitlements) & set(conditional_entitlements))
        and not (set(required_entitlements) & set(forbidden_entitlements))
        and not (set(conditional_entitlements) & set(forbidden_entitlements))
        and len(conditional_entitlements) == len(set(conditional_entitlements))
        and len(forbidden_entitlements) == len(set(forbidden_entitlements))
    )
    checks.extend(
        [
            _check("entitlement_contract", "PASS" if entitlement_contract_valid else "FAIL", "sandbox, user-selected files, and dangerous-entitlement policy"),
            _check("entitlement_template_parse", "PASS" if entitlement_error is None else "FAIL", entitlement_error or str(ENTITLEMENTS_TEMPLATE_PATH)),
            _check("entitlement_template_exact", "PASS" if isinstance(required_entitlements, dict) and entitlement_template == required_entitlements else "FAIL", "template must equal required entitlement map"),
            _check("privacy_template_parse", "PASS" if privacy_error is None else "FAIL", privacy_error or str(PRIVACY_TEMPLATE_PATH)),
            _check("privacy_tracking_disabled", "PASS" if privacy_template.get("NSPrivacyTracking") is False else "FAIL", str(privacy_template.get("NSPrivacyTracking"))),
            _check("privacy_manifest_shape", "PASS" if all(isinstance(privacy_template.get(key), list) for key in ("NSPrivacyTrackingDomains", "NSPrivacyCollectedDataTypes", "NSPrivacyAccessedAPITypes")) else "FAIL", "tracking domains, collected data, and accessed APIs must be arrays"),
        ]
    )

    runtime_policy = profile.get("runtime_policy")
    runtime_policy_valid = (
        isinstance(runtime_policy, dict)
        and runtime_policy.get("downloaded_executable_code") == "DENY"
        and runtime_policy.get("unreviewed_plugins") == "DENY"
        and runtime_policy.get("arbitrary_shell") == "DISABLED_IN_APP_STORE_PROFILE"
        and runtime_policy.get("filesystem_access") == "USER_SELECTED_OR_CONTAINER_ONLY"
        and runtime_policy.get("persistent_external_file_access") == "SECURITY_SCOPED_BOOKMARK_REQUIRED"
    )
    checks.append(_check("runtime_policy", "PASS" if runtime_policy_valid else "FAIL", "Store runtime restrictions must fail closed"))

    nonclaims = profile.get("explicit_nonclaims")
    nonclaim_rows = nonclaims if isinstance(nonclaims, list) else []
    checks.append(
        _check(
            "explicit_nonclaims",
            "PASS" if len(nonclaim_rows) >= 4 and all(_nonempty_string(value) for value in nonclaim_rows) else "FAIL",
            f"count={len(nonclaim_rows)}",
        )
    )

    policy_path = root / "docs" / "APPLE_APP_STORE_SECURITY.md"
    checks.append(_check("security_policy_document", "PASS" if policy_path.is_file() else "FAIL", str(policy_path)))
    packaging_path = root / "backend" / "app" / "packaging.py"
    packaging_text = packaging_path.read_text(encoding="utf-8", errors="replace") if packaging_path.is_file() else ""
    source_bound = "source_bound_local_macos_app_wrapper" in packaging_text and "REPO_ROOT" in packaging_text
    checks.append(
        _check(
            "current_local_wrapper_store_eligibility",
            "OPEN" if source_bound else "PASS",
            "current source-bound wrapper is explicitly not a Mac App Store candidate" if source_bound else "no source-bound wrapper marker found",
        )
    )
    return checks, {
        "entitlement_template": entitlement_template,
        "privacy_template": privacy_template,
        "gate_rows": gate_rows,
        "bundle_requirements": bundle_requirements,
        "privacy_relative_path": privacy_relative_path,
        "provision_relative_path": provision_relative_path,
        "receipt_relative_path": receipt_relative_path,
        "required_info_keys": required_info_keys,
    }


def build_macos_app_store_readiness(
    root: Path = ROOT,
    *,
    app_path: Path | None = None,
    platform_name: str = sys.platform,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    profile_path = root / PROFILE_PATH
    try:
        profile = _load_json(profile_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        check = _check("security_profile", "FAIL", str(exc))
        return {
            "status": "MAC_APP_STORE_READINESS_FAIL",
            "schema_version": PROFILE_SCHEMA_VERSION,
            "project_root": str(root),
            "profile_path": str(profile_path),
            "checks": [check],
            "counts": {"pass": 0, "open": 0, "blocked": 0, "fail": 1, "total": 1},
            "failed_checks": ["security_profile"],
        }

    checks, source_evidence = _validate_source_profile(root, profile)
    artifact: dict[str, Any] | None = None
    if app_path is None:
        checks.append(_check("candidate_bundle", "OPEN", "no Mac App Store candidate bundle supplied"))
    else:
        app = app_path.expanduser().resolve()
        artifact_checks: list[dict[str, str]] = []
        artifact = {"app_path": str(app), "commands": {}, "entitlements": None}
        artifact_checks.extend(
            [
                _check("candidate_platform", "PASS" if platform_name == "darwin" else "FAIL", platform_name),
                _check("candidate_bundle_exists", "PASS" if app.is_dir() and app.suffix == ".app" else "FAIL", str(app)),
            ]
        )
        if app.is_dir() and app.suffix == ".app":
            contents = app / "Contents"
            info_path = contents / "Info.plist"
            privacy_relative_path = source_evidence.get("privacy_relative_path")
            provision_relative_path = source_evidence.get("provision_relative_path")
            receipt_relative_path = source_evidence.get("receipt_relative_path")
            privacy_path = app / privacy_relative_path if isinstance(privacy_relative_path, Path) else app / "INVALID_PRIVACY_PATH"
            provision_path = app / provision_relative_path if isinstance(provision_relative_path, Path) else app / "INVALID_PROVISION_PATH"
            candidate_receipt_path = app / receipt_relative_path if isinstance(receipt_relative_path, Path) else app / "INVALID_RECEIPT_PATH"
            try:
                info = _load_plist(info_path)
                info_error = None
            except (OSError, TypeError, ValueError, plistlib.InvalidFileException) as exc:
                info = {}
                info_error = str(exc)
            required_info_keys = source_evidence.get("required_info_keys", [])
            missing_info_keys = sorted(key for key in required_info_keys if not info.get(key))
            executable_name = info.get("CFBundleExecutable")
            executable_name_valid = _nonempty_string(executable_name) and Path(str(executable_name)).name == executable_name
            executable_path = contents / "MacOS" / str(executable_name) if executable_name_valid else contents / "INVALID_EXECUTABLE"
            arbitrary_loads = bool(
                info.get("NSAppTransportSecurity", {}).get("NSAllowsArbitraryLoads")
                if isinstance(info.get("NSAppTransportSecurity"), dict)
                else False
            )
            artifact_checks.extend(
                [
                    _check("candidate_info_plist", "PASS" if info_error is None else "FAIL", info_error or str(info_path)),
                    _check("candidate_info_keys", "PASS" if not missing_info_keys else "FAIL", "missing=" + ",".join(missing_info_keys) if missing_info_keys else "all required keys present"),
                    _check("candidate_bundle_identifier", "PASS" if info.get("CFBundleIdentifier") == profile.get("expected_bundle_identifier") else "FAIL", f"observed={info.get('CFBundleIdentifier')} expected={profile.get('expected_bundle_identifier')}"),
                    _check("candidate_package_type", "PASS" if info.get("CFBundlePackageType") == "APPL" else "FAIL", str(info.get("CFBundlePackageType"))),
                    _check(
                        "candidate_executable",
                        "PASS" if executable_name_valid and executable_path.is_file() and os.access(executable_path, os.X_OK) else "FAIL",
                        str(executable_path),
                    ),
                    _check("candidate_transport_security", "FAIL" if arbitrary_loads else "PASS", "NSAllowsArbitraryLoads must not be true"),
                ]
            )
            try:
                privacy = _load_plist(privacy_path)
                privacy_error = None
            except (OSError, TypeError, ValueError, plistlib.InvalidFileException) as exc:
                privacy = {}
                privacy_error = str(exc)
            artifact_checks.extend(
                [
                    _check("candidate_privacy_manifest", "PASS" if privacy_error is None else "FAIL", privacy_error or str(privacy_path)),
                    _check("candidate_privacy_tracking", "PASS" if privacy.get("NSPrivacyTracking") is False else "FAIL", str(privacy.get("NSPrivacyTracking"))),
                    _check("candidate_provisioning_profile", "PASS" if provision_path.is_file() else "FAIL", str(provision_path)),
                ]
            )
            escaping_symlinks = _escaping_symlinks(app)
            artifact_checks.append(_check("candidate_symlink_boundary", "PASS" if not escaping_symlinks else "FAIL", "none" if not escaping_symlinks else ",".join(escaping_symlinks)))

            candidate_receipt: dict[str, Any] = {}
            candidate_receipt_error: str | None = None
            if candidate_receipt_path.is_file():
                try:
                    candidate_receipt = _load_json(candidate_receipt_path)
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    candidate_receipt_error = str(exc)
            else:
                candidate_receipt_error = f"missing={candidate_receipt_path}"
            receipt_valid = (
                candidate_receipt_error is None
                and candidate_receipt.get("schema_version") == "mucaro_mac_app_store_candidate_receipt_v1"
                and candidate_receipt.get("scope") == "self_contained_mac_app_store_candidate"
                and candidate_receipt.get("build_profile") == "mac_app_store"
                and candidate_receipt.get("bundle_identifier") == profile.get("expected_bundle_identifier")
                and candidate_receipt.get("source_bound") is False
                and candidate_receipt.get("runtime_origin") == "embedded"
                and _git_sha(candidate_receipt.get("source_head_sha"))
            )
            artifact_checks.extend(
                [
                    _check(
                        "candidate_receipt",
                        "PASS" if receipt_valid else "FAIL",
                        candidate_receipt_error or str(candidate_receipt_path),
                    ),
                    _check(
                        "candidate_self_contained",
                        "PASS" if receipt_valid else "FAIL",
                        str(candidate_receipt.get("scope") or "candidate receipt unavailable"),
                    ),
                ]
            )

            local_receipt_path = contents / "Resources" / "mucaro-app-receipt.json"
            local_receipt_scope = None
            if local_receipt_path.is_file():
                try:
                    local_receipt_scope = _load_json(local_receipt_path).get("scope")
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    local_receipt_scope = "UNREADABLE"
            artifact_checks.append(
                _check(
                    "candidate_source_bound_marker",
                    "FAIL" if local_receipt_scope in {"source_bound_local_macos_app_wrapper", "UNREADABLE"} else "PASS",
                    str(local_receipt_scope or "none"),
                )
            )

            if platform_name == "darwin":
                commands = {
                    "codesign_verify": _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)], runner),
                    "codesign_details": _run(["codesign", "-dv", "--verbose=4", str(app)], runner),
                    "codesign_entitlements": _run(["codesign", "-d", "--entitlements", ":-", "--xml", str(app)], runner),
                    "provisioning_profile": _run(["security", "cms", "-D", "-i", str(provision_path)], runner),
                }
                artifact["commands"] = commands
                artifact_checks.extend(
                    [
                        _check("candidate_codesign_verify", commands["codesign_verify"]["status"], "codesign --verify --deep --strict"),
                        _check("candidate_codesign_details", commands["codesign_details"]["status"], "codesign identity details"),
                        _check("candidate_codesign_entitlements", commands["codesign_entitlements"]["status"], "codesign entitlement extraction"),
                        _check("candidate_provisioning_decode", commands["provisioning_profile"]["status"], "security cms provisioning-profile decode"),
                    ]
                )
                details_output = str(commands["codesign_details"].get("output") or "")
                expected_identifier = str(profile.get("expected_bundle_identifier") or "")
                disallowed_authority = any(
                    marker in details_output
                    for marker in ("Authority=Apple Development", "Authority=Developer ID Application", "Authority=Mac Developer")
                )
                artifact_checks.append(
                    _check(
                        "candidate_distribution_identity",
                        "PASS" if "Authority=" in details_output and not disallowed_authority and "TeamIdentifier=" in details_output and f"Identifier={expected_identifier}" in details_output else "FAIL",
                        "signed authority, team identifier, and exact bundle identifier required",
                    )
                )
                try:
                    entitlements = _extract_plist(str(commands["codesign_entitlements"].get("output") or ""))
                    entitlement_error = None
                except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
                    entitlements = {}
                    entitlement_error = str(exc)
                artifact["entitlements"] = entitlements
                required_entitlements = profile.get("required_entitlements", {})
                forbidden_entitlements = profile.get("forbidden_entitlements", [])
                conditional_entitlements = profile.get("conditionally_allowed_entitlements", [])
                allowed_entitlements = set(required_entitlements) | set(conditional_entitlements)
                missing_required = sorted(key for key, value in required_entitlements.items() if entitlements.get(key) != value)
                enabled_forbidden = sorted(key for key in forbidden_entitlements if entitlements.get(key))
                undeclared = sorted(key for key in entitlements if key not in allowed_entitlements)
                signed_application_identifier = entitlements.get("com.apple.application-identifier")
                signed_team_identifier = entitlements.get("com.apple.developer.team-identifier")
                signed_identity_valid = (
                    _nonempty_string(signed_application_identifier)
                    and str(signed_application_identifier).endswith(f".{expected_identifier}")
                    and _nonempty_string(signed_team_identifier)
                    and str(signed_application_identifier).startswith(f"{signed_team_identifier}.")
                )
                artifact_checks.extend(
                    [
                        _check("candidate_entitlement_plist", "PASS" if entitlement_error is None else "FAIL", entitlement_error or "parsed"),
                        _check("candidate_required_entitlements", "PASS" if not missing_required else "FAIL", "missing=" + ",".join(missing_required) if missing_required else "all required entitlements exact"),
                        _check("candidate_forbidden_entitlements", "PASS" if not enabled_forbidden else "FAIL", "none" if not enabled_forbidden else ",".join(enabled_forbidden)),
                        _check("candidate_undeclared_entitlements", "PASS" if not undeclared else "FAIL", "none" if not undeclared else ",".join(undeclared)),
                        _check(
                            "candidate_signed_identity_entitlements",
                            "PASS" if signed_identity_valid else "FAIL",
                            f"application_identifier={signed_application_identifier} team_identifier={signed_team_identifier}",
                        ),
                    ]
                )
                try:
                    provisioning = _extract_plist(str(commands["provisioning_profile"].get("output") or ""))
                    provisioning_error = None
                except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
                    provisioning = {}
                    provisioning_error = str(exc)
                artifact["provisioning_profile"] = provisioning
                provision_entitlements = provisioning.get("Entitlements")
                provision_entitlements = provision_entitlements if isinstance(provision_entitlements, dict) else {}
                team_identifiers = provisioning.get("TeamIdentifier")
                team_identifiers = team_identifiers if isinstance(team_identifiers, list) else []
                application_identifier = provision_entitlements.get("com.apple.application-identifier") or provision_entitlements.get("application-identifier")
                app_id_matches = (
                    _nonempty_string(application_identifier)
                    and str(application_identifier).endswith(f".{expected_identifier}")
                )
                expiration = provisioning.get("ExpirationDate")
                if isinstance(expiration, datetime) and expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=UTC)
                expiration_valid = isinstance(expiration, datetime) and expiration.astimezone(UTC) > datetime.now(UTC)
                distribution_profile = not provisioning.get("ProvisionedDevices") and provisioning.get("ProvisionsAllDevices") is not True
                team_matches = bool(team_identifiers) and any(
                    f"TeamIdentifier={team_id}" in details_output
                    and team_id == signed_team_identifier
                    for team_id in team_identifiers
                    if _nonempty_string(team_id)
                )
                application_identifier_matches = app_id_matches and application_identifier == signed_application_identifier
                artifact_checks.extend(
                    [
                        _check("candidate_provisioning_plist", "PASS" if provisioning_error is None else "FAIL", provisioning_error or "parsed"),
                        _check("candidate_provisioning_application_id", "PASS" if application_identifier_matches else "FAIL", str(application_identifier)),
                        _check("candidate_provisioning_team", "PASS" if team_matches else "FAIL", ",".join(str(value) for value in team_identifiers)),
                        _check("candidate_provisioning_expiration", "PASS" if expiration_valid else "FAIL", str(expiration)),
                        _check("candidate_provisioning_distribution", "PASS" if distribution_profile else "FAIL", "development and Developer ID profiles are rejected"),
                        _check(
                            "candidate_provisioning_sandbox",
                            "PASS" if provision_entitlements.get("com.apple.security.app-sandbox") is True else "FAIL",
                            str(provision_entitlements.get("com.apple.security.app-sandbox")),
                        ),
                        _check(
                            "candidate_provisioning_debug",
                            "PASS" if provision_entitlements.get("com.apple.security.get-task-allow") is not True else "FAIL",
                            str(provision_entitlements.get("com.apple.security.get-task-allow")),
                        ),
                    ]
                )
        checks.extend(artifact_checks)

    gate_rows = source_evidence.get("gate_rows", [])
    external_gates = [
        row
        for row in gate_rows
        if isinstance(row, dict) and row.get("id") in EXTERNAL_GATE_IDS
    ]
    failed = [check for check in checks if check["status"] == "FAIL"]
    external_gate_counts = {
        state.lower(): sum(1 for gate in external_gates if gate.get("state") == state)
        for state in ("PASS", "OPEN", "BLOCKED", "FAIL")
    }
    external_gate_counts["total"] = len(external_gates)
    counts = {
        state.lower(): sum(1 for check in checks if check["status"] == state)
        for state in ("PASS", "OPEN", "BLOCKED", "FAIL")
    }
    counts["total"] = len(checks)
    if failed:
        status = "MAC_APP_STORE_READINESS_FAIL"
    elif app_path is None:
        status = "MAC_APP_STORE_READINESS_OPEN"
    else:
        status = "MAC_APP_STORE_CANDIDATE_PROVISIONAL"
    return {
        "status": status,
        "schema_version": PROFILE_SCHEMA_VERSION,
        "project_root": str(root),
        "profile_path": str(profile_path),
        "profile_state": profile.get("current_state"),
        "candidate_scope": "local_static_bundle_and_codesign_audit" if app_path is not None else "source_policy_readiness",
        "notarization_required_for_store": False,
        "notarization_note": "Developer ID notarization is an outside-the-Store lane and is not Mac App Store proof.",
        "checks": checks,
        "counts": counts,
        "failed_checks": [check["name"] for check in failed],
        "artifact": artifact,
        "external_gates": external_gates,
        "external_gate_counts": external_gate_counts,
        "runtime_policy": profile.get("runtime_policy"),
        "explicit_nonclaims": profile.get("explicit_nonclaims"),
        "apple_references": profile.get("apple_references"),
        "next_steps": [
            "replace the source-bound local wrapper with a self-contained App Store build target",
            "implement native user-selected file access and security-scoped bookmark lifecycle",
            "resolve coding execution and loopback server compatibility before enabling those capabilities in the Store profile",
            "complete privacy and third-party SDK data inventory before freezing PrivacyInfo.xcprivacy",
            "audit the exact signed candidate with this command before App Store Connect upload",
            "preserve App Store Connect processing and Apple App Review as external evidence gates",
        ],
    }
