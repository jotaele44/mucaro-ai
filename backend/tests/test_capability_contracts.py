from backend.app.capability_contracts import (
    CAPABILITY_CONTRACT_SCHEMA_VERSION,
    build_capability_contract_report,
)


def test_capability_contracts_cover_expanded_scope_without_overclaiming():
    report = build_capability_contract_report()

    assert report["status"] == "ADVANCED_CAPABILITY_CONTRACTS_READY"
    assert report["schema_version"] == CAPABILITY_CONTRACT_SCHEMA_VERSION
    assert report["contract_count"] == 5
    assert report["complete_contract_count"] == 5
    assert report["implementation_states"] == {"ABSENT": 4, "CANDIDATE": 1}

    contracts = {contract["capability_id"]: contract for contract in report["contracts"]}
    assert set(contracts) == {
        "advanced_gis_processing",
        "state_of_art_ocr_analysis",
        "governed_web_browsing",
        "coding_programming_expertise",
        "transparency_objectivity_governance",
    }
    for contract in contracts.values():
        assert contract["contract_status"] == "CONTRACT_DEFINED"
        assert contract["permission_gates"]
        assert contract["provenance_fields"]
        assert contract["invariants"]
        assert contract["positive_regression_gates"]
        assert contract["negative_regression_gates"]
        assert contract["output_requirements"]
        assert contract["unavailable_until"]

    assert "crs" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "ocr_engine" in contracts["state_of_art_ocr_analysis"]["provenance_fields"]
    assert "content_sha256" in contracts["governed_web_browsing"]["provenance_fields"]
    assert "dirty_status" in contracts["coding_programming_expertise"]["provenance_fields"]
    assert "evidence_type" in contracts["transparency_objectivity_governance"]["provenance_fields"]
