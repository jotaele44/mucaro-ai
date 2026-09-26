#!/usr/bin/env python3
"""Fail-closed release-grade certification (TERMINAL CERTIFICATION RULE, MATRIX-019, MATRIX-020).

Runs the local gates, checks the exact-head macOS app receipts from CI, runs the security
gates, freezes the final release manifest, and applies the release-scope ledgers' terminal
rule. Anything short of every condition prints ``MUCARO AI RELEASE STATUS = PROVISIONAL``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_manifest import build_final_release_manifest, write_final_release_manifest
from backend.app.release_receipts import write_release_receipt
from backend.app.release_scope import STATUS_CERTIFIED, compute_release_status

FINAL_CERTIFIED = "MUCARO_AI_RELEASE_GRADE_CERTIFIED"
FINAL_BLOCKED = "MUCARO_AI_RELEASE_GRADE_BLOCKED"
EXTERNAL_EVIDENCE_SCHEMA = "mucaro-external-release-evidence/1.0"
MACOS_BUILD_SCHEMA = "mucaro-macos-app-build/1.0"
MACOS_E2E_SCHEMA = "mucaro-macos-app-e2e/1.0"

# Gates of the earlier source-bound/portable-wrapper packaging lane, replaced by the frozen
# PyInstaller app (assumption A2 refinement). Listed in every receipt so the change is visible.
SUPERSEDED_GATES = {
    "portable_embedded_runtime": "replaced by macos_app_build (PyInstaller bundle with its own Python runtime)",
    "portable_install_reinstall_smoke": "replaced by macos_app_e2e (install from zip, relaunch, reinstall keeping data)",
    "distinct_version_upgrade_rollback": "replaced by macos_app_e2e reinstall plus backend/tests/test_db_upgrade.py "
    "(atomic schema upgrade, interrupted-upgrade rollback, too-new database refused); a cross-version app upgrade "
    "needs a previous packaged release",
    "clean_machine_macos": "replaced by macos_app_e2e on a fresh hosted macOS runner (LaunchServices open)",
}
REQUIRED_GATES = (
    "clean_worktree",
    "local_release",
    "fresh_clone",
    "python_distribution",
    "representative_operator_workflow",
    "retrieval_benchmark",
    "gis_benchmark",
    "security_profile",
    "security_gates",
    "package_build",
    "source_artifact_installability",
    "macos_app_build",
    "macos_app_e2e",
    "signed_notarized_installer",
    "hosted_ci_current_head",
    "final_release_manifest",
    "release_scope",
)


@dataclass
class Gate:
    name: str
    status: str
    detail: str
    command: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "command": self.command,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_head() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def run_gate(name: str, command: list[str], *, cwd: Path = ROOT) -> tuple[Gate, str]:
    proc = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    lines = proc.stdout.strip().splitlines()
    detail = lines[-1] if lines else f"returncode={proc.returncode}"
    return Gate(name, "PASS" if proc.returncode == 0 else "FAIL", detail, command), proc.stdout


def external_gate(
    name: str,
    path: Path | None,
    *,
    expected_head: str,
    required_true: tuple[str, ...],
) -> Gate:
    if path is None:
        return Gate(name, "BLOCKED", "required external evidence receipt not supplied")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Gate(name, "FAIL", f"external receipt unreadable: {exc}")
    if not isinstance(payload, dict):
        return Gate(name, "FAIL", "external receipt must be a JSON object")
    failures = []
    if payload.get("schema_version") != EXTERNAL_EVIDENCE_SCHEMA:
        failures.append("schema_version")
    if payload.get("head_sha") != expected_head:
        failures.append("head_sha")
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        failures.append("evidence")
        evidence = {}
    for field in required_true:
        if evidence.get(field) is not True:
            failures.append(field)
    if failures:
        return Gate(name, "FAIL", "invalid fields: " + ",".join(failures))
    return Gate(name, "PASS", f"sha256={hashlib.sha256(raw).hexdigest()}")


def parse_json_stdout(name: str, stdout: str) -> tuple[dict[str, Any] | None, Gate | None]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return None, Gate(name, "FAIL", f"JSON output invalid: {exc}")
    if not isinstance(payload, dict):
        return None, Gate(name, "FAIL", "JSON output must be an object")
    return payload, None


def receipt_gate(name: str, path: Path | None, *, expected_head: str, schema_version: str, expected_status: str) -> Gate:
    """Checks a receipt written by scripts/build_macos_app.py or scripts/verify_macos_app.py."""
    if path is None:
        return Gate(name, "BLOCKED", "receipt not supplied (produced by the macOS app workflow for this head)")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Gate(name, "FAIL", f"receipt unreadable: {exc}")
    if not isinstance(payload, dict):
        return Gate(name, "FAIL", "receipt must be a JSON object")
    failures = []
    if payload.get("schema_version") != schema_version:
        failures.append("schema_version")
    if payload.get("head_sha") != expected_head:
        failures.append(f"head_sha={payload.get('head_sha')}")
    if payload.get("status") != expected_status:
        failures.append(f"status={payload.get('status')}")
    if payload.get("blocking_checks"):
        failures.append("blocking_checks=" + ",".join(map(str, payload["blocking_checks"])))
    if failures:
        return Gate(name, "FAIL", "; ".join(failures))
    return Gate(name, "PASS", f"{expected_status} sha256={hashlib.sha256(raw).hexdigest()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fail-closed Mucaro AI release-grade certification matrix.")
    parser.add_argument("--macos-build-receipt", type=Path, help="build_receipt.json from the macOS app workflow for this head")
    parser.add_argument("--macos-e2e-receipt", type=Path, help="e2e_receipt.json from the macOS app workflow for this head")
    parser.add_argument("--signing-receipt", type=Path)
    parser.add_argument("--notarization-receipt", type=Path)
    parser.add_argument("--hosted-ci-receipt", type=Path)
    return parser


def _receipt_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def security_profile_gate(e2e_receipt: Path | None, *, expected_head: str) -> Gate:
    """The shipped app's own runtime security report, taken from the exact-head macOS E2E receipt."""
    if e2e_receipt is None:
        return Gate("security_profile", "BLOCKED", "macOS E2E receipt not supplied; it carries the packaged app's runtime security report")
    payload = _receipt_payload(e2e_receipt)
    if payload is None:
        return Gate("security_profile", "FAIL", "E2E receipt unreadable")
    if payload.get("head_sha") != expected_head:
        return Gate("security_profile", "FAIL", f"E2E receipt head_sha={payload.get('head_sha')}")
    security = payload.get("security")
    if not isinstance(security, dict):
        return Gate("security_profile", "FAIL", "E2E receipt has no runtime security report")
    if security.get("production_ready") is True and security.get("status") == "SECURITY_OK":
        return Gate("security_profile", "PASS", f"packaged app: production profile, SECURITY_OK, cors={security.get('cors_origins')}")
    return Gate("security_profile", "FAIL", f"packaged app: production_ready={security.get('production_ready')} status={security.get('status')} failing={security.get('failing')}")


