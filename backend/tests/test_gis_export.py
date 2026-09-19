from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.gis_export import create_map_export_manifest
from backend.app.gis_processing import GISProcessingError


def test_export_manifest_binds_output_layers_renderer_and_crs(tmp_path: Path) -> None:
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
    ); cfg.ensure_dirs()
    layer = tmp_path / "layer.geojson"; layer.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    output = tmp_path / "map.png"; output.write_bytes(b"fake-rendered-map")
    report = create_map_export_manifest(cfg=cfg, output_path=output, layers=[{"path": str(layer), "crs": "EPSG:4326", "name": "test"}], extent=[-67,17,-65,19], output_crs="EPSG:4326", renderer="fixture-renderer", renderer_version="1.0")
    assert report["invariants"]["all_layer_hashes_present"] is True
    assert report["output"]["sha256"]
    assert report["renderer"]["version"] == "1.0"


def test_export_manifest_rejects_missing_layer(tmp_path: Path) -> None:
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
    ); cfg.ensure_dirs(); output = tmp_path / "map.png"; output.write_bytes(b"x")
    with pytest.raises(GISProcessingError) as exc:
        create_map_export_manifest(cfg=cfg, output_path=output, layers=[{"path": str(tmp_path / "missing.geojson")}], extent=[0,0,1,1], output_crs="EPSG:4326", renderer="r", renderer_version="1")
    assert exc.value.status == "ERROR_GIS_EXPORT_LAYER"
