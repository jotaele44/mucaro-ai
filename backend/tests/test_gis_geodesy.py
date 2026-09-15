import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.gis_geodesy import measure_geojson_ellipsoidal
from backend.app.gis_processing import GISProcessingError


def cfg(tmp_path: Path) -> Settings:
    value = Settings(
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
    value.ensure_dirs()
    return value


def fc(path: Path, crs: str, geometry: dict) -> Path:
    path.write_text(json.dumps({"type":"FeatureCollection","crs":{"type":"name","properties":{"name":crs}},"features":[{"type":"Feature","id":"x","properties":{},"geometry":geometry}]}), encoding="utf-8"); return path


def test_wgs84_ellipsoidal_line_measurement(tmp_path: Path) -> None:
    source = fc(tmp_path / "line.geojson", "EPSG:4326", {"type":"LineString","coordinates":[[-66.0,18.0],[-66.0,19.0]]})
    report = measure_geojson_ellipsoidal(source, cfg=cfg(tmp_path))
    assert report["status"] == "GIS_ELLIPSOIDAL_OK"
    assert 110000 < report["measurements"][0]["length_m"] < 111000
    assert report["invariants"]["row_accounting_closed"] is True


def test_polygon_area_and_perimeter_are_positive(tmp_path: Path) -> None:
    source = fc(tmp_path / "poly.geojson", "EPSG:4326", {"type":"Polygon","coordinates":[[[-66.1,18.0],[-66.0,18.0],[-66.0,18.1],[-66.1,18.1],[-66.1,18.0]]]})
    report = measure_geojson_ellipsoidal(source, cfg=cfg(tmp_path))
    assert report["measurements"][0]["area_m2"] > 0
    assert report["measurements"][0]["length_m"] > 0


def test_non_4326_fails_closed(tmp_path: Path) -> None:
    source = fc(tmp_path / "merc.geojson", "EPSG:3857", {"type":"Point","coordinates":[0,0]})
    with pytest.raises(GISProcessingError) as exc:
        measure_geojson_ellipsoidal(source, cfg=cfg(tmp_path))
    assert exc.value.status == "ERROR_GIS_GEOD_CRS"
