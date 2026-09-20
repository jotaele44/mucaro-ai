from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .distribution_policy import (
    MAC_APP_STORE_DISTRIBUTION_CHANNEL,
    evaluate_distribution_runtime_policy,
)

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


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _repository_relative_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        return None
    return path


def _schema_errors(payload: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    return sorted(error.message for error in Draft202012Validator(schema).iter_errors(payload))


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _fresh_timestamp(value: Any, *, now: datetime, maximum_age_hours: int) -> bool:
    parsed = _parse_utc_timestamp(value)
    if parsed is None:
        return False
    return now - timedelta(hours=maximum_age_hours) <= parsed <= now + timedelta(minutes=5)


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

    supply_chain_requirements = profile.get("supply_chain_requirements")
    supply_chain_requirements = supply_chain_requirements if isinstance(supply_chain_requirements, dict) else {}
    evidence_paths = {
        "sbom": _bundle_relative_path(supply_chain_requirements.get("sbom_relative_path")),
        "vulnerability_scan": _bundle_relative_path(
            supply_chain_requirements.get("vulnerability_scan_relative_path")
        ),
        "malware_scan": _bundle_relative_path(supply_chain_requirements.get("malware_scan_relative_path")),
    }
    schema_paths = {
        "candidate_receipt": _repository_relative_path(
            supply_chain_requirements.get("candidate_receipt_schema_path")
        ),
        "vulnerability_scan": _repository_relative_path(
            supply_chain_requirements.get("vulnerability_scan_schema_path")
        ),
        "malware_scan": _repository_relative_path(supply_chain_requirements.get("malware_scan_schema_path")),
    }
    maximum_scan_age_hours = supply_chain_requirements.get("maximum_scan_age_hours")
    maximum_evidence_file_bytes = supply_chain_requirements.get("maximum_evidence_file_bytes")
    allowed_sbom_spec_versions = supply_chain_requirements.get("allowed_sbom_spec_versions")
    maximum_vulnerability_findings = supply_chain_requirements.get("maximum_vulnerability_findings")
    supply_chain_contract_valid = (
        bool(supply_chain_requirements)
        and all(path is not None for path in evidence_paths.values())
        and len(set(evidence_paths.values())) == len(evidence_paths)
        and all(
            path is not None and path.parts[:2] == ("_architecture", "schemas")
            for path in schema_paths.values()
        )
        and isinstance(maximum_scan_age_hours, int)
        and not isinstance(maximum_scan_age_hours, bool)
        and maximum_scan_age_hours > 0
        and isinstance(maximum_evidence_file_bytes, dict)
        and set(maximum_evidence_file_bytes) == set(evidence_paths)
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in maximum_evidence_file_bytes.values()
        )
        and isinstance(allowed_sbom_spec_versions, list)
        and bool(allowed_sbom_spec_versions)
        and all(_nonempty_string(value) for value in allowed_sbom_spec_versions)
        and len(allowed_sbom_spec_versions) == len(set(allowed_sbom_spec_versions))
        and supply_chain_requirements.get("require_component_purls") is True
        and isinstance(maximum_vulnerability_findings, dict)
        and set(maximum_vulnerability_findings) == {"critical", "high", "known_patchable"}
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in maximum_vulnerability_findings.values()
        )
        and supply_chain_requirements.get("maximum_malware_detections") == 0
        and supply_chain_requirements.get("maximum_malware_scan_errors") == 0
    )
    checks.append(
        _check(
            "supply_chain_requirement_contract",
            "PASS" if supply_chain_contract_valid else "FAIL",
            "exact evidence paths, schemas, freshness, SBOM, vulnerability, and malware thresholds",
        )
    )
    schemas: dict[str, dict[str, Any]] = {}
    schema_failures: list[str] = []
    for name, relative_path in schema_paths.items():
        if relative_path is None:
            schema_failures.append(f"{name}=invalid_path")
            continue
        try:
            schema = _load_json(root / relative_path)
            Draft202012Validator.check_schema(schema)
            schemas[name] = schema
        except (OSError, TypeError, ValueError, json.JSONDecodeError, SchemaError) as exc:
            schema_failures.append(f"{name}={exc}")
    checks.append(
        _check(
            "supply_chain_schemas",
            "PASS" if not schema_failures and len(schemas) == len(schema_paths) else "FAIL",
            "all valid" if not schema_failures else "; ".join(schema_failures),
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
        and runtime_policy.get("arbitrary_shell") == "DENY"
        and runtime_policy.get("user_code_execution") == "DENY"
        and runtime_policy.get("filesystem_access") == "USER_SELECTED_OR_CONTAINER_ONLY"
        and runtime_policy.get("persistent_external_file_access") == "SECURITY_SCOPED_BOOKMARK_REQUIRED"
    )
    checks.append(_check("runtime_policy", "PASS" if runtime_policy_valid else "FAIL", "Store runtime restrictions must fail closed"))
    runtime_enforcement = {
        "in_process": evaluate_distribution_runtime_policy(
            distribution_channel=MAC_APP_STORE_DISTRIBUTION_CHANNEL,
            runtime="IN_PROCESS",
            granted_capabilities={"runtime.in_process"},
            skill_id="mucaro/intelligence-plane-health",
        ),
        "subprocess": evaluate_distribution_runtime_policy(
            distribution_channel=MAC_APP_STORE_DISTRIBUTION_CHANNEL,
            runtime="IN_PROCESS",
            granted_capabilities={"runtime.in_process", "subprocess.execute"},
            skill_id="mucaro/intelligence-plane-health",
        ),
        "local_worker": evaluate_distribution_runtime_policy(
            distribution_channel=MAC_APP_STORE_DISTRIBUTION_CHANNEL,
            runtime="LOCAL_WORKER",
            granted_capabilities={"runtime.local_worker"},
            skill_id="mucaro/local-worker-health",
        ),
        "container": evaluate_distribution_runtime_policy(
            distribution_channel=MAC_APP_STORE_DISTRIBUTION_CHANNEL,
            runtime="CONTAINER",
            granted_capabilities={"runtime.container"},
            skill_id="mucaro/coding",
        ),
    }
    runtime_enforcement_valid = (
        runtime_enforcement["in_process"].authorized
        and all(not runtime_enforcement[name].authorized for name in ("subprocess", "local_worker", "container"))
    )
    checks.append(
        _check(
            "runtime_policy_enforcement",
            "PASS" if runtime_enforcement_valid else "FAIL",
            "reviewed in-process allowlist plus subprocess, local-worker, and container denials",
        )
    )

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
        "supply_chain_requirements": supply_chain_requirements,
        "evidence_paths": evidence_paths,
        "schemas": schemas,
    }


