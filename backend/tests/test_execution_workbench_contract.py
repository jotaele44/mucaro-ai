from app.execution_workbench import LANES, create_plan, plan_json
import pytest

def test_maximum_plan_is_complete_and_nonexecuting():
    plan = create_plan("maximum", "isolated", "unavailable", ["macos"])
    assert len(plan.lanes) == 20 and plan.lanes == LANES
    assert plan.state == "PLAN_ONLY"
    assert plan.certification == "BLOCKED"
    assert plan.catalog_status == "UNBOUND"
    assert plan.catalog_ref is None
    assert plan.blocked_lanes == LANES
    assert plan.lane_status == tuple((lane, "UNRESOLVED_CATALOG") for lane in LANES)
    rendered = plan_json(plan)
    assert '"certification": "BLOCKED"' in rendered
    assert '"catalog_status": "UNBOUND"' in rendered

def test_order_is_canonical():
    assert create_plan("maximum", "offline", "unknown", ["windows", "linux"]).platforms == ("linux", "windows")

@pytest.mark.parametrize("profile,network,hardware,platforms", [
    ("maximum", "internet", "unavailable", ["macos"]),
    ("all", "isolated", "unavailable", ["macos"]),
    ("maximum", "isolated", "invalid", ["macos"]),
    ("maximum", "isolated", "unknown", []),
    ("maximum", "isolated", "unknown", ["macos", "macos"]),
    ("maximum", "isolated", "unknown", ["ios"]),
])
def test_invalid_config_fails_closed(profile, network, hardware, platforms):
    with pytest.raises(ValueError):
        create_plan(profile, network, hardware, platforms)
