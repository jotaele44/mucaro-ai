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
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": crs}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "p",
                        "properties": {},
                        "geometry": {"type": "Point", "coordinates": coordinates},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_pyproj_reprojection_records_pipeline_and_axis_policy(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "pr.geojson", "EPSG:4326", [-66.0, 18.2])
    report = reproject_geojson_crs(source, target_crs="EPSG:32161", cfg=cfg)
    parameters = report["parameters"]
    selected_index = parameters["selected_candidate_index"]
    selected = parameters["candidate_inventory"][selected_index]
    expected_status = "GIS_CRS_REPROJECT_WARN" if report["warnings"] else "GIS_CRS_REPROJECT_OK"
    assert report["status"] == expected_status
    if not parameters["best_available"]:
        assert "GIS_CRS_BEST_OPERATION_UNAVAILABLE" in report["warnings"]
    if selected_index > 0:
        assert "GIS_CRS_OFFLINE_CANDIDATE_FALLBACK" in report["warnings"]
    if any(
        candidate["requires_network_download"]
        for candidate in parameters["candidate_inventory"][:selected_index]
    ):
        assert "GIS_CRS_NETWORK_GRID_SKIPPED" in report["warnings"]
    assert parameters["always_xy"] is True
    assert parameters["network_policy"] == "offline_only"
    assert selected["offline_capable"] is True
    assert selected["requires_network_download"] is False
    assert selected["grid_provenance_frozen"] is True
    assert parameters["transformer_definition_sha256"]
    assert not parameters["transformer_definition"].lower().startswith("unavailable until")
    assert parameters["available_candidate_count"] >= 1
    assert parameters["unavailable_candidate_count"] >= 0
    assert parameters["pyproj_version"]
    assert parameters["proj_version"]
    assert report["invariants"]["feature_row_conservation"] is True
    assert report["invariants"]["transform_pipeline_frozen"] is True
    assert report["invariants"]["offline_policy_satisfied"] is True
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
