#!/usr/bin/env python3
"""Run the measurable security gates and freeze SBOMs (INV-016, MATRIX-014, TGT-024, TGT-025).

Writes data/exports/security/<head12>/ (SBOMs, raw scanner output) and a release receipt
of type ``security_gates``. Exit 0 only when every gate passes. Passing means each named
measurement held; it is not a claim that no unknown vulnerability exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.release_receipts import write_release_receipt
from backend.app.security_gates import (
    listening_addresses,
    lock_integrity,
    loopback_only,
    npm_sbom,
    python_sbom,
    scan_evidence_secret_fields,
    scan_secret_patterns,
    shell_true_uses,
    static_security_findings,
    triage_findings,
    xml_amplification_protected,
)

LOCK = ROOT / "packaging" / "macos" / "requirements-macos-arm64.lock"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"
DISPOSITIONS = ROOT / "_architecture" / "release" / "security" / "static_security_dispositions_v1.json"
STATIC_TARGETS = ["backend/app", "packaging", "scripts"]
SECURITY_REGRESSIONS = [
    "backend/tests/test_api_auth_boundary.py",
    "backend/tests/test_ingestion_safety.py",
    "backend/tests/test_prompt_injection.py",
    "backend/tests/test_permissions.py",
    "backend/tests/test_tools.py",
    "backend/tests/test_security.py",
    "backend/tests/test_web_browsing.py",
    "backend/tests/test_desktop_shell.py",
    "backend/tests/test_contracts.py",
    "backend/tests/test_db_upgrade.py",
]


def gate(name: str, ok: bool, detail: Any, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail, **extra}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def secrets_gates() -> list[dict[str, Any]]:
    tracked = [ROOT / line for line in git("ls-files").splitlines() if line and (ROOT / line).is_file()]
    evidence = sorted((ROOT / "_architecture" / "release" / "evidence").rglob("*.json"))
    exports = ROOT / "data" / "exports"
    if exports.is_dir():
        evidence += sorted(path for path in exports.rglob("*.json") if "node_modules" not in path.parts)
    pattern_hits = scan_secret_patterns(tracked + evidence, ROOT)
    field_hits = scan_evidence_secret_fields(evidence, ROOT)
    return [
        gate("secret_scan", not pattern_hits, pattern_hits or f"{len(tracked)} tracked files and {len(evidence)} evidence files clean"),
        gate("evidence_secret_fields", not field_hits, field_hits or "no evidence value stored under a secret-like key"),
    ]


def static_gate() -> dict[str, Any]:
    findings = static_security_findings(ROOT, STATIC_TARGETS)
    dispositions = json.loads(DISPOSITIONS.read_text(encoding="utf-8"))
    triage = triage_findings(findings, dispositions)
    shell = shell_true_uses(ROOT, STATIC_TARGETS)
    ok = triage["ok"] and not triage["stale_dispositions"] and not shell
    detail = {key: triage[key] for key in ("findings", "by_classification", "untriaged", "stale_dispositions")}
    detail["shell_true_uses"] = shell
    return gate("static_security_triage", ok, detail)


def python_vulnerability_gate(out: Path, dispositions: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-r", str(LOCK), "--disable-pip", "--format", "json", "--progress-spinner", "off"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return gate("python_vulnerabilities", False, f"pip-audit produced no report: {proc.stderr.strip()[-400:]}")
    (out / "pip-audit-macos-lock.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    accepted = {row["id"] for row in dispositions.get("vulnerability_dispositions", [])}
    found = [
        {"package": dep["name"], "version": dep["version"], "id": vuln["id"], "fix_versions": vuln.get("fix_versions", [])}
        for dep in report.get("dependencies", [])
        for vuln in dep.get("vulns", [])
    ]
    unresolved = [row for row in found if row["id"] not in accepted]
    audited = len(report.get("dependencies", []))
    return gate("python_vulnerabilities", not unresolved and audited > 0, {"audited": audited, "known": found, "unresolved": unresolved})


def npm_vulnerability_gate(out: Path, dispositions: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(["npm", "audit", "--omit=dev", "--json"], cwd=ROOT / "frontend", capture_output=True, text=True, check=False)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return gate("npm_vulnerabilities", False, f"npm audit produced no report: {proc.stderr.strip()[-400:]}")
    (out / "npm-audit-frontend.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    counts = report.get("metadata", {}).get("vulnerabilities", {})
    accepted = {row["id"] for row in dispositions.get("vulnerability_dispositions", [])}
    unresolved = sorted(name for name in report.get("vulnerabilities", {}) if f"npm:{name}" not in accepted)
    return gate("npm_vulnerabilities", not unresolved, {"counts": counts, "unresolved": unresolved})


def network_exposure_gate() -> dict[str, Any]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("MUCARO_AI_")}
    env["PYTHONPATH"] = str(ROOT)
    with tempfile.TemporaryDirectory(prefix="mucaro-exposure-") as tmp:
        data_dir = Path(tmp) / "data"
        proc = subprocess.Popen([sys.executable, "-m", "backend.app.desktop_shell", "--no-window", "--data-dir", str(data_dir)], cwd=tmp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            instance_path = data_dir / "instance.json"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not instance_path.exists():
                if proc.poll() is not None:
                    return gate("network_exposure", False, f"service exited early: {proc.stderr.read().decode()[-400:] if proc.stderr else ''}")
                time.sleep(0.1)
            security: dict[str, Any] = {}
            while time.monotonic() < deadline and not security:
                try:
                    security = json.loads(instance_path.read_text(encoding="utf-8")).get("security") or {}
                except (OSError, json.JSONDecodeError):
                    security = {}
                time.sleep(0.1)
            addresses = listening_addresses(proc.pid)
            modes = {
                "data_dir": oct(stat.S_IMODE(data_dir.stat().st_mode)),
                "instance_json": oct(stat.S_IMODE(instance_path.stat().st_mode)),
            }
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
    ok = (
        loopback_only(addresses)
        and modes == {"data_dir": "0o700", "instance_json": "0o600"}
        and security.get("production_ready") is True
    )
    return gate("network_exposure", ok, {"listening": addresses, "file_modes": modes, "runtime_security": security})


def permission_boundary_gate() -> dict[str, Any]:
    from backend.app.tools import TOOL_REGISTRY

    remote_without_approval = [tool.name for tool in TOOL_REGISTRY.values() if tool.network != "none" and not tool.requires_approval]
    mutating_without_approval = [tool.name for tool in TOOL_REGISTRY.values() if tool.mutates_state and not tool.requires_approval]
    ok = not remote_without_approval and not mutating_without_approval
    return gate(
        "permission_boundaries",
        ok,
        {
            "tools": len(TOOL_REGISTRY),
            "remote_tools": sorted(tool.name for tool in TOOL_REGISTRY.values() if tool.network != "none"),
            "remote_without_approval": remote_without_approval,
            "mutating_without_approval": mutating_without_approval,
        },
    )


def regression_gate() -> dict[str, Any]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *SECURITY_REGRESSIONS], cwd=ROOT, capture_output=True, text=True, check=False)
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else f"returncode={proc.returncode}"
    return gate("security_regressions", proc.returncode == 0, summary, suites=SECURITY_REGRESSIONS)


def main() -> int:
    head = git("rev-parse", "HEAD")
    out = ROOT / "data" / "exports" / "security" / head[:12]
    out.mkdir(parents=True, exist_ok=True)
    version = str(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
    dispositions = json.loads(DISPOSITIONS.read_text(encoding="utf-8"))

    boms = {
        "python_macos_arm64": ("sbom-python-macos-arm64.cdx.json", python_sbom(LOCK, root=ROOT, app_version=version)),
        "gui_bundle": ("sbom-gui.cdx.json", npm_sbom(PACKAGE_LOCK, root=ROOT, runtime_roots=["react", "react-dom"], app_version=version)),
    }
    sbom_rows: dict[str, dict[str, Any]] = {}
    for key, (name, bom) in boms.items():
        path = out / name
        text = json.dumps(bom, indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
        components = bom["components"]
        sbom_rows[key] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "components": len(components),
            "required_components": sum(1 for row in components if row.get("scope") == "required"),
        }

    integrity = lock_integrity(LOCK, PACKAGE_LOCK)
    gates = [
        *secrets_gates(),
        static_gate(),
        gate("dependency_integrity", integrity["ok"], integrity),
        gate("sbom", all(row["components"] > 0 for row in sbom_rows.values()), sbom_rows),
        python_vulnerability_gate(out, dispositions),
        npm_vulnerability_gate(out, dispositions),
        network_exposure_gate(),
        permission_boundary_gate(),
        gate("xml_amplification_guard", ok=xml_amplification_protected()["ok"], detail=xml_amplification_protected()),
        regression_gate(),
    ]
    ok = all(row["status"] == "PASS" for row in gates)
    status = "SECURITY_GATES_PASS" if ok else "SECURITY_GATES_FAIL"
    receipt = write_release_receipt(
        ROOT,
        receipt_type="security_gates",
        status=status,
        head_sha=head,
        command=[sys.executable, "scripts/certify_security_gates.py"],
        payload={
            "gates": gates,
            "sbom": sbom_rows,
            "blocking_gates": [row["name"] for row in gates if row["status"] != "PASS"],
            "scope_note": "Measured gates only; no claim that unknown vulnerabilities are absent (INV-016).",
        },
    )
    for row in gates:
        print(f"{row['status']} {row['name']}")
    print(f"SECURITY_RECEIPT {receipt.relative_to(ROOT)}")
    print(status)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
