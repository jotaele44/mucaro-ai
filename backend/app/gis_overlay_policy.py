from __future__ import annotations

import hashlib
import json
from typing import Any

OVERLAY_POLICY_ID = "mucaro_overlay_planar_policy_v1"

OVERLAY_POLICY: dict[str, Any] = {
    "policy_id": OVERLAY_POLICY_ID,
    "adapter": "shapely_planar_overlay_v1",
    "input_geometry_types": ["MultiPolygon", "Polygon"],
    "operations": ["difference", "intersection", "symmetric_difference", "union"],
    "result_geometry_types": ["MultiPolygon", "Polygon"],
    "crs_policy": "EXACT_DECLARED_MATCH",
    "matching_policy": "ONE_TO_ONE_BY_FEATURE_KEY",
    "topology_policy": "VALID_INPUT_NO_IMPLICIT_REPAIR",
    "coordinate_dimension_policy": "XY_ONLY_RETAIN_ZM_UNRESOLVED",
    "result_policy": {
        "empty_result": "EMPTY_RESULT_NOT_EMITTED",
        "lower_dimensional_result": "NON_POLYGON_RESULT_NOT_EMITTED",
        "geometry_collection_result": "NON_POLYGON_RESULT_NOT_EMITTED",
    },
}

OVERLAY_POLICY_DECISIONS: dict[str, frozenset[str]] = {
    "EMPTY_INPUT": frozenset({"EMPTY_INPUT_REJECTED"}),
    "UNRESOLVED": frozenset(
        {
            "INVALID_INPUT_REJECTED",
            "COORDINATE_DIMENSION_REJECTED",
            "RUNTIME_UNRESOLVED",
        }
    ),
    "UNSUPPORTED": frozenset({"INPUT_GEOMETRY_REJECTED"}),
    "EMPTY_RESULT": frozenset({"EMPTY_RESULT_NOT_EMITTED"}),
    "NON_POLYGON_RESULT": frozenset({"RESULT_GEOMETRY_REJECTED"}),
    "EMITTED": frozenset({"POLYGONAL_RESULT_EMITTED"}),
}

OVERLAY_POLICY_SHA256 = hashlib.sha256(
    json.dumps(OVERLAY_POLICY, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def overlay_policy_receipt() -> dict[str, Any]:
    return {
        "policy_id": OVERLAY_POLICY_ID,
        "policy_sha256": OVERLAY_POLICY_SHA256,
    }


def overlay_policy_decision_is_allowed(state: Any, decision: Any) -> bool:
    return isinstance(state, str) and isinstance(decision, str) and decision in OVERLAY_POLICY_DECISIONS.get(state, frozenset())
