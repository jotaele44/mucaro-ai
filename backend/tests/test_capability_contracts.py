from backend.app.capability_contracts import (
    CAPABILITY_CONTRACT_SCHEMA_VERSION,
    CAPABILITY_OUTPUT_CONTRACT_FAILED,
    CAPABILITY_OUTPUT_CONTRACT_OK,
    build_capability_contract_report,
    validate_gis_report_contract,
)


def test_capability_contracts_cover_expanded_scope_without_overclaiming():
    report = build_capability_contract_report()

    assert report["status"] == "ADVANCED_CAPABILITY_CONTRACTS_READY"
    assert report["schema_version"] == CAPABILITY_CONTRACT_SCHEMA_VERSION
    assert report["contract_count"] == 5
    assert report["complete_contract_count"] == 5
    assert report["implementation_states"] == {"ABSENT": 1, "CANDIDATE": 4}

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
    assert contracts["advanced_gis_processing"]["implementation_status"] == "CANDIDATE"
    assert "ocr_engine" in contracts["state_of_art_ocr_analysis"]["provenance_fields"]
    assert contracts["state_of_art_ocr_analysis"]["implementation_status"] == "CANDIDATE"
    assert contracts["governed_web_browsing"]["implementation_status"] == "CANDIDATE"
    assert "content_sha256" in contracts["governed_web_browsing"]["provenance_fields"]
    assert "dirty_status" in contracts["coding_programming_expertise"]["provenance_fields"]
    assert "evidence_type" in contracts["transparency_objectivity_governance"]["provenance_fields"]


def test_validate_gis_report_contract_accepts_measurement_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_test",
        "status": "GIS_MEASURE_WARN",
        "operation": "measure_geojson",
        "created_at": "2026-09-12T00:00:00Z",
        "parameters": {
            "coordinate_units": "meters",
            "area_units": "meters^2",
            "adapter": "local_planar_shoelace_v1",
        },
        "source": {
            "path": "fixtures/measured.geojson",
            "sha256": "a" * 64,
            "crs": "EPSG:3857",
            "feature_count": 3,
        },
        "measurements": [],
        "state_counts": {"MEASURED": 2, "NULL_EMPTY": 1, "INVALID": 0, "UNSUPPORTED": 0},
        "invariants": {
            "row_accounting_closed": True,
            "feature_count": 3,
            "measured_feature_count": 2,
            "total_area": 32.0,
            "total_length": 40.0,
        },
        "warnings": ["GIS_MEASURE_NULL_EMPTY_GEOMETRY"],
        "limitations": ["local planar measurement fixture"],
        "falsification_criteria": ["reject if accounting does not close"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0
    assert result["capability_id"] == "advanced_gis_processing"


def test_validate_gis_report_contract_accepts_singular_input_provenance():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_reproject",
        "status": "GIS_REPROJECT_OK",
        "operation": "reproject_geojson",
        "created_at": "2026-09-12T00:00:00Z",
        "input": {
            "path": "fixtures/source.geojson",
            "sha256": "a" * 64,
            "declared_crs": "EPSG:4326",
            "feature_count": 1,
        },
        "invariants": {
            "feature_row_conservation": True,
            "coordinate_count_conservation": True,
        },
        "warnings": [],
        "limitations": ["limited local reprojection adapter"],
        "falsification_criteria": ["reject unsupported CRS pairs"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert "input.sha256_present" in {check["name"] for check in result["checks"]}


def test_validate_gis_report_contract_rejects_weak_measurement_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_test",
        "status": "GIS_MEASURE_WARN",
        "operation": "measure_geojson",
        "created_at": "2026-09-12T00:00:00Z",
        "parameters": {
            "coordinate_units": "",
            "adapter": "unknown",
        },
        "source": {
            "path": "fixtures/measured.geojson",
            "sha256": "",
            "crs": "",
            "feature_count": -1,
        },
        "measurements": [],
        "invariants": {
            "row_accounting_closed": False,
            "measured_feature_count": None,
            "total_area": "unknown",
        },
        "warnings": [],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    failed_names = {check["name"] for check in result["failed_checks"]}
    assert "source.sha256_present" in failed_names
    assert "source.crs_present" in failed_names
    assert "source.feature_count_non_negative" in failed_names
    assert "warn_status_has_warnings" in failed_names
    assert "invariants.row_accounting_closed" in failed_names
    assert "measure.parameters.adapter" in failed_names
    assert "measure.parameters.coordinate_units" in failed_names
    assert "measure.total_area_numeric" in failed_names
    assert "measure.total_length_numeric" in failed_names
    assert "limitations_non_empty" in failed_names
    assert "falsification_criteria_non_empty" in failed_names
