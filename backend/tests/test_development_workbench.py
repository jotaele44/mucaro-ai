"""Standalone workbench compiler controls; no network, database or provider needed."""
import pytest

from backend.app.development_workbench import WorkbenchPlanError, compile_plan


def test_same_inputs_same_digest_and_conserved_features():
    left = compile_plan("Build offline workbench", features=["GIS", "RAG", "GIS"])
    right = compile_plan("Build offline workbench", features=["RAG", "GIS"])
    assert left == right
    assert left["feature_count"] == 2
    assert left["status"] == "CANDIDATE_NOT_EXECUTION"
    assert left["certification"] == "BLOCKED"
    assert all(left["invariants"].values())


def test_approval_is_not_execution():
    result = compile_plan("Implement native app", approval=True)
    assert result["operator_approval_recorded"] is True
    assert result["execution_authorized"] is False
    assert all(row["status"] == "NOT_EXECUTED" for row in result["batches"])


def test_missing_commit_fails_evidence_gate():
    result = compile_plan("Create build")
    assert result["source_commit"] is None
    assert "source_commit" in result["missing_evidence"]
    assert result["certification"] == "BLOCKED"


@pytest.mark.parametrize("kwargs", [
    {"objective": ""}, {"objective": "x", "source_commit": "main"},
    {"objective": "x", "features": [""]}, {"objective": "x", "features": ["ok", 3]},
    {"objective": "x" * 20001},
])
def test_invalid_inputs_fail_closed(kwargs):
    with pytest.raises(WorkbenchPlanError):
        compile_plan(**kwargs)
