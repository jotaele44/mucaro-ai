import json
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app.coding_container import CodingContainerError
from scripts import certify_coding_mutation_live as cert


def test_coding_mutation_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "CODING_MUTATION_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "CODING_MUTATION_LIVE_CERT_FAILED"


def run_main(monkeypatch: pytest.MonkeyPatch, output: Path) -> int:
    monkeypatch.setattr(sys, "argv", ["certify_coding_mutation_live.py", "--image", "img@sha256:" + "a" * 64, "--output", str(output)])
    return cert.main()


@pytest.mark.parametrize("raising", ["plan", "run"])
def test_an_unavailable_engine_still_leaves_a_receipt_that_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], raising: str
) -> None:
    """The preflight raises for a stopped daemon or a missing image; that must not crash the script and lose the receipt."""
    error = CodingContainerError("ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE", "Cannot connect to the Docker daemon")

    def build_plan(*args: Any, **kwargs: Any) -> object:
        if raising == "plan":
            raise error
        return object()

    def run_plan(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(cert, "build_coding_container_plan", build_plan)
    monkeypatch.setattr(cert, "run_coding_container_plan", run_plan)
    output = tmp_path / "receipt.json"

    assert run_main(monkeypatch, output) == 2  # not a pass

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == cert.STATUS_FAILED
    assert {name for name, held in receipt["invariants"].items() if held} == {"host_fixture_unchanged"}  # nothing ran, so nothing else held
    for run in ("positive", "negative", "rollback"):
        execution = receipt[run]["execution"]
        assert execution["execution_status"] == "BLOCKED"  # unavailable infrastructure, not a command that ran and failed
        assert execution["blocked"] == {"status": "ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE", "detail": "Cannot connect to the Docker daemon"}
    assert capsys.readouterr().out.strip() == cert.STATUS_FAILED


def test_a_healthy_engine_is_unaffected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With the engine up the script behaves as before: it never swallows a real receipt."""
    outputs = {
        "apply": {"status": "CODING_MUTATION_APPLIED", "postimage": "beta\n"},
        "preimage-drift": {"expected_rejection": True},
        "rollback": {"expected_rollback": True, "postimage": "alpha\n"},
    }

    def run_plan(plan: Any, *, cfg: Any) -> dict[str, Any]:
        return {
            "execution_status": "PASS",
            "stdout": {"text": json.dumps(outputs[plan])},
            "invariants": {"network_disabled": True, "host_source_read_only": True},
        }

    monkeypatch.setattr(cert, "build_coding_container_plan", lambda root, *, image, command, cfg: command[-1])
    monkeypatch.setattr(cert, "run_coding_container_plan", run_plan)
    output = tmp_path / "receipt.json"

    assert run_main(monkeypatch, output) == 0

    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == cert.STATUS_OK and all(receipt["invariants"].values())
    assert capsys.readouterr().out.strip() == cert.STATUS_OK
