"""Negative and positive gates for the security gate detectors (INV-016, MATRIX-014)."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

from backend.app import security_gates as sg

ROOT = Path(__file__).resolve().parents[2]


def test_secret_patterns_are_detected_and_clean_text_passes(tmp_path: Path) -> None:
    planted = tmp_path / "leak.txt"
    planted.write_text("key = " + "sk-" + "ant" + "-" + "A" * 30 + "\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see\n", encoding="utf-8")
    assert sg.scan_secret_patterns([planted, clean], tmp_path) == ["leak.txt:6"]


def test_evidence_values_under_secret_keys_are_flagged(tmp_path: Path) -> None:
    leaky = tmp_path / "receipt.json"
    leaky.write_text(json.dumps({"run": {"token": "Zx9" * 8}, "rows": [{"password": "correct horse battery"}]}), encoding="utf-8")
    fine = tmp_path / "ok.json"
    fine.write_text(json.dumps({"token_present": True, "api_key_env": "SOME_ENV_NAME", "token": ""}), encoding="utf-8")
    hits = sg.scan_evidence_secret_fields([leaky, fine], tmp_path)
    assert hits == ["receipt.json:token", "receipt.json:password"]


def _finding(rule: str = "S608", path: str = "a.py", code: str = "x") -> dict:
    return {"rule": rule, "path": path, "line": 1, "code": code}


def test_untriaged_high_and_stale_findings_fail_the_triage() -> None:
    dispositions = {
        "rule_dispositions": [{"rule": "S603", "classification": "LOW"}],
        "finding_dispositions": [
            {"rule": "S608", "path": "a.py", "code": "x", "classification": "LOW"},
            {"rule": "S310", "path": "gone.py", "code": "y", "classification": "LOW"},
            {"rule": "S314", "path": "b.py", "code": "z", "classification": "HIGH"},
        ],
    }
    report = sg.triage_findings([_finding(), _finding("S603", "c.py"), _finding("S105", "d.py"), _finding("S314", "b.py", "z")], dispositions)
    assert [row["rule"] for row in report["untriaged"]] == ["S105"]
    assert report["stale_dispositions"] == ["S310:gone.py"]
    assert [row["rule"] for row in report["blocking_high_or_critical"]] == ["S314"]
    assert report["ok"] is False

    clean = sg.triage_findings([_finding(), _finding("S603", "c.py")], {**dispositions, "finding_dispositions": dispositions["finding_dispositions"][:1]})
    assert clean["ok"] is True and not clean["stale_dispositions"]


def test_repository_dispositions_cover_every_current_finding() -> None:
    dispositions = json.loads((ROOT / "_architecture/release/security/static_security_dispositions_v1.json").read_text(encoding="utf-8"))
    findings = sg.static_security_findings(ROOT, ["backend/app", "packaging", "scripts"])
    report = sg.triage_findings(findings, dispositions)
    assert report["untriaged"] == [] and report["stale_dispositions"] == [] and report["ok"]
    assert sg.shell_true_uses(ROOT, ["backend/app", "packaging", "scripts"]) == []


def test_shell_true_is_detected(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("import subprocess\nsubprocess.run('ls', shell = True)\n", encoding="utf-8")
    assert sg.shell_true_uses(tmp_path, ["pkg"]) == ["pkg/m.py:2"]


def test_lock_integrity_rejects_an_unhashed_entry(tmp_path: Path) -> None:
    lock = tmp_path / "req.lock"
    lock.write_text("alpha==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\nbeta==2.0\n", encoding="utf-8")
    package_lock = tmp_path / "package-lock.json"
    package_lock.write_text(json.dumps({"packages": {"": {}, "node_modules/x": {"version": "1", "integrity": "sha512-AAAA"}}}), encoding="utf-8")
    report = sg.lock_integrity(lock, package_lock)
    assert report["python_unhashed"] == ["beta"] and report["ok"] is False


def test_sboms_are_deterministic_and_scope_the_gui_runtime() -> None:
    lock = ROOT / "packaging/macos/requirements-macos-arm64.lock"
    first = sg.python_sbom(lock, root=ROOT, app_version="0.0.0")
    assert first == sg.python_sbom(lock, root=ROOT, app_version="0.0.0")
    names = {row["name"] for row in first["components"]}
    assert {"fastapi", "pywebview", "pyinstaller"} <= names
    assert all(row["purl"].startswith("pkg:pypi/") and row["hashes"] for row in first["components"])

    gui = sg.npm_sbom(ROOT / "frontend/package-lock.json", root=ROOT, runtime_roots=["react", "react-dom"], app_version="0.0.0")
    scopes = {row["name"]: row["scope"] for row in gui["components"]}
    assert scopes["react"] == scopes["react-dom"] == scopes["scheduler"] == "required"
    assert scopes["vite"] == "excluded"


def test_loopback_only_classification() -> None:
    assert sg.loopback_only(["127.0.0.1:8000", "::1:8000"])
    assert not sg.loopback_only(["0.0.0.0:8000"])
    assert not sg.loopback_only(["192.168.1.4:8000", "127.0.0.1:1"])
    assert not sg.loopback_only([])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_listening_addresses_see_a_real_all_interfaces_bind() -> None:
    loopback = socket.socket()
    public = socket.socket()
    try:
        loopback.bind(("127.0.0.1", 0))
        loopback.listen()
        public.bind(("0.0.0.0", 0))
        public.listen()
        addresses = sg.listening_addresses(os.getpid())
        assert f"127.0.0.1:{loopback.getsockname()[1]}" in addresses
        assert f"0.0.0.0:{public.getsockname()[1]}" in addresses
        assert not sg.loopback_only(addresses)
    finally:
        loopback.close()
        public.close()


def test_expat_refuses_amplification() -> None:
    assert sg.xml_amplification_protected()["ok"] is True


def test_linux_self_lookup_never_substitutes_for_another_pid(tmp_path, monkeypatch):
    own = tmp_path / "self"
    other = tmp_path / "other"
    for base, inode, port in [(own, "123", "1F40"), (other, "456", "1F41")]:
        (base / "fd").mkdir(parents=True)
        (base / "net").mkdir()
        (base / "fd" / "3").symlink_to(f"socket:[{inode}]")
        (base / "net" / "tcp").write_text(
            "header\n" + f"0: 0100007F:{port} 00000000:0000 0A 0 0 0 0 0 {inode}\n"
        )
    seen = []
    def proc_path(value):
        seen.append(value)
        return own if value == "/proc/self" else other
    monkeypatch.setattr(sg, "Path", proc_path)
    assert sg._linux_listening(os.getpid()) == ["127.0.0.1:8000"]
    assert sg._linux_listening(os.getpid() + 10000) == ["127.0.0.1:8001"]
    assert seen == ["/proc/self", f"/proc/{os.getpid() + 10000}"]
