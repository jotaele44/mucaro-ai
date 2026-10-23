"""Negative acceptance tests for the derived MUCARO_100 closure register.

All malformed ledgers are deep-copied in memory. No canonical file is edited.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "scripts" / "build_mucaro100_closure_register.py"


@pytest.fixture()
def builder(monkeypatch):
    spec = importlib.util.spec_from_file_location("_mucaro_closure_under_test", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    canonical = {
        "frozen": copy.deepcopy(module.load_denominator(ROOT)),
        "projection": copy.deepcopy(module.audit_current_denominator_state(ROOT)),
        "events": copy.deepcopy(module.load_denominator_events(ROOT)),
        "release": copy.deepcopy(module._load("_architecture/release/acceptance_ledger_v1.json")),
        "scope": copy.deepcopy(module._load("_architecture/release/release_scope_manifest_v1.json")),
        "blockers": copy.deepcopy(module._load("_architecture/release/blocker_ledger_v1.json")),
    }

    monkeypatch.setattr(module, "load_denominator", lambda _root: canonical["frozen"])
    monkeypatch.setattr(module, "audit_current_denominator_state", lambda _root: canonical["projection"])
    monkeypatch.setattr(module, "load_denominator_events", lambda _root: canonical["events"])
    monkeypatch.setattr(module, "_git", lambda *_args: "fixed-source-identity")

    def fake_load(path):
        if path.endswith("acceptance_ledger_v1.json"):
            return canonical["release"]
        if path.endswith("release_scope_manifest_v1.json"):
            return canonical["scope"]
        if path.endswith("blocker_ledger_v1.json"):
            return canonical["blockers"]
        raise AssertionError(path)

    monkeypatch.setattr(module, "_load", fake_load)
    return module, canonical


def test_positive_preserves_denominators_and_never_authenticates_evidence(builder):
    module, _ = builder
    report = module.build_register()
    assert report["capability_event_audit"]["non_pass_count"] == 40
    assert report["release_acceptance"]["non_pass_applicable_count"] == 77
    assert len(report["blocker_ledger"]) == 6
    assert all(
        e["independent_evidence_verification"] == "NOT_ASSESSED"
        for row in report["capability_event_audit"]["requirements"]
        for e in row["events"]
    )
    assert all(
        row["evidence_verification"] == "NOT_ASSESSED"
        for row in report["release_acceptance"]["requirements"]
    )


@pytest.mark.parametrize("group,field", [
    ("frozen", "requirements"),
    ("projection", "requirements"),
    ("release", "rows"),
    ("scope", "requirements"),
    ("blockers", "blockers"),
    ("events", "events"),
])
def test_duplicate_id_rejected(builder, group, field):
    module, data = builder
    rows = data[group][field]
    rows[1] = copy.deepcopy(rows[0])
    with pytest.raises(ValueError):
        module.build_register()


@pytest.mark.parametrize("group,field", [
    ("frozen", "requirements"),
    ("projection", "requirements"),
    ("release", "rows"),
    ("scope", "requirements"),
    ("blockers", "blockers"),
    ("events", "events"),
])
def test_missing_record_rejected(builder, group, field):
    module, data = builder
    data[group][field].pop()
    with pytest.raises(ValueError):
        module.build_register()


def test_projection_audit_failure_rejected(builder):
    module, data = builder
    data["projection"]["status"] = "MUCARO_V1_DENOMINATOR_AUDIT_FAILED"
    with pytest.raises(ValueError, match="projection failed"):
        module.build_register()


def test_unapplied_event_identity_rejected(builder):
    module, data = builder
    data["projection"]["applied_events"][0]["event_id"] = "not-in-ledger"
    with pytest.raises(ValueError, match="applied event identities"):
        module.build_register()


def test_release_scope_state_disagreement_rejected(builder):
    module, data = builder
    data["scope"]["requirements"][0]["scope_state"] = "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="scope_state mismatch"):
        module.build_register()


def test_removed_na_credit_rejected(builder):
    module, data = builder
    row = next(r for r in data["release"]["rows"] if r["state"] == "NOT_APPLICABLE")
    row["state"] = "PASS"
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        module.build_register()


def test_additional_na_credit_rejected(builder):
    module, data = builder
    row = next(r for r in data["release"]["rows"] if r["state"] == "OPEN")
    row["state"] = "NOT_APPLICABLE"
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        module.build_register()


def test_output_must_not_be_inside_source_worktree(builder, monkeypatch):
    module, _ = builder
    monkeypatch.setattr(
        sys, "argv",
        ["build_mucaro100_closure_register.py", "--output", str(ROOT / "closure-register.json")],
    )
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 2
    assert not (ROOT / "closure-register.json").exists()
