import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import certify_coding_container_live as cert

IMAGE = "ghcr.io/jotaele44/mucaro-coding@sha256:" + "a" * 64


def test_live_cert_constants_are_fail_closed() -> None:
    assert cert.STATUS_OK == "CODING_CONTAINER_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "CODING_CONTAINER_LIVE_CERT_FAILED"


def run_main(monkeypatch: pytest.MonkeyPatch, suite: dict[str, Any], output: Path) -> int:
    monkeypatch.setattr(sys, "argv", ["certify_coding_container_live.py", "--image", IMAGE, "--output", str(output)])
    monkeypatch.setattr(cert, "execute_coding_check_suite", lambda *args, **kwargs: suite)
    return cert.main()


def test_a_blocked_suite_still_leaves_a_receipt_instead_of_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A BLOCKED row has `execution_receipt: None`; reading the engine from it must not end the script with a traceback."""
    blocked = {
        "execution_status": "BLOCKED",
        "state_counts": {"PASS": 0, "FAIL": 0, "BLOCKED": 3},
        "required_failure_count": 0,
        "required_blocked_count": 3,
        "checks": [
            {"check_id": name, "state": "BLOCKED", "error_status": "ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE", "execution_receipt": None}
            for name in ("live_python", "ephemeral_write", "network_denied")
        ],
    }
    output = tmp_path / "receipt.json"

    assert run_main(monkeypatch, blocked, output) == 2  # not a pass

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == cert.STATUS_FAILED and receipt["suite"]["execution_status"] == "BLOCKED"
    assert receipt["invariants"]["suite_pass"] is False and receipt["invariants"]["all_required_terminal_pass"] is False
    assert receipt["identity"]["coding_image_digest"] == "sha256:" + "a" * 64
    assert capsys.readouterr().out.splitlines()[0] == cert.STATUS_FAILED


def test_a_passing_suite_is_unaffected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    passing = {
        "execution_status": "PASS",
        "state_counts": {"PASS": 3, "FAIL": 0, "BLOCKED": 0},
        "required_failure_count": 0,
        "required_blocked_count": 0,
        "checks": [{"check_id": "live_python", "state": "PASS", "execution_receipt": {"engine": "/usr/bin/docker"}}],
    }
    output = tmp_path / "receipt.json"

    assert run_main(monkeypatch, passing, output) == 0

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == cert.STATUS_OK and all(receipt["invariants"].values())
    assert capsys.readouterr().out.splitlines()[0] == cert.STATUS_OK
