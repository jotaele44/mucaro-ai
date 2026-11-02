import pytest

from app.evidence_closure import (
    EvidenceInvariantError, adjudicate_identity, capability_delta, certify,
    logical_digest, reconcile_source_rows,
)


def test_capability_sets_are_conserved():
    x = capability_delta(["shared", "a"], ["shared", "b"])
    assert x == {"intersection": ["shared"], "a_only": ["a"], "b_only": ["b"],
                 "union": ["a", "b", "shared"], "symmetric_difference": ["a", "b"]}


def test_identity_does_not_promote_name_or_proximity():
    x = adjudicate_identity([{"candidate_key": "lake-A", "evidence": ["name_only", "proximity"]}])
    assert x["state"] == "UNRESOLVED" and x["winner"] is None


def test_guayabal_toa_vaca_coarse_polygon_false_merge_fixture():
    x = adjudicate_identity([{"candidate_key": "Guayabal", "evidence": ["point_in_polygon"]},
                             {"candidate_key": "Toa Vaca", "evidence": ["point_in_polygon"]}])
    assert x["state"] == "UNRESOLVED" and len(x["candidates"]) == 2


def test_top_evidence_is_only_candidate_not_identity():
    x = adjudicate_identity([{"candidate_key": "a", "evidence": ["stable_id"]},
                             {"candidate_key": "b", "evidence": ["proximity"]}])
    assert x["state"] == "CANDIDATE_NOT_IDENTITY" and x["winner"] == "a"


def test_contradiction_blocks_stable_id_candidate():
    x = adjudicate_identity([{"candidate_key": "a", "evidence": ["stable_id"], "contradiction": True}])
    assert x["state"] == "UNRESOLVED"


def test_duplicate_candidate_fails():
    with pytest.raises(EvidenceInvariantError):
        adjudicate_identity([{"candidate_key": "a"}, {"candidate_key": "a"}])


def test_missing_2008_source_layer_records_block_closure():
    x = reconcile_source_rows(["opa2008:1", "opa2008:2"], [{"source_id": "opa2008:1", "disposition": "RETAINED"}])
    assert x["state"] == "OPEN" and x["missing"] == ["opa2008:2"]


def test_all_source_records_disposed_without_aggregation():
    x = reconcile_source_rows(["s:1", "s:2"], [{"source_id": "s:1", "disposition": "RETAINED"},
                                               {"source_id": "s:2", "disposition": "COMPANION"}])
    assert x["state"] == "PASS" and x["source_count"] == sum(x["counts"].values())


def test_duplicate_routing_detected():
    with pytest.raises(EvidenceInvariantError):
        reconcile_source_rows(["s:1"], [{"source_id": "s:1", "disposition": "RETAINED"},
                                          {"source_id": "s:1", "disposition": "EXCLUDED"}])


def test_required_gate_absence_and_residue_prevent_certification():
    assert certify(["CI", "review"], {"CI": "PASS"})["state"] == "WITHHELD"
    assert certify(["CI"], {"CI": "PASS"}, ["unresolved tie"])["state"] == "WITHHELD"


def test_strict_certification_and_deterministic_receipt():
    x = certify(["review", "CI"], {"review": "PASS", "CI": "PASS"})
    y = certify(["CI", "review"], {"CI": "PASS", "review": "PASS"})
    assert x["state"] == "PASS" and x["receipt_sha256"] == y["receipt_sha256"]
    assert logical_digest({"a": 1, "b": 2}) == logical_digest({"b": 2, "a": 1})


def test_unrecognized_evidence_is_not_accepted():
    with pytest.raises(EvidenceInvariantError):
        adjudicate_identity([{"candidate_key": "a", "evidence": ["magical_evidence"]}])


def test_unexpected_gate_cannot_silently_pass():
    x = certify(["CI"], {"CI": "PASS", "extra": "OPEN"})
    assert x["state"] == "WITHHELD" and x["unexpected_gates"] == ["extra"]
