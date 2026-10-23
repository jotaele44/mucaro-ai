"""Structural regressions for the Mucaro closure dependency backlog planner."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts" / "build_mucaro100_dependency_backlog.py"


@pytest.fixture()
def planner():
    spec = importlib.util.spec_from_file_location("mucaro_dependency_under_test", PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def example_register():
    cap = [{"requirement_id": f"C-{i:03}", "projected_state": "PASS" if i < 63 else "OPEN"}
           for i in range(100)]
    rel = [{"requirement_id": f"TGT-{i:03}", "state": "PASS" if i < 61 else "OPEN",
            "scope_state": "IN_SCOPE", "dependencies": []}
           for i in range(138)]
    rel.append({"requirement_id": "LEARN-GATE-001", "state": "NOT_APPLICABLE",
                "scope_state": "NOT_APPLICABLE", "dependencies": []})
    blockers = [{"blocker_id": f"BLK-{i}"} for i in range(6)]
    return {
        "capability_event_audit": {"requirements": cap, "non_pass_count": 37},
        "release_acceptance": {"requirements": rel, "non_pass_applicable_count": 77},
        "blocker_ledger": blockers,
        "source": {"commit": "fixture"},
    }


def test_exact_counts_and_no_fabricated_edges(planner, example_register):
    result = planner.plan(example_register, "fixture-digest")
    assert result["counts"]["capability_non_pass"] == 37
    assert result["counts"]["release_non_pass"] == 77
    assert result["counts"]["task_occurrences"] == 114
    assert result["declared_edges"] == []
    assert result["register_sha256"] == "fixture-digest"


def test_declared_id_dependency_preserved(planner, example_register):
    example_register["release_acceptance"]["requirements"][61]["dependencies"] = ["C-063"]
    result = planner.plan(example_register, "fixture")
    assert {"from": "C-063", "to": "TGT-061", "kind": "DECLARED_ID"} in result["declared_edges"]


def test_unknown_dependency_retained_for_review(planner, example_register):
    example_register["release_acceptance"]["requirements"][61]["dependencies"] = ["unknown-dependency"]
    result = planner.plan(example_register, "fixture")
    assert result["unresolved_dependency_references"][0]["resolution"] == "REVIEW_REQUIRED"


def test_missing_blocker_fails_closed(planner, example_register):
    example_register["release_acceptance"]["requirements"][61]["blocker_id"] = "BLK-NOT-FOUND"
    with pytest.raises(ValueError, match="missing blocker"):
        planner.plan(example_register, "fixture")


def test_duplicate_requirement_fails_closed(planner, example_register):
    example_register["capability_event_audit"]["requirements"][99]["requirement_id"] = "C-001"
    with pytest.raises(ValueError, match="duplicate identity"):
        planner.plan(example_register, "fixture")


def test_changed_denominator_fails_closed(planner, example_register):
    example_register["release_acceptance"]["requirements"].pop()
    with pytest.raises(ValueError, match="canonical denominators"):
        planner.plan(example_register, "fixture")


def test_mismatched_count_fails_closed(planner, example_register):
    example_register["capability_event_audit"]["non_pass_count"] = 40
    with pytest.raises(ValueError, match="capability arithmetic"):
        planner.plan(example_register, "fixture")
