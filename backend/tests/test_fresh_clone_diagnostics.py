"""A failed subprocess must leave enough evidence to diagnose the retained clone."""

import json
import subprocess
import sys

from scripts import certify_fresh_clone as certification


def test_failed_install_step_retains_its_output_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(certification, "ROOT", tmp_path)
    monkeypatch.setattr(certification.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path / "kept"))
    monkeypatch.setattr(certification, "clone_ref", lambda *args: None)
    monkeypatch.setattr(certification, "git_head", lambda *args: "a" * 40)
    output = "x" * 4500 + "\nFAILED specific_test: accounting invalid\n"

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["fixture-step"], output=output)

    monkeypatch.setattr(certification, "run", fail)
    monkeypatch.setattr(
        sys, "argv", ["certify_fresh_clone.py", "--source", "local-fixture", "--ref", "a" * 40, "--keep"]
    )
    assert certification.main() == 2
    receipt = json.loads(next((tmp_path / "data/exports/release_receipts").glob("fresh_clone-*.json")).read_bytes())
    assert receipt["status"] == "FRESH_CLONE_CERT_FAILED"
    assert receipt["payload"]["error_output_tail"] == output[-4000:]
