import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError, inspect_geojson, spatial_join_points_to_polygons


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
