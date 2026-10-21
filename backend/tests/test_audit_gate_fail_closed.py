import json
from types import SimpleNamespace

import pytest

from scripts import certify_security_gates as gates


@pytest.mark.parametrize("report,returncode", [
    ({}, 0),
    ({"error": {"code": "ENOAUDIT"}}, 1),
    ({"vulnerabilities": {}, "metadata": {"dependencies": {"total": 1}}}, 2),
    ({"vulnerabilities": {}, "metadata": {"dependencies": {"total": 0}}}, 0),
])
def test_npm_scanner_errors_never_certify_clean(tmp_path, monkeypatch, report, returncode):
    monkeypatch.setattr(gates.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps(report), stderr="", returncode=returncode,
    ))
    assert gates.npm_vulnerability_gate(tmp_path, {})["status"] == "FAIL"


@pytest.mark.parametrize("report,returncode", [
    ({}, 0), ([], 0), ({"dependencies": []}, 0),
    ({"dependencies": [{"name": "example", "version": "1", "vulns": []}]}, 2),
])
def test_python_scanner_errors_never_certify_clean(tmp_path, monkeypatch, report, returncode):
    monkeypatch.setattr(gates.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout=json.dumps(report), stderr="", returncode=returncode,
    ))
    assert gates.python_vulnerability_gate(tmp_path, {})["status"] == "FAIL"
