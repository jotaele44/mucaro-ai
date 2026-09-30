import json
from pathlib import Path

import pytest

import scripts.capture_final_governance as governance


def test_governance_receipt_binds_head_and_conserves_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "checkout"
    ledger_path = root / governance.LEDGER
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps({"rows": [{"state": "OPEN"}, {"state": "PASS"}]}), encoding="utf-8")
    head = "a" * 40
    monkeypatch.setattr(governance, "ROOT", root)
    monkeypatch.setattr(governance, "_git", lambda *args: head if args == ("rev-parse", "HEAD") else "")
    by_state = {state: int(state in {"OPEN", "PASS"}) for state in governance.REQUIREMENT_STATES}
    monkeypatch.setattr(governance, "compute_release_status", lambda root: {
        "status": "MUCARO_AI_RELEASE_STATUS_PROVISIONAL",
        "validation": {"status": "RELEASE_SCOPE_VALID"},
        "denominator": {"by_state": by_state, "total": 2, "arithmetic_closes": True},
    })
    monkeypatch.setattr(governance, "capture_state_snapshot", lambda root: {
        "repository": {"head_sha": head, "dirty_paths": []}, "credentials": []
    })
    output = tmp_path / "receipt.json"
    receipt = governance.capture_governance(head, output)
    assert receipt["acceptance_ledger"]["rows"] == 2
    assert receipt["acceptance_ledger"]["counts"] == by_state
    assert receipt["head_sha"] == head
    assert receipt["status"] == "GOVERNANCE_CAPTURE_VALID_RELEASE_OPEN"
    assert json.loads(output.read_text(encoding="utf-8")) == receipt


@pytest.mark.parametrize("failure", ["wrong_head", "dirty", "inside_repo", "denominator"])
def test_governance_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    root = tmp_path / "checkout"
    ledger_path = root / governance.LEDGER
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(json.dumps({"rows": [{"state": "OPEN"}]}), encoding="utf-8")
    head = "a" * 40
    monkeypatch.setattr(governance, "ROOT", root)
    def fake_git(*args: str) -> str:
        if args == ("rev-parse", "HEAD"):
            return "b" * 40 if failure == "wrong_head" else head
        return " M tracked.txt" if failure == "dirty" else ""

    monkeypatch.setattr(governance, "_git", fake_git)
    by_state = {state: int(state == "OPEN") for state in governance.REQUIREMENT_STATES}
    if failure == "denominator":
        by_state["OPEN"] = 0
    monkeypatch.setattr(governance, "compute_release_status", lambda root: {
        "status": "MUCARO_AI_RELEASE_STATUS_PROVISIONAL",
        "validation": {"status": "RELEASE_SCOPE_VALID"},
        "denominator": {"by_state": by_state},
    })
    monkeypatch.setattr(governance, "capture_state_snapshot", lambda root: {
        "repository": {"head_sha": head, "dirty_paths": []}
    })
    output = root / "receipt.json" if failure == "inside_repo" else tmp_path / "receipt.json"
    with pytest.raises(ValueError):
        governance.capture_governance(head, output)
    assert not output.exists()