def developer_environment_security_gate() -> Gate:
    """Informational: the certifier's own shell, which is not the shipped configuration."""
    command = [sys.executable, "scripts/mucaro.py", "security", "status", "--host", "127.0.0.1", "--format", "json"]
    process_gate, stdout = run_gate("developer_environment_security_command", command)
    payload, parse_error = parse_json_stdout("developer_environment_security_json", stdout) if process_gate.status == "PASS" else (None, None)
    if payload is None:
        return Gate("developer_environment_security", "FAIL", parse_error.detail if parse_error else process_gate.detail, command)
    return Gate("developer_environment_security", "INFO", f"profile={payload.get('deployment_profile')} status={payload.get('status')}", command)


def hosted_ci_gate(path: Path | None, *, expected_head: str) -> Gate:
    """Hosted CI tests the pull-request merge commit; accept it for this head only when its tree is identical."""
    required = ("ci_admitted", "ci_passed", "jobs_executed")
    gate = external_gate("hosted_ci_current_head", path, expected_head=expected_head, required_true=required)
    if gate.status != "FAIL" or path is None:
        return gate
    payload = _receipt_payload(path) or {}
    tested = str(payload.get("head_sha") or "")
    if payload.get("pull_request_head_sha") == expected_head and payload.get("tree_equivalent_to_pull_request_head") is True and tested:
        equivalent = external_gate("hosted_ci_current_head", path, expected_head=tested, required_true=required)
        if equivalent.status == "PASS":
            return Gate("hosted_ci_current_head", "PASS", f"tested merge {tested[:12]} has the same tree as {expected_head[:12]}; {equivalent.detail}")
    return gate


