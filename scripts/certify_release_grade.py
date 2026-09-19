#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt  # noqa: E402

FINAL_CERTIFIED = "MUCARO_AI_RELEASE_GRADE_CERTIFIED"
FINAL_BLOCKED = "MUCARO_AI_RELEASE_GRADE_BLOCKED"
EXTERNAL_EVIDENCE_SCHEMA = "mucaro-external-release-evidence/1.0"


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
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the fail-closed Mucaro AI release-grade certification matrix.")
    parser.add_argument("--portable-runtime-dir", type=Path)
    parser.add_argument("--previous-portable-app", type=Path)
    parser.add_argument("--portable-install-root", type=Path)
    parser.add_argument("--clean-machine-receipt", type=Path)
    parser.add_argument("--signing-receipt", type=Path)
    parser.add_argument("--hosted-ci-receipt", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    head = git_head()
    gates: list[Gate] = []

    status_proc = subprocess.run(["git", "status", "--short"], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    gates.append(Gate("clean_worktree", "PASS" if not status_proc.stdout.strip() else "FAIL", status_proc.stdout.strip() or "clean"))

    commands = [
        ("local_release", [sys.executable, "scripts/certify_release.py"]),
        ("fresh_clone", [sys.executable, "scripts/certify_fresh_clone.py", "--ref", head]),
        ("python_distribution", [sys.executable, "scripts/certify_python_distribution.py"]),
        ("representative_operator_workflow", [sys.executable, "scripts/certify_operator_workflow.py"]),
        ("retrieval_benchmark", [sys.executable, "scripts/certify_retrieval_quality.py"]),
        ("gis_benchmark", [sys.executable, "scripts/certify_gis_quality.py"]),
    ]
    for name, command in commands:
        gate, _ = run_gate(name, command)
        gates.append(gate)

    security_cmd = [sys.executable, "scripts/mucaro.py", "security", "status", "--host", "127.0.0.1", "--format", "json"]
    security_process_gate, security_stdout = run_gate("security_profile_command", security_cmd)
    if security_process_gate.status != "PASS":
        gates.append(Gate("security_profile", "FAIL", security_process_gate.detail, security_cmd))
    else:
        security_payload, security_parse_error = parse_json_stdout("security_profile_json", security_stdout)
        if security_parse_error:
            gates.append(Gate("security_profile", "FAIL", security_parse_error.detail, security_cmd))
        elif security_payload.get("production_ready") is True and security_payload.get("status") == "SECURITY_OK":
            gates.append(Gate("security_profile", "PASS", "production_ready=true SECURITY_OK", security_cmd))
        else:
            gates.append(
                Gate(
                    "security_profile",
                    "BLOCKED",
                    f"production_ready={security_payload.get('production_ready')} status={security_payload.get('status')}",
                    security_cmd,
                )
            )

    package_gate, package_stdout = run_gate(
        "package_build",
        [sys.executable, "scripts/mucaro.py", "package", "build", "--format", "json"],
    )
    gates.append(package_gate)
    package_payload = None
    if package_gate.status == "PASS":
        package_payload, parse_error = parse_json_stdout("package_build_json", package_stdout)
        if parse_error:
            gates.append(parse_error)
    manifest_path = None
    if isinstance(package_payload, dict):
        manifest = package_payload.get("manifest")
        if isinstance(manifest, dict) and isinstance(manifest.get("path"), str):
            manifest_path = Path(manifest["path"])
    if manifest_path is None:
        gates.append(Gate("source_artifact_installability", "BLOCKED", "package manifest unavailable"))
    else:
        gate, _ = run_gate(
            "source_artifact_installability",
            [sys.executable, "scripts/certify_source_artifact.py", str(manifest_path)],
        )
        gates.append(gate)

    portable_app: Path | None = None
    if args.portable_runtime_dir is None:
        gates.append(Gate("portable_embedded_runtime", "BLOCKED", "--portable-runtime-dir not supplied"))
    else:
        with tempfile.TemporaryDirectory(prefix="mucaro-release-grade-portable-") as tmp:
            portable_gate, portable_stdout = run_gate(
                "portable_embedded_runtime",
                [
                    sys.executable,
                    "scripts/mucaro.py",
                    "package",
                    "portable-macos-app",
                    "--runtime-dir",
                    str(args.portable_runtime_dir),
                    "--output-dir",
                    tmp,
                    "--format",
                    "json",
                ],
            )
            gates.append(portable_gate)
            if portable_gate.status == "PASS":
                portable_payload, parse_error = parse_json_stdout("portable_embedded_runtime_json", portable_stdout)
                if parse_error:
                    gates.append(parse_error)
                elif isinstance(portable_payload, dict) and isinstance(portable_payload.get("app_path"), str):
                    persistent_dir = ROOT / "data" / "exports" / "release_grade_portable"
                    persistent_dir.mkdir(parents=True, exist_ok=True)
                    source = Path(portable_payload["app_path"])
                    portable_app = persistent_dir / source.name
                    if portable_app.exists():
                        import shutil
                        shutil.rmtree(portable_app)
                    import shutil
                    shutil.copytree(source, portable_app)

    if portable_app is None or args.portable_install_root is None:
        gates.append(Gate("portable_install_reinstall_smoke", "BLOCKED", "portable app or --portable-install-root unavailable"))
    else:
        install_cmd = [
            sys.executable, "scripts/mucaro.py", "package", "portable-install", "install",
            "--source-app", str(portable_app), "--install-root", str(args.portable_install_root), "--format", "json",
        ]
        first, _ = run_gate("portable_install", install_cmd)
        second, _ = run_gate("portable_reinstall", install_cmd)
        gates.extend([first, second])
        gates.append(
            Gate(
                "portable_install_reinstall_smoke",
                "PASS" if first.status == second.status == "PASS" else "FAIL",
                "current portable bundle installed and reinstalled transactionally",
            )
        )

    if portable_app is None or args.previous_portable_app is None or args.portable_install_root is None:
        gates.append(Gate("distinct_version_upgrade_rollback", "BLOCKED", "current/previous portable apps and install root are required"))
    else:
        try:
            current_manifest = json.loads((portable_app / "Contents/Resources/portable-manifest.json").read_text(encoding="utf-8"))
            previous_manifest = json.loads((args.previous_portable_app / "Contents/Resources/portable-manifest.json").read_text(encoding="utf-8"))
            current_head = current_manifest.get("head_sha")
            previous_head = previous_manifest.get("head_sha")
        except Exception as exc:
            gates.append(Gate("distinct_version_upgrade_rollback", "FAIL", f"portable manifests unreadable: {exc}"))
        else:
            if not previous_head or previous_head == current_head:
                gates.append(Gate("distinct_version_upgrade_rollback", "FAIL", "previous portable app must bind to a different non-empty head"))
            else:
                previous_install, _ = run_gate(
                    "previous_version_install",
                    [
                        sys.executable, "scripts/mucaro.py", "package", "portable-install", "install",
                        "--source-app", str(args.previous_portable_app), "--install-root", str(args.portable_install_root), "--format", "json",
                    ],
                )
                upgrade, _ = run_gate(
                    "upgrade_to_current",
                    [
                        sys.executable, "scripts/mucaro.py", "package", "portable-install", "install",
                        "--source-app", str(portable_app), "--install-root", str(args.portable_install_root), "--format", "json",
                    ],
                )
                rollback, _ = run_gate(
                    "rollback_to_previous",
                    [
                        sys.executable, "scripts/mucaro.py", "package", "portable-install", "rollback",
                        "--install-root", str(args.portable_install_root), "--format", "json",
                    ],
                )
                gates.extend([previous_install, upgrade, rollback])
                gates.append(
                    Gate(
                        "distinct_version_upgrade_rollback",
                        "PASS" if previous_install.status == upgrade.status == rollback.status == "PASS" else "FAIL",
                        f"previous_head={previous_head} current_head={current_head}",
                    )
                )

    gates.extend(
        [
            external_gate(
                "clean_machine_macos",
                args.clean_machine_receipt,
                expected_head=head,
                required_true=("install", "launch", "representative_workflow", "reinstall", "rollback"),
            ),
            external_gate(
                "signed_notarized_installer",
                args.signing_receipt,
                expected_head=head,
                required_true=("signed", "notarized", "installer_verified"),
            ),
            external_gate(
                "hosted_ci_current_head",
                args.hosted_ci_receipt,
                expected_head=head,
                required_true=("ci_admitted", "ci_passed", "jobs_executed"),
            ),
        ]
    )

    required_names = {
        "clean_worktree",
        "local_release",
        "fresh_clone",
        "python_distribution",
        "representative_operator_workflow",
        "retrieval_benchmark",
        "gis_benchmark",
        "security_profile",
        "package_build",
        "source_artifact_installability",
        "portable_embedded_runtime",
        "portable_install_reinstall_smoke",
        "distinct_version_upgrade_rollback",
        "clean_machine_macos",
        "signed_notarized_installer",
        "hosted_ci_current_head",
    }
    by_name = {gate.name: gate for gate in gates}
    missing = sorted(required_names - set(by_name))
    for name in missing:
        gates.append(Gate(name, "BLOCKED", "mandatory gate row missing"))
    final_ok = all(by_name.get(name) is not None and by_name[name].status == "PASS" for name in required_names)
    final_status = FINAL_CERTIFIED if final_ok else FINAL_BLOCKED
    receipt_path = write_release_receipt(
        ROOT,
        receipt_type="release_grade",
        status=final_status,
        head_sha=head,
        command=[sys.executable, "scripts/certify_release_grade.py"],
        payload={
            "gates": [gate.as_dict() for gate in gates],
            "required_gate_names": sorted(required_names),
            "blocking_gates": sorted(gate.name for gate in gates if gate.name in required_names and gate.status != "PASS"),
            "certification_rule": "every mandatory gate must be PASS; BLOCKED and FAIL both prohibit release-grade certification",
        },
    )
    print(final_status)
    print(f"RELEASE_GRADE_RECEIPT {receipt_path.relative_to(ROOT)}")
    return 0 if final_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
