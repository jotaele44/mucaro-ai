from backend.app.gis_overlay_policy import (
    OVERLAY_POLICY,
    OVERLAY_POLICY_ID,
    OVERLAY_POLICY_SHA256,
    OVERLAY_POLICY_DECISIONS,
    overlay_policy_decision_is_allowed,
    overlay_policy_receipt,
)


def test_overlay_policy_receipt_is_deterministic_and_complete():
    assert OVERLAY_POLICY["policy_id"] == OVERLAY_POLICY_ID
    assert set(OVERLAY_POLICY["operations"]) == {
        "difference",
        "intersection",
        "symmetric_difference",
        "union",
    }
    assert OVERLAY_POLICY["coordinate_dimension_policy"] == "XY_ONLY_RETAIN_ZM_UNRESOLVED"
    assert overlay_policy_decision_is_allowed("EMITTED", "POLYGONAL_RESULT_EMITTED")
    assert not overlay_policy_decision_is_allowed("EMPTY_RESULT", "POLYGONAL_RESULT_EMITTED")
    assert set(OVERLAY_POLICY_DECISIONS) == {
        "EMPTY_INPUT",
        "UNRESOLVED",
        "UNSUPPORTED",
        "EMPTY_RESULT",
        "NON_POLYGON_RESULT",
        "EMITTED",
    }
    assert overlay_policy_receipt() == {
        "policy_id": OVERLAY_POLICY_ID,
        "policy_sha256": OVERLAY_POLICY_SHA256,
    }