def _candidate_supply_chain_checks(
    app: Path,
    *,
    info: dict[str, Any],
    executable_sha256: str | None,
    candidate_receipt: dict[str, Any],
    source_evidence: dict[str, Any],
    now: datetime,
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    requirements = source_evidence.get("supply_chain_requirements")
    requirements = requirements if isinstance(requirements, dict) else {}
    evidence_paths = source_evidence.get("evidence_paths")
    evidence_paths = evidence_paths if isinstance(evidence_paths, dict) else {}
    schemas = source_evidence.get("schemas")
    schemas = schemas if isinstance(schemas, dict) else {}
    references = candidate_receipt.get("security_evidence")
    references = references if isinstance(references, dict) else {}

    reference_contract_valid = set(references) == set(evidence_paths)
    evidence_files: dict[str, Path] = {}
    reference_failures: list[str] = []
    for name, relative_path in evidence_paths.items():
        reference = references.get(name)
        reference = reference if isinstance(reference, dict) else {}
        if (
            not isinstance(relative_path, Path)
            or reference.get("relative_path") != str(relative_path)
            or not _sha256(reference.get("sha256"))
        ):
            reference_contract_valid = False
            reference_failures.append(name)
            continue
        evidence_files[name] = app / relative_path
    checks.append(
        _check(
            "candidate_security_evidence_contract",
            "PASS" if reference_contract_valid else "FAIL",
            "exact paths and SHA-256 references" if reference_contract_valid else "invalid=" + ",".join(reference_failures),
        )
    )

    missing_or_unsafe: list[str] = []
    oversized: list[str] = []
    hash_mismatches: list[str] = []
    hashed_names: set[str] = set()
    payloads: dict[str, dict[str, Any]] = {}
    parse_failures: list[str] = []
    maximum_evidence_file_bytes = requirements.get("maximum_evidence_file_bytes")
    maximum_evidence_file_bytes = (
        maximum_evidence_file_bytes if isinstance(maximum_evidence_file_bytes, dict) else {}
    )
    for name, path in evidence_files.items():
        if not path.is_file() or path.is_symlink():
            missing_or_unsafe.append(name)
            continue
        maximum_bytes = maximum_evidence_file_bytes.get(name)
        try:
            observed_size = path.stat().st_size
        except OSError:
            missing_or_unsafe.append(name)
            continue
        if not isinstance(maximum_bytes, int) or observed_size > maximum_bytes:
            oversized.append(name)
            continue
        reference = references[name]
        try:
            observed_sha256 = _sha256_file(path)
        except OSError:
            missing_or_unsafe.append(name)
            continue
        hashed_names.add(name)
        if observed_sha256 != reference.get("sha256"):
            hash_mismatches.append(name)
        try:
            payloads[name] = _load_json(path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            parse_failures.append(f"{name}={exc}")
    checks.extend(
        [
            _check(
                "candidate_security_evidence_files",
                "PASS" if len(evidence_files) == len(evidence_paths) and not missing_or_unsafe else "FAIL",
                "all regular files" if not missing_or_unsafe else "missing_or_unsafe=" + ",".join(missing_or_unsafe),
            ),
            _check(
                "candidate_security_evidence_sizes",
                "PASS" if not oversized and len(evidence_files) == len(evidence_paths) else "FAIL",
                "within configured limits" if not oversized else "oversized=" + ",".join(oversized),
            ),
            _check(
                "candidate_security_evidence_hashes",
                "PASS"
                if len(hashed_names) == len(evidence_paths) and not hash_mismatches
                else "FAIL",
                (
                    "all match receipt"
                    if len(hashed_names) == len(evidence_paths) and not hash_mismatches
                    else "unverifiable=" + ",".join(sorted(set(evidence_paths) - hashed_names))
                    if not hash_mismatches
                    else "mismatch=" + ",".join(hash_mismatches)
                ),
            ),
            _check(
                "candidate_security_evidence_json",
                "PASS" if len(payloads) == len(evidence_paths) and not parse_failures else "FAIL",
                "all parse" if not parse_failures else "; ".join(parse_failures),
            ),
        ]
    )

    source_head_sha = candidate_receipt.get("source_head_sha")
    bundle_identifier = info.get("CFBundleIdentifier")
    app_version = info.get("CFBundleShortVersionString")
    sbom = payloads.get("sbom", {})
    sbom_metadata = sbom.get("metadata") if isinstance(sbom.get("metadata"), dict) else {}
    sbom_component = sbom_metadata.get("component") if isinstance(sbom_metadata.get("component"), dict) else {}
    sbom_properties = sbom_metadata.get("properties") if isinstance(sbom_metadata.get("properties"), list) else []
    property_map = {
        row.get("name"): row.get("value")
        for row in sbom_properties
        if isinstance(row, dict) and _nonempty_string(row.get("name"))
    }
    components = sbom.get("components") if isinstance(sbom.get("components"), list) else []
    component_purls = [row.get("purl") for row in components if isinstance(row, dict)]
    allowed_sbom_versions = requirements.get("allowed_sbom_spec_versions")
    allowed_sbom_versions = allowed_sbom_versions if isinstance(allowed_sbom_versions, list) else []
    sbom_valid = (
        sbom.get("bomFormat") == "CycloneDX"
        and sbom.get("specVersion") in allowed_sbom_versions
        and isinstance(sbom.get("serialNumber"), str)
        and str(sbom.get("serialNumber")).startswith("urn:uuid:")
        and sbom_component.get("name") == "mucaro-ai"
        and sbom_component.get("version") == app_version
        and bool(components)
        and len(component_purls) == len(components)
        and all(_nonempty_string(value) for value in component_purls)
        and len(component_purls) == len(set(component_purls))
        and property_map.get("mucaro:source-head-sha") == source_head_sha
        and property_map.get("mucaro:bundle-identifier") == bundle_identifier
        and property_map.get("mucaro:candidate-executable-sha256") == executable_sha256
    )
    checks.append(
        _check(
            "candidate_sbom_contract",
            "PASS" if sbom_valid else "FAIL",
            f"format={sbom.get('bomFormat')} spec={sbom.get('specVersion')} components={len(components)}",
        )
    )

    vulnerability = payloads.get("vulnerability_scan", {})
    vulnerability_schema = schemas.get("vulnerability_scan")
    vulnerability_schema_errors = (
        _schema_errors(vulnerability, vulnerability_schema)
        if isinstance(vulnerability_schema, dict) and vulnerability
        else ["schema or payload unavailable"]
    )
    vulnerability_summary = (
        vulnerability.get("summary") if isinstance(vulnerability.get("summary"), dict) else {}
    )
    severity_total = sum(
        value
        for key in ("critical", "high", "moderate", "low", "unknown")
        if isinstance((value := vulnerability_summary.get(key)), int) and not isinstance(value, bool)
    )
    vulnerability_thresholds = requirements.get("maximum_vulnerability_findings")
    vulnerability_thresholds = vulnerability_thresholds if isinstance(vulnerability_thresholds, dict) else {}
    vulnerability_binding_valid = (
        not vulnerability_schema_errors
        and vulnerability.get("source_head_sha") == source_head_sha
        and vulnerability.get("bundle_identifier") == bundle_identifier
        and vulnerability.get("app_version") == app_version
        and vulnerability.get("candidate_executable_sha256") == executable_sha256
        and vulnerability_summary.get("total") == severity_total
        and all(
            isinstance(vulnerability_summary.get(key), int)
            and not isinstance(vulnerability_summary.get(key), bool)
            and vulnerability_summary.get(key) <= maximum
            for key, maximum in vulnerability_thresholds.items()
        )
    )
    checks.append(
        _check(
            "candidate_vulnerability_scan_contract",
            "PASS" if vulnerability_binding_valid else "FAIL",
            (
                f"total={vulnerability_summary.get('total')} known_patchable={vulnerability_summary.get('known_patchable')}"
                if not vulnerability_schema_errors
                else "schema=" + " | ".join(vulnerability_schema_errors[:3])
            ),
        )
    )

    malware = payloads.get("malware_scan", {})
    malware_schema = schemas.get("malware_scan")
    malware_schema_errors = (
        _schema_errors(malware, malware_schema)
        if isinstance(malware_schema, dict) and malware
        else ["schema or payload unavailable"]
    )
    malware_summary = malware.get("summary") if isinstance(malware.get("summary"), dict) else {}
    malware_binding_valid = (
        not malware_schema_errors
        and malware.get("source_head_sha") == source_head_sha
        and malware.get("bundle_identifier") == bundle_identifier
        and malware.get("app_version") == app_version
        and malware.get("candidate_executable_sha256") == executable_sha256
        and malware_summary.get("detections") == requirements.get("maximum_malware_detections")
        and malware_summary.get("errors") == requirements.get("maximum_malware_scan_errors")
    )
    checks.append(
        _check(
            "candidate_malware_scan_contract",
            "PASS" if malware_binding_valid else "FAIL",
            (
                f"files={malware_summary.get('files_scanned')} detections={malware_summary.get('detections')} errors={malware_summary.get('errors')}"
                if not malware_schema_errors
                else "schema=" + " | ".join(malware_schema_errors[:3])
            ),
        )
    )

    maximum_scan_age_hours = requirements.get("maximum_scan_age_hours")
    maximum_scan_age_hours = maximum_scan_age_hours if isinstance(maximum_scan_age_hours, int) else 0
    vulnerability_scanner = vulnerability.get("scanner") if isinstance(vulnerability.get("scanner"), dict) else {}
    malware_scanner = malware.get("scanner") if isinstance(malware.get("scanner"), dict) else {}
    freshness_values = {
        "vulnerability_scanned_at": vulnerability.get("scanned_at"),
        "vulnerability_database_updated_at": vulnerability_scanner.get("database_updated_at"),
        "malware_scanned_at": malware.get("scanned_at"),
        "malware_signatures_updated_at": malware_scanner.get("signatures_updated_at"),
    }
    stale = sorted(
        name
        for name, value in freshness_values.items()
        if not _fresh_timestamp(value, now=now, maximum_age_hours=maximum_scan_age_hours)
    )
    checks.append(
        _check(
            "candidate_security_scan_freshness",
            "PASS" if not stale else "FAIL",
            f"within={maximum_scan_age_hours}h" if not stale else "stale_or_invalid=" + ",".join(stale),
        )
    )
    return checks


def build_macos_app_store_readiness(
    root: Path = ROOT,
    *,
    app_path: Path | None = None,
    platform_name: str = sys.platform,
    runner: Runner = _default_runner,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    now = now.astimezone(UTC) if now is not None else datetime.now(UTC)
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
            try:
                executable_sha256 = _sha256_file(executable_path) if executable_path.is_file() else None
            except OSError:
                executable_sha256 = None
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
            candidate_receipt_schema = source_evidence.get("schemas", {}).get("candidate_receipt")
            receipt_schema_errors = (
                _schema_errors(candidate_receipt, candidate_receipt_schema)
                if isinstance(candidate_receipt_schema, dict) and candidate_receipt
                else ["schema or payload unavailable"]
            )
            receipt_valid = (
                candidate_receipt_error is None
                and not receipt_schema_errors
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
                        candidate_receipt_error
                        or ("schema=" + " | ".join(receipt_schema_errors[:3]) if receipt_schema_errors else str(candidate_receipt_path)),
                    ),
                    _check(
                        "candidate_self_contained",
                        "PASS" if receipt_valid else "FAIL",
                        str(candidate_receipt.get("scope") or "candidate receipt unavailable"),
                    ),
                ]
            )
            artifact_checks.extend(
                _candidate_supply_chain_checks(
                    app,
                    info=info,
                    executable_sha256=executable_sha256,
                    candidate_receipt=candidate_receipt,
                    source_evidence=source_evidence,
                    now=now,
                )
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
            "keep coding execution disabled in the Store profile and resolve the loopback server architecture",
            "complete privacy and third-party SDK data inventory before freezing PrivacyInfo.xcprivacy",
            "generate exact-candidate SBOM, vulnerability, and malware evidence within the configured freshness window",
            "audit the exact signed candidate with this command before App Store Connect upload",
            "preserve App Store Connect processing and Apple App Review as external evidence gates",
        ],
    }
