from backend.app.gis_overlay_policy import (
    OVERLAY_POLICY,
    OVERLAY_POLICY_ID,
    OVERLAY_POLICY_SHA256,
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
    assert overlay_policy_receipt() == {
        "policy_id": OVERLAY_POLICY_ID,
        "policy_sha256": OVERLAY_POLICY_SHA256,
    }
