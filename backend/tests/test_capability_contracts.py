from backend.app.capability_contracts import (
    CAPABILITY_CONTRACT_SCHEMA_VERSION,
    CAPABILITY_OUTPUT_CONTRACT_FAILED,
    CAPABILITY_OUTPUT_CONTRACT_OK,
    build_capability_contract_report,
    validate_execution_lineage_contract,
    validate_gis_report_contract,
    validate_ocr_report_contract,
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
    assert "clip_adapter" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "overlay_adapter" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "transformer_definition_sha256" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "repair_adapter" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "ellipsoidal_adapter" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "raster_source_sha256" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert "bound_layer_sha256" in contracts["advanced_gis_processing"]["provenance_fields"]
    assert contracts["advanced_gis_processing"]["implementation_status"] == "CANDIDATE"
    assert "ocr_engine" in contracts["state_of_art_ocr_analysis"]["provenance_fields"]
    assert contracts["state_of_art_ocr_analysis"]["implementation_status"] == "CANDIDATE"
    assert contracts["governed_web_browsing"]["implementation_status"] == "CANDIDATE"
    assert "content_sha256" in contracts["governed_web_browsing"]["provenance_fields"]
    assert "dirty_status" in contracts["coding_programming_expertise"]["provenance_fields"]
    assert "evidence_type" in contracts["transparency_objectivity_governance"]["provenance_fields"]


def _execution_artifact(artifact_id: str, artifact_type: str, parents: list[str]) -> dict:
    return {
        "schema_version": "mucaro-artifact/1.0",
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "sha256": "a" * 64,
        "byte_size": 12,
        "logical_identity": f"skill:{artifact_id}",
        "producer": "skill_runtime",
        "producer_version": "1.0.0",
        "created_utc": "2026-10-22T00:00:00Z",
        "parent_artifact_ids": parents,
        "schema_id": "test.schema/1",
        "source_path_or_uri": "skill://test",
    }


def test_validate_execution_lineage_contract_accepts_success_lineage():
    source = _execution_artifact("artifact_input", "SOURCE", [])
    output = _execution_artifact("artifact_output", "DERIVED", ["artifact_input"])
    result = validate_execution_lineage_contract(
        {
            "execution_id": "execution_test",
            "skill_id": "mucaro/test",
            "skill_digest": "sha256:test",
            "runtime": "IN_PROCESS",
            "status": "PASS",
            "artifact_lineage": {"input": source, "output": output, "failure_receipt": None},
        }
    )
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, result["failed_checks"]


def test_validate_execution_lineage_contract_rejects_contradictory_failure_lineage():
    source = _execution_artifact("artifact_input", "SOURCE", [])
    output = _execution_artifact("artifact_output", "DERIVED", ["artifact_input"])
    result = validate_execution_lineage_contract(
        {
            "execution_id": "execution_test",
            "skill_id": "mucaro/test",
            "skill_digest": "sha256:test",
            "runtime": "IN_PROCESS",
            "status": "FAIL",
            "artifact_lineage": {"input": source, "output": output, "failure_receipt": None},
        }
    )
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert any(check["name"] == "failure.output_absent" for check in result["failed_checks"])
    assert any(check["name"] == "failure.receipt_present" for check in result["failed_checks"])


def test_validate_execution_lineage_contract_rejects_malformed_hash():
    source = _execution_artifact("artifact_input", "SOURCE", [])
    source["sha256"] = "not-a-sha"
    output = _execution_artifact("artifact_output", "DERIVED", ["artifact_input"])
    result = validate_execution_lineage_contract(
        {
            "execution_id": "execution_test",
            "skill_id": "mucaro/test",
            "skill_digest": "sha256:test",
            "runtime": "IN_PROCESS",
            "status": "PASS",
            "artifact_lineage": {"input": source, "output": output, "failure_receipt": None},
        }
    )
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert any(check["name"] == "artifact_lineage.input.sha256" for check in result["failed_checks"])


def test_validate_gis_report_contract_accepts_measurement_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_test",
        "status": "GIS_MEASURE_WARN",
        "operation": "measure_geojson",
        "created_at": "2026-09-12T00:00:00Z",
        "parameters": {
            "coordinate_units": "meters",
            "measurement_mode": "planar",
            "area_units": "meters^2",
            "length_units": "meters",
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


def test_validate_gis_report_contract_accepts_geodesic_measurement_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_geodesic",
        "status": "GIS_MEASURE_OK",
        "operation": "measure_geojson",
        "created_at": "2026-09-12T00:00:00Z",
        "parameters": {
            "coordinate_units": "degrees",
            "measurement_mode": "geodesic",
            "area_units": "meters^2",
            "length_units": "meters",
            "adapter": "local_spherical_geodesic_v1",
        },
        "source": {
            "path": "fixtures/wgs84.geojson",
            "sha256": "c" * 64,
            "crs": "epsg:4326",
            "feature_count": 1,
        },
        "measurements": [],
        "state_counts": {"MEASURED": 1, "NULL_EMPTY": 0, "INVALID": 0, "UNSUPPORTED": 0},
        "invariants": {
            "row_accounting_closed": True,
            "feature_count": 1,
            "measured_feature_count": 1,
            "total_area": 12363718145.179935,
            "total_length": 444763.384955,
        },
        "warnings": [],
        "limitations": ["local spherical geodesic measurement fixture"],
        "falsification_criteria": ["reject if source CRS is not EPSG:4326"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0


def test_validate_gis_report_contract_accepts_compare_overlay_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_compare",
        "status": "GIS_GEOMETRY_COMPARE_WARN",
        "operation": "compare_geojson_geometries",
        "created_at": "2026-09-12T00:00:00Z",
        "match_on": "id",
        "parameters": {
            "match_on": "id",
            "topology_adapter": "local_canonical_geometry_v1",
            "planar_overlay_adapter": "shapely_planar_overlay_v1",
        },
        "inputs": {
            "left": {
                "path": "fixtures/left.geojson",
                "sha256": "a" * 64,
                "crs": "EPSG:4326",
                "feature_count": 1,
            },
            "right": {
                "path": "fixtures/right.geojson",
                "sha256": "b" * 64,
                "crs": "EPSG:4326",
                "feature_count": 1,
            },
        },
        "comparisons": [
            {
                "feature_key": "id:overlap",
                "planar_overlay_state": "MEASURED",
                "intersection_area": 4.0,
                "symmetric_difference_area": 24.0,
            }
        ],
        "invariants": {
            "feature_key_conservation": True,
            "compared_count": 1,
            "planar_overlay_measured_count": 1,
            "planar_overlay_unresolved_count": 0,
            "planar_overlay_difference_count": 1,
            "planar_overlay_symmetric_difference_area_total": 24.0,
        },
        "warnings": ["GIS_GEOMETRY_DIFFERENCE_PRESENT"],
        "limitations": ["local planar overlay comparison fixture"],
        "falsification_criteria": ["reject if overlay accounting does not close"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0


def test_validate_gis_report_contract_accepts_standalone_overlay_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_overlay",
        "status": "GIS_OVERLAY_OK",
        "operation": "overlay_geojson_collections",
        "created_at": "2026-09-12T00:00:00Z",
        "match_on": "id",
        "parameters": {
            "overlay_operation": "intersection",
            "match_on": "id",
            "adapter": "shapely_planar_overlay_v1",
            "overlay_adapter": "shapely_planar_overlay_v1",
            "supported_operations": ["difference", "intersection", "symmetric_difference", "union"],
            "supported_geometry_types": ["MultiPolygon", "Polygon"],
        },
        "inputs": {
            "left": {
                "path": "fixtures/left.geojson",
                "sha256": "a" * 64,
                "crs": "EPSG:4326",
                "feature_count": 1,
            },
            "right": {
                "path": "fixtures/right.geojson",
                "sha256": "b" * 64,
                "crs": "EPSG:4326",
                "feature_count": 1,
            },
        },
        "output": {
            "path": "data/exports/gis_reports/gisreport_overlay.geojson",
            "sha256": "c" * 64,
            "crs": "EPSG:4326",
            "feature_count": 1,
        },
        "left_only_feature_keys": [],
        "right_only_feature_keys": [],
        "features": [
            {
                "feature_key": "id:overlap",
                "overlay_state": "EMITTED",
                "result_geometry_type": "Polygon",
                "result_area": 4.0,
                "result_length": 8.0,
            }
        ],
        "state_counts": {
            "EMITTED": 1,
            "EMPTY_INPUT": 0,
            "EMPTY_RESULT": 0,
            "UNRESOLVED": 0,
            "UNSUPPORTED": 0,
            "NON_POLYGON_RESULT": 0,
            "UNMATCHED_LEFT": 0,
            "UNMATCHED_RIGHT": 0,
        },
        "invariants": {
            "crs_match": True,
            "left_feature_count": 1,
            "right_feature_count": 1,
            "shared_feature_key_count": 1,
            "left_only_count": 0,
            "right_only_count": 0,
            "output_feature_count": 1,
            "emitted_feature_count": 1,
            "empty_input_count": 0,
            "empty_result_count": 0,
            "unresolved_pair_count": 0,
            "unsupported_pair_count": 0,
            "non_polygon_result_count": 0,
            "feature_key_accounting_closed": True,
            "overlay_pair_accounting_closed": True,
            "result_area_total": 4.0,
            "result_length_total": 8.0,
        },
        "warnings": [],
        "limitations": ["local planar overlay fixture"],
        "falsification_criteria": ["reject if overlay accounting does not close"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0


def test_validate_gis_report_contract_rejects_standalone_overlay_false_accounting():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_overlay_bad",
        "status": "GIS_OVERLAY_OK",
        "operation": "overlay_geojson_collections",
        "created_at": "2026-09-12T00:00:00Z",
        "match_on": "id",
        "parameters": {
            "overlay_operation": "intersection",
            "match_on": "id",
            "adapter": "shapely_planar_overlay_v1",
            "overlay_adapter": "shapely_planar_overlay_v1",
        },
        "inputs": {
            "left": {"path": "fixtures/left.geojson", "sha256": "a" * 64, "crs": "EPSG:4326", "feature_count": 1},
            "right": {"path": "fixtures/right.geojson", "sha256": "b" * 64, "crs": "EPSG:4326", "feature_count": 1},
        },
        "output": {"path": "data/exports/gis_reports/bad.geojson", "sha256": "c" * 64, "crs": "EPSG:4326", "feature_count": 2},
        "features": [{"feature_key": "id:overlap", "overlay_state": "EMITTED"}],
        "state_counts": {
            "EMITTED": 1,
            "EMPTY_INPUT": 0,
            "EMPTY_RESULT": 0,
            "UNRESOLVED": 0,
            "UNSUPPORTED": 0,
            "NON_POLYGON_RESULT": 0,
        },
        "invariants": {
            "crs_match": True,
            "shared_feature_key_count": 1,
            "output_feature_count": 2,
            "emitted_feature_count": 1,
            "feature_key_accounting_closed": True,
            "overlay_pair_accounting_closed": True,
            "result_area_total": 4.0,
            "result_length_total": 8.0,
        },
        "warnings": [],
        "limitations": ["local planar overlay fixture"],
        "falsification_criteria": ["reject if overlay accounting does not close"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert result["counts"]["fail"] >= 1
    failures = {check["name"] for check in result["checks"] if check["status"] == "FAIL"}
    assert "overlay.output_count_matches_emitted" in failures


def test_validate_gis_report_contract_accepts_bbox_clip_receipts():
    report = {
        "schema_version": "mucaro_gis_report_v1",
        "report_id": "gisreport_clip",
        "status": "GIS_CLIP_BBOX_WARN",
        "operation": "clip_geojson_by_bbox",
        "created_at": "2026-09-12T00:00:00Z",
        "parameters": {
            "bbox": [0.0, 0.0, 2.0, 2.0],
            "adapter": "shapely_bbox_intersection_v1",
            "supported_geometry_types": ["LineString", "Point", "Polygon"],
        },
        "source": {
            "path": "fixtures/clip.geojson",
            "sha256": "d" * 64,
            "crs": "EPSG:4326",
            "feature_count": 5,
        },
        "output": {
            "path": "data/exports/gis_reports/gisreport_clip.geojson",
            "sha256": "e" * 64,
            "crs": "EPSG:4326",
            "feature_count": 2,
        },
        "features": [],
        "state_counts": {
            "RETAINED_UNCHANGED": 1,
            "RETAINED_CLIPPED": 1,
            "EXCLUDED_OUTSIDE": 1,
            "EXCLUDED_NULL_EMPTY": 1,
            "EXCLUDED_UNRESOLVED": 1,
        },
        "invariants": {
            "row_accounting_closed": True,
            "crs_preserved": True,
            "input_feature_count": 5,
            "output_feature_count": 2,
            "retained_feature_count": 2,
            "clipped_feature_count": 1,
            "unchanged_feature_count": 1,
            "excluded_feature_count": 3,
            "source_coordinate_count": 8,
            "output_coordinate_count": 6,
        },
        "warnings": ["GIS_CLIP_BBOX_UNRESOLVED_EXCLUDED"],
        "limitations": ["local bbox clipping fixture"],
        "falsification_criteria": ["reject if clipped feature accounting does not close"],
    }

    result = validate_gis_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0


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


def test_validate_ocr_report_contract_accepts_image_ocr_receipts():
    report = {
        "schema_version": "mucaro_ocr_report_v1",
        "report_id": "ocrreport_test",
        "status": "OCR_ANALYSIS_OK",
        "operation": "image_ocr",
        "created_at": "2026-09-12T00:00:00Z",
        "source": {
            "path": "fixtures/scan.png",
            "sha256": "b" * 64,
            "mime_type": "image/png",
            "media_kind": "image",
            "bytes": 128,
            "width": 8,
            "height": 6,
            "parser_warnings": [],
        },
        "engine": {
            "name": "tesseract",
            "version": "tesseract 5.3.0",
            "language": "eng",
            "layout_mode": "auto",
            "confidence_scale": "0-100",
        },
        "text": {
            "raw": "Mucaro\nAI",
            "normalized": "Mucaro\nAI",
            "canonical": "mucaro\nai",
        },
        "spans": [
            {
                "span_index": 0,
                "raw_text": "Mucaro",
                "normalized_text": "Mucaro",
                "canonical_text": "mucaro",
                "confidence": 96.4,
                "confidence_state": "HIGH",
                "bbox": {"x": 10, "y": 20, "width": 30, "height": 12},
                "page_index": 0,
            },
            {
                "span_index": 1,
                "raw_text": "AI",
                "normalized_text": "AI",
                "canonical_text": "ai",
                "confidence": 42.0,
                "confidence_state": "LOW",
                "bbox": {"x": 40, "y": 20, "width": 12, "height": 12},
                "page_index": 0,
            },
        ],
        "invariants": {
            "raw_normalized_canonical_separated": True,
            "span_count": 2,
            "low_confidence_count": 1,
            "unknown_confidence_count": 0,
            "empty_text_state": False,
            "bounding_boxes_have_source_coordinates": True,
        },
        "warnings": ["OCR_LOW_CONFIDENCE_PRESENT"],
        "limitations": ["image-only OCR fixture"],
        "falsification_criteria": ["reject if span accounting does not close"],
    }

    result = validate_ocr_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK
    assert result["counts"]["fail"] == 0
    assert result["capability_id"] == "state_of_art_ocr_analysis"


def test_validate_ocr_report_contract_rejects_weak_image_ocr_receipts():
    report = {
        "schema_version": "mucaro_ocr_report_v1",
        "report_id": "ocrreport_test",
        "status": "OCR_ANALYSIS_OK",
        "operation": "image_ocr",
        "created_at": "2026-09-12T00:00:00Z",
        "source": {
            "sha256": "",
            "mime_type": "",
            "media_kind": "document",
            "bytes": 0,
            "width": None,
            "height": -1,
            "parser_warnings": "none",
        },
        "engine": {
            "name": "unknown",
            "version": "",
            "language": "",
            "layout_mode": "",
            "confidence_scale": "0-1",
        },
        "text": {
            "raw": "Mucaro",
            "normalized": "Mucaro",
            "canonical": "not-derived",
        },
        "spans": [
            {
                "raw_text": "Mucaro",
                "normalized_text": "Mucaro",
                "canonical_text": "mucaro",
                "confidence_state": "LOW",
                "bbox": {"x": 0, "y": 0, "width": 10, "height": 10},
                "page_index": 0,
            }
        ],
        "invariants": {
            "raw_normalized_canonical_separated": False,
            "span_count": 2,
            "low_confidence_count": 0,
            "unknown_confidence_count": 0,
            "empty_text_state": True,
            "bounding_boxes_have_source_coordinates": False,
        },
        "warnings": [],
    }

    result = validate_ocr_report_contract(report)

    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    failed_names = {check["name"] for check in result["failed_checks"]}
    assert "source.sha256_present" in failed_names
    assert "source.media_kind_image" in failed_names
    assert "engine.name" in failed_names
    assert "engine.confidence_scale" in failed_names
    assert "text.canonical_matches_normalized" in failed_names
    assert "invariants.span_count_matches" in failed_names
    assert "invariants.low_confidence_count_matches" in failed_names
    assert "invariants.empty_text_state_matches" in failed_names
    assert "low_confidence_has_warning" in failed_names
    assert "limitations_non_empty" in failed_names
    assert "falsification_criteria_non_empty" in failed_names
