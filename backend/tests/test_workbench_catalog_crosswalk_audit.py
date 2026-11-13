"""Protect the unbound Workbench lane catalog from accidental equivalence claims.

This audit tests source declarations only; it is NOT an execution test or release certification.
"""
import json
from pathlib import Path

from backend.app.execution_workbench import LANES, create_plan


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "_architecture" / "workbench_catalog_crosswalk_audit_20261102.json"
ENTRY = ROOT / "_architecture" / "execution_entry_denominator_v1.json"


def test_catalog_crosswalk_preserves_all_unresolved_lanes():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    rows = audit["lanes"]
    assert audit["snapshot_source_commit"] == "f8709cc9f8299ca7f9827dd204693912b1cb7511"
    assert audit["state"] == "AUDIT_ONLY"
    assert audit["catalog_status"] == "UNBOUND"
    assert audit["lane_count"] == len(rows) == len(LANES) == 20
    assert [row["lane_id"] for row in rows] == list(LANES)
    assert all(row["status"] == "UNRESOLVED_CATALOG" for row in rows)
    assert all(row["executable_test_id"] is None for row in rows)
    assert all(row["authoritative_catalog_ref"] is None for row in rows)
    assert len({row["lane_id"] for row in rows}) == 20
    assert audit["release_certification"] == "BLOCKED"


def test_goal_plan_entry_denominator_is_not_workbench_lane_identity():
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    entry = json.loads(ENTRY.read_text(encoding="utf-8"))
    ids = [case["case_id"] for case in entry["cases"]]
    assert len(ids) == len(set(ids)) == entry["expected_count"] == 28
    assert sum(x.startswith("P") for x in ids) == entry["positive_count"] == 8
    assert sum(x.startswith("N") for x in ids) == entry["negative_count"] == 20
    assert set(ids).isdisjoint(LANES)
    assert audit["execution_entry_denominator"]["identity_relation_to_workbench_lanes"] == "UNRESOLVED_NOT_EQUIVALENT_BY_LABEL"


def test_no_lane_becomes_executable_from_catalog_audit():
    plan = create_plan("maximum", "offline", "unavailable", ["macos"])
    assert plan.catalog_status == "UNBOUND"
    assert plan.blocked_lanes == LANES
    assert plan.certification == "BLOCKED"
    assert all(status == "UNRESOLVED_CATALOG" for _, status in plan.lane_status)
