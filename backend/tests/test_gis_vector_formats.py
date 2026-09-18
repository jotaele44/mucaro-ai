from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

from backend.app.config import Settings
from backend.app.gis_vector_formats import inspect_vector_format


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


def test_geojson_manifest_preserves_geometry_and_crs(tmp_path: Path) -> None:
    path = tmp_path / "a.geojson"
    path.write_text(json.dumps({"type":"FeatureCollection","crs":{"type":"name","properties":{"name":"EPSG:4326"}},"features":[{"type":"Feature","id":"p","properties":{},"geometry":{"type":"Point","coordinates":[-66,18]}}]}), encoding="utf-8")
    report = inspect_vector_format(path, cfg=cfg(tmp_path))
    assert report["source"]["format"] == "GEOJSON"
    assert report["manifest"]["feature_count"] == 1
    assert report["manifest"]["geometry_types"] == {"Point": 1}


def test_geopackage_metadata_is_read_without_synthesizing_features(tmp_path: Path) -> None:
    path = tmp_path / "a.gpkg"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, identifier TEXT, srs_id INTEGER)")
    db.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER)")
    db.execute("INSERT INTO gpkg_contents VALUES ('roads','features','roads',4326)")
    db.execute("INSERT INTO gpkg_geometry_columns VALUES ('roads','geom','LINESTRING',4326,0,0)")
    db.commit(); db.close()
    report = inspect_vector_format(path, cfg=cfg(tmp_path))
    assert report["manifest"]["feature_table_count"] == 1
    assert report["manifest"]["geometry_columns"][0]["geometry_type"] == "LINESTRING"


def test_shapefile_sidecars_are_independent_manifestations(tmp_path: Path) -> None:
    path = tmp_path / "a.shp"
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into("<i", header, 28, 1000)
    struct.pack_into("<i", header, 32, 1)
    struct.pack_into("<4d", header, 36, -66.1, 18.0, -66.0, 18.1)
    path.write_bytes(header)
    path.with_suffix('.prj').write_text('GEOGCS["WGS 84"]', encoding='utf-8')
    report = inspect_vector_format(path, cfg=cfg(tmp_path))
    assert report["manifest"]["shape_type_code"] == 1
    assert report["manifest"]["sidecars"][".prj"]["present"] is True
    assert report["manifest"]["sidecars"][".prj"]["sha256"]
