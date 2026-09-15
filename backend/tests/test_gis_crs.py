from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.gis_crs import reproject_geojson_crs
from backend.app.gis_processing import GISProcessingError


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


def write_fc(path: Path, crs: str, coordinates: list[float]) -> Path:
    path.write_text(json.dumps({"type":"FeatureCollection","crs":{"type":"name","properties":{"name":crs}},"features":[{"type":"Feature","id":"p","properties":{},"geometry":{"type":"Point","coordinates":coordinates}}]}), encoding="utf-8")
    return path


def test_pyproj_reprojection_records_pipeline_and_axis_policy(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "pr.geojson", "EPSG:4326", [-66.0, 18.2])
    report = reproject_geojson_crs(source, target_crs="EPSG:32161", cfg=cfg)
    assert report["status"] == "GIS_CRS_REPROJECT_OK"
    assert report["parameters"]["always_xy"] is True
    assert report["parameters"]["transformer_definition_sha256"]
    assert report["invariants"]["feature_row_conservation"] is True
    assert report["output"]["crs"] == "EPSG:32161"


def test_m_coordinates_fail_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "m.geojson", "EPSG:4326", [-66.0, 18.2, 5.0, 9.0])
    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(source, target_crs="EPSG:3857", cfg=cfg)
    assert exc.value.status == "ERROR_GIS_CRS_M_UNSUPPORTED"


def test_invalid_crs_fails_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "bad.geojson", "EPSG:4326", [-66.0, 18.2])
    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(source, target_crs="EPSG:NOTREAL", cfg=cfg)
    assert exc.value.status == "ERROR_GIS_CRS_UNSUPPORTED"
