from pathlib import Path
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError
from backend.app.gis_vector_raster import analyze_vector_raster_intersection, compute_zonal_statistics


def make_settings(tmp_path: Path) -> Settings:
    cfg = Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        input_dir=tmp_path / "data" / "input",
        processed_dir=tmp_path / "data" / "processed",
        export_dir=tmp_path / "data" / "exports",
        index_dir=tmp_path / "data" / "indexes",
        sqlite_path=tmp_path / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def make_raster(path: Path) -> Path:
    data = np.array([[0, 2], [3, -9999]], dtype="int16")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
        nodata=-9999,
    ) as target:
        target.write(data, 1)
    return path


def make_vector(path: Path, crs: str = "EPSG:4326") -> Path:
    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs}},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 1], [1, 1], [1, 2], [0, 2], [0, 1]]]},
                "properties": {},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[1, 0], [2, 0], [2, 1], [1, 1], [1, 0]]]},
                "properties": {},
            },
            {"type": "Feature", "geometry": None, "properties": {}},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_vector_raster_preserves_zero_nodata_and_null(tmp_path: Path) -> None:
    report = analyze_vector_raster_intersection(
        make_vector(tmp_path / "v.geojson"),
        make_raster(tmp_path / "r.tif"),
        cfg=make_settings(tmp_path),
    )

    assert report["status"] == "GIS_VECTOR_RASTER_OK"
    assert report["features"][0]["mean"] == 0.0
    assert report["features"][0]["state"] == "INTERSECTS_VALID"
    assert report["features"][1]["state"] == "NODATA_ONLY"
    assert report["features"][2]["state"] == "NULL_EMPTY"
    assert report["state_counts"] == {
        "INTERSECTS_VALID": 1,
        "NODATA_ONLY": 1,
        "OUTSIDE_OR_NO_CELL_CENTER": 0,
        "NULL_EMPTY": 1,
        "UNRESOLVED": 0,
    }
    assert all(report["invariants"].values())
    assert len(report["report_sha256"]) == 64


def test_vector_raster_crs_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(GISProcessingError) as exc:
        analyze_vector_raster_intersection(
            make_vector(tmp_path / "v.geojson", "EPSG:3857"),
            make_raster(tmp_path / "r.tif"),
            cfg=make_settings(tmp_path),
        )
    assert exc.value.status == "ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH"


def test_invalid_band_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(GISProcessingError) as exc:
        analyze_vector_raster_intersection(
            make_vector(tmp_path / "v.geojson"),
            make_raster(tmp_path / "r.tif"),
            cfg=make_settings(tmp_path),
            band=0,
        )
    assert exc.value.status == "ERROR_GIS_VECTOR_RASTER_BAND"


def test_malformed_geometry_remains_explicit_unresolved(tmp_path: Path) -> None:
    vector = make_vector(tmp_path / "v.geojson")
    payload = json.loads(vector.read_text(encoding="utf-8"))
    payload["features"].append({"type": "Feature", "geometry": {"type": "NotAGeometry", "coordinates": []}, "properties": {}})
    vector.write_text(json.dumps(payload), encoding="utf-8")

    report = analyze_vector_raster_intersection(vector, make_raster(tmp_path / "r.tif"), cfg=make_settings(tmp_path))

    assert report["status"] == "GIS_VECTOR_RASTER_WARN"
    assert report["state_counts"]["UNRESOLVED"] == 1
    assert report["warnings"] == ["GIS_VECTOR_RASTER_UNRESOLVED_FEATURES"]
    assert report["invariants"]["feature_accounting_closed"] is True


def test_dimensional_and_non_object_features_remain_explicit_unresolved(tmp_path: Path) -> None:
    vector = make_vector(tmp_path / "v.geojson")
    payload = json.loads(vector.read_text(encoding="utf-8"))
    payload["features"].extend(
        [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0.5, 1.5, 3]}, "properties": {}},
            "not-a-feature",
        ]
    )
    vector.write_text(json.dumps(payload), encoding="utf-8")

    report = analyze_vector_raster_intersection(vector, make_raster(tmp_path / "r.tif"), cfg=make_settings(tmp_path))

    assert report["status"] == "GIS_VECTOR_RASTER_WARN"
    assert report["state_counts"]["UNRESOLVED"] == 2
    assert report["features"][-2]["warnings"] == ["GIS_VECTOR_RASTER_DIMENSION_UNSUPPORTED"]
    assert report["features"][-1]["warnings"] == ["ERROR_GIS_VECTOR_RASTER_FEATURE"]
    assert report["invariants"]["feature_accounting_closed"] is True


def test_zonal_statistics_close_numeric_and_nodata_accounting(tmp_path: Path) -> None:
    vector = make_vector(tmp_path / "zones.geojson")
    payload = json.loads(vector.read_text(encoding="utf-8"))
    payload["features"][0]["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
    }
    vector.write_text(json.dumps(payload), encoding="utf-8")

    report = compute_zonal_statistics(vector, make_raster(tmp_path / "values.tif"), cfg=make_settings(tmp_path))

    row = report["features"][0]
    assert report["status"] == "GIS_ZONAL_STATS_OK"
    assert row["covered_cell_count"] == 4
    assert row["valid_cell_count"] == 3
    assert row["nodata_cell_count"] == 1
    assert row["zero_cell_count"] == 1
    assert row["sum"] == 5.0
    assert row["mean"] == pytest.approx(5 / 3)
    assert row["median"] == 2.0
    assert row["population_standard_deviation"] == pytest.approx(1.247219128924647)
    assert row["cell_accounting_closed"] is True
    assert report["invariants"]["cell_accounting_closed"] is True