def package_gates() -> list[Gate]:
    package_gate, stdout = run_gate("package_build", [sys.executable, "scripts/mucaro.py", "package", "build", "--format", "json"])
    gates = [package_gate]
    manifest_path = None
    if package_gate.status == "PASS":
        payload, parse_error = parse_json_stdout("package_build_json", stdout)
        if parse_error:
            gates.append(parse_error)
        manifest = (payload or {}).get("manifest")
        if isinstance(manifest, dict) and isinstance(manifest.get("path"), str):
            manifest_path = Path(manifest["path"])
    if manifest_path is None:
        gates.append(Gate("source_artifact_installability", "BLOCKED", "package manifest unavailable"))
    else:
        gate, _ = run_gate("source_artifact_installability", [sys.executable, "scripts/certify_source_artifact.py", str(manifest_path)])
        gates.append(gate)
    return gates


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    head = git_head()
    gates: list[Gate] = []

    status_proc = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, capture_output=True, check=False)
    gates.append(Gate("clean_worktree", "PASS" if not status_proc.stdout.strip() else "FAIL", status_proc.stdout.strip() or "clean"))

    commands = [
        ("local_release", [sys.executable, "scripts/certify_release.py"]),
        ("fresh_clone", [sys.executable, "scripts/certify_fresh_clone.py", "--ref", head]),
        ("python_distribution", [sys.executable, "scripts/certify_python_distribution.py"]),
        ("representative_operator_workflow", [sys.executable, "scripts/certify_operator_workflow.py"]),
        ("retrieval_benchmark", [sys.executable, "scripts/certify_retrieval_quality.py"]),
        ("gis_benchmark", [sys.executable, "scripts/certify_gis_quality.py"]),
        ("security_gates", [sys.executable, "scripts/certify_security_gates.py"]),
    ]
    for name, command in commands:
        gates.append(run_gate(name, command)[0])
    gates.append(security_profile_gate(args.macos_e2e_receipt, expected_head=head))
    gates.append(developer_environment_security_gate())
    gates.extend(package_gates())
    gates.append(receipt_gate("macos_app_build", args.macos_build_receipt, expected_head=head, schema_version=MACOS_BUILD_SCHEMA, expected_status="MACOS_APP_BUILD_OK"))
    gates.append(receipt_gate("macos_app_e2e", args.macos_e2e_receipt, expected_head=head, schema_version=MACOS_E2E_SCHEMA, expected_status="MACOS_APP_E2E_PASS"))
    gates.append(external_gate("signed_notarized_installer", args.signing_receipt, expected_head=head, required_true=("signed", "notarized", "installer_verified")))
    gates.append(hosted_ci_gate(args.hosted_ci_receipt, expected_head=head))

    manifest = build_final_release_manifest(
        ROOT,
        head=head,
        macos_build_receipt=args.macos_build_receipt,
        macos_e2e_receipt=args.macos_e2e_receipt,
        signing_receipt=args.signing_receipt,
        notarization_receipt=args.notarization_receipt,
    )
    manifest_path, manifest_sha = write_final_release_manifest(ROOT, manifest)
    gates.append(
        Gate(
            "final_release_manifest",
            "PASS" if manifest["complete_except_blocked"] else "FAIL",
            f"{manifest_path.relative_to(ROOT)} sha256={manifest_sha} missing={','.join(manifest['missing_entries']) or 'none'}",
        )
    )

    scope = compute_release_status(ROOT)
    residue = {key: value for key, value in scope.get("residue", {}).items() if value}
    gates.append(Gate("release_scope", "PASS" if scope["status"] == STATUS_CERTIFIED else "BLOCKED", f"{scope['status']} residue={json.dumps(residue, sort_keys=True)[:1500]}"))

    by_name = {gate.name: gate for gate in gates}
    for name in REQUIRED_GATES:
        if name not in by_name:
            gates.append(Gate(name, "BLOCKED", "mandatory gate row missing"))
            by_name[name] = gates[-1]
    final_ok = all(by_name[name].status == "PASS" for name in REQUIRED_GATES)
    final_status = FINAL_CERTIFIED if final_ok else FINAL_BLOCKED
    status_line = "MUCARO AI RELEASE-GRADE CERTIFIED" if final_ok else "MUCARO AI RELEASE STATUS = PROVISIONAL"
    receipt_path = write_release_receipt(
        ROOT,
        receipt_type="release_grade",
        status=final_status,
        head_sha=head,
        command=[sys.executable, "scripts/certify_release_grade.py", *(argv if argv is not None else sys.argv[1:])],
        payload={
            "gates": [gate.as_dict() for gate in gates],
            "required_gate_names": list(REQUIRED_GATES),
            "blocking_gates": sorted(name for name in REQUIRED_GATES if by_name[name].status != "PASS"),
            "superseded_gates": SUPERSEDED_GATES,
            "gate_sources": {
                "security_profile": "runtime security report of the packaged app in the exact-head macOS E2E receipt",
                "developer_environment_security": "informational only: the certifier's own shell, not the shipped configuration",
                "hosted_ci_current_head": "hosted CI receipt for this head, or for a merge commit whose tree equals it",
            },
            "final_release_manifest": {"path": str(manifest_path.relative_to(ROOT)), "sha256": manifest_sha},
            "release_scope_status": scope["status"],
            "release_status_line": status_line,
            "certification_rule": "every mandatory gate must be PASS and the release-scope terminal rule must hold; BLOCKED and FAIL both prohibit release-grade certification",
        },
    )
    for gate in gates:
        print(f"{gate.status} {gate.name}: {gate.detail[:200]}")
    print(final_status)
    print(status_line)
    print(f"RELEASE_GRADE_RECEIPT {receipt_path.relative_to(ROOT)}")
    return 0 if final_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
