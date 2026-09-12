import copy
import json
from pathlib import Path

from backend.app.gis_eval import DEFAULT_GIS_EVAL_FIXTURE, build_gis_eval_report, load_gis_eval_fixture


def test_gis_eval_default_fixture_covers_core_operations():
    report = build_gis_eval_report()

    assert report["status"] == "GIS_EVAL_OK"
    assert report["fixture"] == "backend/tests/fixtures/gis_eval.json"
    assert report["fixture_validation"]["status"] == "PASS"
    assert report["counts"] == {
        "cases": 10,
        "passed": 10,
        "failed": 0,
        "positive_cases": 7,
        "negative_cases": 3,
        "operations": 7,
        "operation_names": ["bbox_filter", "compare", "csv_points", "inspect", "measure", "point_in_polygon", "reproject"],
    }
    by_id = {case["case_id"]: case for case in report["cases"]}
    assert by_id["inspect_dimensionality"]["observed_status"] == "GIS_INSPECT_OK"
    assert by_id["point_in_polygon_overlap"]["observed_warnings"] == ["GIS_JOIN_MULTIPLICATION_PRESENT"]
    assert by_id["compare_rotated_polygon"]["observed_status"] == "GIS_GEOMETRY_COMPARE_OK"
    assert by_id["reproject_wgs84_to_webmercator"]["observed_status"] == "GIS_REPROJECT_OK"
    assert by_id["csv_points_skip_invalid_rows"]["observed_status"] == "GIS_CSV_POINTS_WARN"
    assert by_id["csv_points_skip_invalid_rows"]["observed_warnings"] == ["GIS_CSV_INVALID_ROWS_SKIPPED"]
    assert by_id["bbox_filter_intersects_with_null_residue"]["observed_status"] == "GIS_BBOX_FILTER_WARN"
    assert by_id["bbox_filter_invalid_bbox_fails_closed"]["case_type"] == "negative"
    assert by_id["bbox_filter_invalid_bbox_fails_closed"]["observed_status"] == "ERROR_GIS_BBOX_INVALID"
    assert by_id["measure_planar_polygon_line_and_null"]["observed_status"] == "GIS_MEASURE_WARN"
    assert by_id["measure_planar_polygon_line_and_null"]["observed_warnings"] == ["GIS_MEASURE_NULL_EMPTY_GEOMETRY"]
    assert by_id["measure_units_required_fails_closed"]["case_type"] == "negative"
    assert by_id["measure_units_required_fails_closed"]["observed_status"] == "ERROR_GIS_MEASURE_UNITS_REQUIRED"
    assert by_id["reproject_invalid_latitude_fails_closed"]["case_type"] == "negative"
    assert by_id["reproject_invalid_latitude_fails_closed"]["observed_status"] == "ERROR_GIS_REPROJECT_INVALID_COORDINATES"


def test_gis_eval_fails_when_expected_invariant_does_not_match(tmp_path: Path):
    fixture = load_gis_eval_fixture(DEFAULT_GIS_EVAL_FIXTURE)
    tampered = copy.deepcopy(fixture)
    tampered["cases"][0]["expected_invariants"]["feature_count"] = 99
    fixture_path = tmp_path / "gis_eval_tampered.json"
    fixture_path.write_text(json.dumps(tampered), encoding="utf-8")

    report = build_gis_eval_report(fixture_path=fixture_path)

    assert report["status"] == "GIS_EVAL_FAILED"
    assert report["counts"]["failed"] == 1
    failed_case = next(case for case in report["cases"] if case["status"] == "FAIL")
    assert failed_case["case_id"] == "inspect_dimensionality"
    assert any(check["name"] == "invariants.feature_count" and check["status"] == "FAIL" for check in failed_case["checks"])
