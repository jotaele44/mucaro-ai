import json
import math
from pathlib import Path

from backend.app.config import Settings
from backend.app.gis_processing import (
    GISProcessingError,
    compare_geojson_geometries,
    inspect_geojson,
    reproject_geojson,
    spatial_join_points_to_polygons,
)


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def write_geojson(path: Path, features: list[dict], *, crs: str = "EPSG:4326") -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": crs}},
                "features": features,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def polygon_feature(feature_id: str, coordinates: list[list[list[float]]]) -> dict:
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {"name": feature_id},
        "geometry": {"type": "Polygon", "coordinates": coordinates},
    }


def multipolygon_feature(feature_id: str, coordinates: list[list[list[list[float]]]]) -> dict:
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {"name": feature_id},
        "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
    }


def point_feature(feature_id: str, coordinates) -> dict:
    geometry = None if coordinates is None else {"type": "Point", "coordinates": coordinates}
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {"name": feature_id},
        "geometry": geometry,
    }


def test_inspect_geojson_preserves_crs_geometry_state_and_receipt(tmp_path: Path):
    cfg = make_settings(tmp_path)
    geojson = write_geojson(
        tmp_path / "mixed.geojson",
        [
            point_feature("inside", [1, 1]),
            point_feature("null", None),
            polygon_feature("bad_polygon", [[[0, 0], [1, 0], [1, 1]]]),
        ],
        crs="EPSG:3857",
    )

    result = inspect_geojson(geojson, cfg=cfg)

    assert result["status"] == "GIS_INSPECT_WARN"
    assert result["source"]["crs"] == "EPSG:3857"
    assert result["source"]["crs_status"] == "DECLARED"
    assert result["source"]["feature_count"] == 3
    assert result["source"]["sha256"]
    assert result["geometry_counts"] == {"Point": 1, "NULL": 1, "Polygon": 1}
    assert result["invariants"]["invalid_or_unsupported_count"] == 1
    assert result["invariants"]["null_empty_count"] == 1
    assert result["features"][0]["bbox"] == [1.0, 1.0, 1.0, 1.0]
    assert result["features"][1]["geometry_status"] == "NULL_EMPTY"
    assert result["features"][2]["geometry_status"] == "INVALID"
    receipt = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert receipt["report_id"] == result["report_id"]
    assert result["report_sha256"]


def test_inspect_geojson_preserves_coordinate_dimensionality(tmp_path: Path):
    cfg = make_settings(tmp_path)
    geojson = write_geojson(
        tmp_path / "dimensions.geojson",
        [
            point_feature("xy", [1, 2]),
            point_feature("xyz", [1, 2, 3]),
            point_feature("xyzm", [1, 2, 3, 4]),
        ],
    )

    result = inspect_geojson(geojson, cfg=cfg)

    by_id = {row["feature_id"]: row for row in result["features"]}
    assert by_id["xy"]["coordinate_dimension"] == "XY"
    assert by_id["xyz"]["coordinate_dimension"] == "XYZ"
    assert by_id["xyz"]["has_z"] is True
    assert by_id["xyzm"]["coordinate_dimension"] == "XYZM"
    assert by_id["xyzm"]["has_m"] is True
    assert result["invariants"]["coordinate_dimension_counts"] == {"XY": 1, "XYZ": 1, "XYZM": 1}
    assert result["invariants"]["features_with_z"] == 2
    assert result["invariants"]["features_with_m"] == 1


def test_inspect_geojson_stringifies_unusual_feature_ids(tmp_path: Path):
    cfg = make_settings(tmp_path)
    geojson = write_geojson(
        tmp_path / "ids.geojson",
        [
            {
                "type": "Feature",
                "id": ["array", "id"],
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [1, 1]},
            },
            {
                "type": "Feature",
                "id": "",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [2, 2]},
            },
        ],
    )

    result = inspect_geojson(geojson, cfg=cfg)

    assert result["status"] == "GIS_INSPECT_OK"
    assert result["features"][0]["feature_id"] == "['array', 'id']"
    assert result["features"][1]["feature_id"] == "feature_2"


