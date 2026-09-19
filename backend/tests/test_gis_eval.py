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
        "cases": 48,
        "passed": 48,
        "failed": 0,
        "positive_cases": 31,
        "negative_cases": 17,
        "operations": 22,
        "operation_names": [
            "bbox_filter",
            "clip_bbox",
            "compare",
            "corpus_validate",
            "csv_points",
            "inspect",
            "map_export",
            "measure",
            "measure_ellipsoidal",
            "overlay",
            "point_in_polygon",
            "raster_clip",
            "raster_inspect",
            "raster_resample",
            "raster_sample",
            "raster_terrain",
            "repair_topology",
            "reproject",
            "reproject_crs",
            "vector_format",
            "vector_raster",
            "zonal_stats",
        ],
    }
    by_id = {case["case_id"]: case for case in report["cases"]}
    assert by_id["inspect_dimensionality"]["observed_status"] == "GIS_INSPECT_WARN"
    assert by_id["inspect_dimensionality"]["observed_warnings"] == [
        "GIS_TOPOLOGY_PLANAR_XY_ONLY_PRESENT",
        "GIS_TOPOLOGY_WARNINGS_PRESENT",
    ]
    assert by_id["inspect_invalid_topology_warns_without_repair"]["observed_status"] == "GIS_INSPECT_WARN"
    assert by_id["inspect_invalid_topology_warns_without_repair"]["observed_warnings"] == [
        "GIS_TOPOLOGY_WARNINGS_PRESENT"
    ]
    assert by_id["point_in_polygon_overlap"]["observed_warnings"] == ["GIS_JOIN_MULTIPLICATION_PRESENT"]
    assert by_id["compare_rotated_polygon"]["observed_status"] == "GIS_GEOMETRY_COMPARE_OK"
    assert by_id["compare_planar_overlay_difference"]["observed_status"] == "GIS_GEOMETRY_COMPARE_WARN"
    assert by_id["compare_planar_overlay_difference"]["observed_warnings"] == [
        "GIS_GEOMETRY_DIFFERENCE_PRESENT",
        "GIS_PLANAR_OVERLAY_DIFFERENCE_PRESENT",
    ]
    assert by_id["overlay_intersection_outputs_feature"]["observed_status"] == "GIS_OVERLAY_OK"
    assert by_id["overlay_invalid_operation_fails_closed"]["case_type"] == "negative"
    assert by_id["overlay_invalid_operation_fails_closed"]["observed_status"] == "ERROR_GIS_OVERLAY_OPERATION"
    assert by_id["reproject_wgs84_to_webmercator"]["observed_status"] == "GIS_REPROJECT_OK"
    assert by_id["measure_ellipsoidal_antimeridian_line"]["observed_status"] == "GIS_ELLIPSOIDAL_WARN"
    assert by_id["measure_ellipsoidal_polar_cap_polygon"]["observed_warnings"] == [
        "GIS_ELLIPSOIDAL_EDGE_CONDITIONS_PRESENT"
    ]
    assert by_id["measure_ellipsoidal_invalid_latitude_is_unresolved"]["observed_warnings"] == [
        "GIS_ELLIPSOIDAL_UNRESOLVED"
    ]
    assert by_id["raster_terrain_east_rising_plane"]["observed_status"] == "GIS_RASTER_TERRAIN_OK"
    assert by_id["raster_terrain_geographic_crs_fails_closed"]["observed_status"] == "ERROR_GIS_TERRAIN_CRS"
    assert by_id["raster_terrain_invalid_band_fails_closed"]["observed_status"] == "ERROR_GIS_TERRAIN_BAND"
    assert by_id["csv_points_skip_invalid_rows"]["observed_status"] == "GIS_CSV_POINTS_WARN"
    assert by_id["csv_points_skip_invalid_rows"]["observed_warnings"] == ["GIS_CSV_INVALID_ROWS_SKIPPED"]
    assert by_id["bbox_filter_intersects_with_null_residue"]["observed_status"] == "GIS_BBOX_FILTER_WARN"
    assert by_id["bbox_filter_invalid_bbox_fails_closed"]["case_type"] == "negative"
    assert by_id["bbox_filter_invalid_bbox_fails_closed"]["observed_status"] == "ERROR_GIS_BBOX_INVALID"
    assert by_id["clip_bbox_polygon_point_and_residue"]["observed_status"] == "GIS_CLIP_BBOX_WARN"
    assert by_id["clip_bbox_polygon_point_and_residue"]["observed_warnings"] == [
        "GIS_CLIP_BBOX_NULL_EMPTY_EXCLUDED",
        "GIS_CLIP_BBOX_UNRESOLVED_EXCLUDED",
    ]
    assert by_id["clip_bbox_invalid_bbox_fails_closed"]["case_type"] == "negative"
    assert by_id["clip_bbox_invalid_bbox_fails_closed"]["observed_status"] == "ERROR_GIS_BBOX_INVALID"
    assert by_id["measure_planar_polygon_line_and_null"]["observed_status"] == "GIS_MEASURE_WARN"
    assert by_id["measure_planar_polygon_line_and_null"]["observed_warnings"] == ["GIS_MEASURE_NULL_EMPTY_GEOMETRY"]
    assert by_id["measure_geodesic_wgs84_square"]["observed_status"] == "GIS_MEASURE_OK"
    assert by_id["measure_geodesic_non_wgs84_fails_closed"]["case_type"] == "negative"
    assert by_id["measure_geodesic_non_wgs84_fails_closed"]["observed_status"] == "ERROR_GIS_MEASURE_GEODESIC_CRS"
    assert by_id["measure_units_required_fails_closed"]["case_type"] == "negative"
    assert by_id["measure_units_required_fails_closed"]["observed_status"] == "ERROR_GIS_MEASURE_UNITS_REQUIRED"
    assert by_id["reproject_invalid_latitude_fails_closed"]["case_type"] == "negative"
    assert by_id["reproject_invalid_latitude_fails_closed"]["observed_status"] == "ERROR_GIS_REPROJECT_INVALID_COORDINATES"
    assert by_id["reproject_crs_wgs84_to_utm20n"]["observed_status"] == "GIS_CRS_REPROJECT_OK"
    assert by_id["reproject_crs_m_coordinates_fail_closed"]["observed_status"] == "ERROR_GIS_CRS_M_UNSUPPORTED"
    assert by_id["repair_topology_bowtie_explicit"]["observed_status"] == "GIS_TOPOLOGY_REPAIR_OK"
    assert by_id["repair_topology_z_remains_unresolved"]["observed_status"] == "GIS_TOPOLOGY_REPAIR_WARN"
    assert by_id["measure_ellipsoidal_wgs84_line"]["observed_status"] == "GIS_ELLIPSOIDAL_OK"
    assert by_id["measure_ellipsoidal_non_wgs84_fails_closed"]["observed_status"] == "ERROR_GIS_GEOD_CRS"
    assert by_id["vector_format_geojson_manifest"]["observed_status"] == "GIS_VECTOR_FORMAT_OK"
    assert by_id["raster_inspect_nodata_accounting"]["observed_status"] == "GIS_RASTER_INSPECT_OK"
    assert by_id["raster_sample_distinct_states"]["observed_status"] == "GIS_RASTER_SAMPLE_OK"
    assert by_id["raster_clip_bbox_writes_hashed_output"]["observed_status"] == "GIS_RASTER_CLIP_OK"
    assert by_id["raster_clip_outside_fails_closed"]["observed_status"] == "ERROR_GIS_RASTER_CLIP_OUTSIDE"
    assert by_id["vector_raster_preserves_zero_nodata_null"]["observed_status"] == "GIS_VECTOR_RASTER_OK"
    assert by_id["vector_raster_crs_mismatch_fails_closed"]["observed_status"] == "ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH"
    assert by_id["zonal_stats_close_numeric_nodata_and_zero_accounting"]["observed_status"] == "GIS_ZONAL_STATS_OK"
    assert by_id["zonal_stats_invalid_band_fails_closed"]["observed_status"] == "ERROR_GIS_VECTOR_RASTER_BAND"
    assert by_id["raster_resample_preserves_grid_contract"]["observed_status"] == "GIS_RASTER_RESAMPLE_OK"
    assert by_id["raster_resample_invalid_dimensions_fail_closed"]["observed_status"] == "ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS"
    assert by_id["real_corpus_bytes_schema_ids_and_geometry_validate"]["observed_status"] == "GIS_CORPUS_VALIDATION_OK"
    assert by_id["map_export_binds_renderer_and_layers"]["observed_status"] == "GIS_MAP_EXPORT_MANIFEST_OK"
    assert by_id["map_export_invalid_extent_fails_closed"]["observed_status"] == "ERROR_GIS_EXPORT_EXTENT"


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
