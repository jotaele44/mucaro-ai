from pathlib import Path

from backend.app import mucaro_100


ROOT = Path(__file__).resolve().parents[2]


def _capability(states: list[str]) -> dict:
    return {
        "status": "MUCARO_V1_DENOMINATOR_AUDIT_OK",
        "requirements": [
            {"id": f"R-{index:03d}", "state": state}
            for index, state in enumerate(states, 1)
        ],
    }


def _release(*, status: str, passed: int) -> dict:
    return {
        "status": status,
        "denominator": {
            "total": 139,
            "arithmetic_closes": True,
            "by_state": {
                "PASS": passed,
                "FAIL": 0,
                "OPEN": 138 - passed,
                "BLOCKED": 0,
                "PROVISIONAL": 0,
                "NOT_APPLICABLE": 1,
            },
        },
        "residue": {},
    }


def test_current_project_uses_exact_frozen_denominators() -> None:
    report = mucaro_100.compute_mucaro_100(ROOT)
    assert report["capability"]["denominator"] == 100
    assert report["capability"]["integrity"] is True
    assert report["release"]["denominator"] == 139
    assert report["release"]["applicable_denominator"] == 138
    assert report["release"]["not_applicable"] == 1


def test_provisional_capability_receives_zero_strict_credit(monkeypatch) -> None:
    monkeypatch.setattr(
        mucaro_100,
        "audit_current_denominator_state",
        lambda _root: _capability(["PASS"] * 99 + ["PROVISIONAL"]),
    )
    monkeypatch.setattr(
        mucaro_100,
        "compute_release_status",
        lambda _root: _release(status=mucaro_100.STATUS_CERTIFIED, passed=138),
    )
    report = mucaro_100.compute_mucaro_100(ROOT)
    assert report["capability"]["strict_score_percent"] == 99.0
    assert report["conditions"]["capability_100_of_100_pass"] is False
    assert report["certified"] is False


def test_capability_100_does_not_override_release_residue(monkeypatch) -> None:
    monkeypatch.setattr(
        mucaro_100,
        "audit_current_denominator_state",
        lambda _root: _capability(["PASS"] * 100),
    )
    monkeypatch.setattr(
        mucaro_100,
        "compute_release_status",
        lambda _root: _release(status="MUCARO_AI_RELEASE_STATUS_PROVISIONAL", passed=137),
    )
    report = mucaro_100.compute_mucaro_100(ROOT)
    assert report["capability"]["strict_score_percent"] == 100.0
    assert report["conditions"]["release_138_of_138_applicable_pass"] is False
    assert report["certified"] is False


def test_terminal_certification_requires_100_and_138(monkeypatch) -> None:
    monkeypatch.setattr(
        mucaro_100,
        "audit_current_denominator_state",
        lambda _root: _capability(["PASS"] * 100),
    )
    monkeypatch.setattr(
        mucaro_100,
        "compute_release_status",
        lambda _root: _release(status=mucaro_100.STATUS_CERTIFIED, passed=138),
    )
    report = mucaro_100.compute_mucaro_100(ROOT)
    assert report["capability"]["strict_score_percent"] == 100.0
    assert report["release"]["strict_score_percent"] == 100.0
    assert report["certified"] is True
    assert report["status"] == mucaro_100.STATUS_MUCARO_100_CERTIFIED