def test_spatial_join_classifies_inside_touch_outside_and_null(tmp_path: Path):
    cfg = make_settings(tmp_path)
    square = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]
    points = write_geojson(
        tmp_path / "points.geojson",
        [
            point_feature("inside", [5, 5]),
            point_feature("touch", [10, 5]),
            point_feature("outside", [12, 5]),
            point_feature("null", None),
        ],
    )
    polygons = write_geojson(tmp_path / "polygons.geojson", [polygon_feature("square", square)])

    result = spatial_join_points_to_polygons(points, polygons, cfg=cfg)

    assert result["status"] == "GIS_SPATIAL_JOIN_OK"
    assert result["join_cardinality"] == "N:1"
    assert result["state_counts"] == {
        "FULLY_WITHIN": 1,
        "TOUCH_ONLY": 1,
        "OUTSIDE": 1,
        "NULL_EMPTY": 1,
        "UNRESOLVED": 0,
    }
    by_id = {row["point_feature_id"]: row for row in result["point_results"]}
    assert by_id["inside"]["matched_polygons"] == [
        {"polygon_feature_id": "square", "polygon_feature_index": 0, "relation": "FULLY_WITHIN"}
    ]
    assert by_id["touch"]["spatial_state"] == "TOUCH_ONLY"
    assert by_id["outside"]["matched_polygons"] == []
    assert by_id["null"]["spatial_state"] == "NULL_EMPTY"
    assert result["invariants"]["point_row_conservation"] is True
    assert result["warnings"] == ["GIS_JOIN_MULTIPLICATION_PRESENT"]


def test_spatial_join_preserves_holes_multipolygons_and_overlap_multiplication(tmp_path: Path):
    cfg = make_settings(tmp_path)
    outer_with_hole = [
        [[0, 0], [12, 0], [12, 12], [0, 12], [0, 0]],
        [[4, 4], [8, 4], [8, 8], [4, 8], [4, 4]],
    ]
    overlap = [[[6, 0], [14, 0], [14, 10], [6, 10], [6, 0]]]
    island_a = [[[20, 20], [25, 20], [25, 25], [20, 25], [20, 20]]]
    island_b = [[[30, 30], [35, 30], [35, 35], [30, 35], [30, 30]]]
    points = write_geojson(
        tmp_path / "points.geojson",
        [
            point_feature("in_hole", [5, 5]),
            point_feature("on_hole_edge", [4, 6]),
            point_feature("overlap", [10, 5]),
            point_feature("multipolygon", [32, 32]),
            {"type": "Feature", "id": "line", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
        ],
    )
    polygons = write_geojson(
        tmp_path / "polygons.geojson",
        [
            polygon_feature("donut", outer_with_hole),
            polygon_feature("overlap", overlap),
            multipolygon_feature("islands", [island_a, island_b]),
        ],
    )

    result = spatial_join_points_to_polygons(points, polygons, cfg=cfg)

    assert result["join_cardinality"] == "N:N"
    assert result["warnings"] == ["GIS_JOIN_MULTIPLICATION_PRESENT"]
    assert result["state_counts"] == {
        "FULLY_WITHIN": 2,
        "TOUCH_ONLY": 1,
        "OUTSIDE": 1,
        "NULL_EMPTY": 0,
        "UNRESOLVED": 1,
    }
    by_id = {row["point_feature_id"]: row for row in result["point_results"]}
    assert by_id["in_hole"]["spatial_state"] == "OUTSIDE"
    assert by_id["on_hole_edge"]["spatial_state"] == "TOUCH_ONLY"
    assert by_id["overlap"]["match_count"] == 2
    assert [match["polygon_feature_id"] for match in by_id["overlap"]["matched_polygons"]] == ["donut", "overlap"]
    assert by_id["multipolygon"]["matched_polygons"] == [
        {"polygon_feature_id": "islands", "polygon_feature_index": 2, "relation": "FULLY_WITHIN"}
    ]
    assert by_id["line"]["spatial_state"] == "UNRESOLVED"
    assert result["invariants"]["point_row_conservation"] is True
    assert result["invariants"]["max_matches_per_point"] == 2


def test_spatial_join_fails_closed_on_crs_mismatch(tmp_path: Path):
    cfg = make_settings(tmp_path)
    points = write_geojson(tmp_path / "points.geojson", [point_feature("inside", [5, 5])], crs="EPSG:4326")
    polygons = write_geojson(
        tmp_path / "polygons.geojson",
        [polygon_feature("square", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
        crs="EPSG:3857",
    )

    try:
        spatial_join_points_to_polygons(points, polygons, cfg=cfg)
    except GISProcessingError as exc:
        assert exc.status == "ERROR_GIS_CRS_MISMATCH"
    else:
        raise AssertionError("expected CRS mismatch to fail closed")


def test_spatial_join_can_record_operator_allowed_crs_mismatch(tmp_path: Path):
    cfg = make_settings(tmp_path)
    points = write_geojson(tmp_path / "points.geojson", [point_feature("inside", [5, 5])], crs="EPSG:4326")
    polygons = write_geojson(
        tmp_path / "polygons.geojson",
        [polygon_feature("square", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]])],
        crs="EPSG:3857",
    )

    result = spatial_join_points_to_polygons(points, polygons, cfg=cfg, require_matching_crs=False)

    assert result["status"] == "GIS_SPATIAL_JOIN_OK"
    assert result["invariants"]["crs_match"] is False
    assert result["invariants"]["crs_mismatch_allowed"] is True
    assert result["warnings"] == ["GIS_CRS_MISMATCH_ALLOWED"]


def test_compare_geojson_geometries_records_topology_and_vertex_metrics(tmp_path: Path):
    cfg = make_settings(tmp_path)
    left = write_geojson(
        tmp_path / "left.geojson",
        [
            polygon_feature("rotated", [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]),
            point_feature("moved", [10, 10, 5]),
        ],
    )
    right = write_geojson(
        tmp_path / "right.geojson",
        [
            polygon_feature("rotated", [[[4, 4], [0, 4], [0, 0], [4, 0], [4, 4]]]),
            point_feature("moved", [13, 14, 5]),
        ],
    )

    result = compare_geojson_geometries(left, right, cfg=cfg)

    assert result["status"] == "GIS_GEOMETRY_COMPARE_WARN"
    assert result["match_on"] == "id"
    assert result["warnings"] == ["GIS_GEOMETRY_DIFFERENCE_PRESENT"]
    assert result["invariants"]["feature_key_conservation"] is True
    assert result["invariants"]["compared_count"] == 2
    assert result["invariants"]["exact_equal_count"] == 0
    assert result["invariants"]["topological_equal_count"] == 1
    assert result["invariants"]["geometry_difference_count"] == 1
    assert result["invariants"]["vertex_symmetric_difference_total"] == 2
    assert result["invariants"]["max_hausdorff_vertex_distance"] == 5.0
    by_key = {row["feature_key"]: row for row in result["comparisons"]}
    assert by_key["id:rotated"]["exact_coordinate_equal"] is False
    assert by_key["id:rotated"]["topological_equal"] is True
    assert by_key["id:rotated"]["hausdorff_vertex_distance"] == 0.0
    assert by_key["id:moved"]["left_coordinate_dimension"] == "XYZ"
    assert by_key["id:moved"]["coordinate_dimension_preserved"] is True
    receipt = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert receipt["report_id"] == result["report_id"]
    assert result["report_sha256"]


def test_compare_geojson_geometries_records_unmatched_and_dimension_mismatch(tmp_path: Path):
    cfg = make_settings(tmp_path)
    left = write_geojson(tmp_path / "left.geojson", [point_feature("a", [1, 2]), point_feature("left_only", [3, 4])])
    right = write_geojson(tmp_path / "right.geojson", [point_feature("a", [1, 2, 3]), point_feature("right_only", [5, 6])])

    result = compare_geojson_geometries(left, right, cfg=cfg)

    assert result["status"] == "GIS_GEOMETRY_COMPARE_WARN"
    assert result["left_only_feature_keys"] == ["id:left_only"]
    assert result["right_only_feature_keys"] == ["id:right_only"]
    assert result["warnings"] == ["GIS_UNMATCHED_FEATURES", "GIS_COORDINATE_DIMENSION_MISMATCH"]
    assert result["invariants"]["left_only_count"] == 1
    assert result["invariants"]["right_only_count"] == 1
    assert result["invariants"]["coordinate_dimension_mismatch_count"] == 1
    assert result["comparisons"][0]["topology_state"] == "EQUAL"
    assert result["comparisons"][0]["exact_coordinate_equal"] is False


def test_compare_geojson_geometries_can_match_by_index(tmp_path: Path):
    cfg = make_settings(tmp_path)
    left = write_geojson(tmp_path / "left.geojson", [point_feature("left_name", [1, 2])])
    right = write_geojson(tmp_path / "right.geojson", [point_feature("right_name", [1, 2])])

    result = compare_geojson_geometries(left, right, cfg=cfg, match_on="index")

    assert result["status"] == "GIS_GEOMETRY_COMPARE_OK"
    assert result["comparisons"][0]["feature_key"] == "index:0"
    assert result["comparisons"][0]["topology_state"] == "EQUAL"


def test_reproject_geojson_writes_output_and_preserves_dimensions(tmp_path: Path):
    cfg = make_settings(tmp_path)
    source = write_geojson(
        tmp_path / "wgs84.geojson",
        [
            point_feature("origin", [0, 0]),
            point_feature("z_m", [1, 1, 7, 9]),
            polygon_feature("square", [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]),
        ],
        crs="EPSG:4326",
    )

    result = reproject_geojson(source, target_crs="EPSG:3857", cfg=cfg)

    assert result["status"] == "GIS_REPROJECT_OK"
    assert result["parameters"]["adapter"] == "local_web_mercator_formula_v1"
    assert result["input"]["declared_crs"] == "EPSG:4326"
    assert result["output"]["crs"] == "EPSG:3857"
    assert result["invariants"]["crs_changed"] is True
    assert result["invariants"]["feature_row_conservation"] is True
    assert result["invariants"]["coordinate_count_conservation"] is True
    assert result["invariants"]["transformed_feature_count"] == 3
    assert result["invariants"]["features_with_z"] == 1
    assert result["invariants"]["features_with_m"] == 1
    output = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
    assert output["crs"]["properties"]["name"] == "EPSG:3857"
    assert output["features"][0]["geometry"]["coordinates"] == [0.0, 0.0]
    projected = output["features"][1]["geometry"]["coordinates"]
    assert math.isclose(projected[0], 111319.49079327357, rel_tol=0, abs_tol=1e-6)
    assert math.isclose(projected[1], 111325.1428663851, rel_tol=0, abs_tol=1e-6)
    assert projected[2:] == [7, 9]
    receipt = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert receipt["report_id"] == result["report_id"]
    assert result["report_sha256"]


def test_reproject_geojson_supports_inverse_web_mercator(tmp_path: Path):
    cfg = make_settings(tmp_path)
    source = write_geojson(
        tmp_path / "webmercator.geojson",
        [point_feature("one_degree", [111319.49079327357, 111325.1428663851])],
        crs="EPSG:3857",
    )

    result = reproject_geojson(source, target_crs="EPSG:4326", cfg=cfg)

    output = json.loads(Path(result["output"]["path"]).read_text(encoding="utf-8"))
    lon, lat = output["features"][0]["geometry"]["coordinates"]
    assert result["status"] == "GIS_REPROJECT_OK"
    assert math.isclose(lon, 1.0, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(lat, 1.0, rel_tol=0, abs_tol=1e-9)


def test_reproject_geojson_fails_closed_on_unsupported_crs(tmp_path: Path):
    cfg = make_settings(tmp_path)
    source = write_geojson(tmp_path / "local.geojson", [point_feature("a", [1, 2])], crs="EPSG:2263")

    try:
        reproject_geojson(source, target_crs="EPSG:4326", cfg=cfg)
    except GISProcessingError as exc:
        assert exc.status == "ERROR_GIS_REPROJECT_UNSUPPORTED_CRS"
    else:
        raise AssertionError("expected unsupported CRS to fail closed")


def test_reproject_geojson_fails_closed_on_invalid_web_mercator_latitude(tmp_path: Path):
    cfg = make_settings(tmp_path)
    source = write_geojson(tmp_path / "invalid.geojson", [point_feature("north", [0, 89])], crs="EPSG:4326")

    try:
        reproject_geojson(source, target_crs="EPSG:3857", cfg=cfg)
    except GISProcessingError as exc:
        assert exc.status == "ERROR_GIS_REPROJECT_INVALID_COORDINATES"
    else:
        raise AssertionError("expected invalid latitude to fail closed")
