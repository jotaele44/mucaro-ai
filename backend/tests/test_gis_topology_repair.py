from __future__ import annotations

import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.gis_topology_repair import repair_geojson_topology


def make_settings(tmp_path: Path) -> Settings:
    cfg = Settings(project_root=tmp_path)
    cfg.ensure_dirs()
    return cfg


def write_fc(path: Path, features: list[dict]) -> Path:
    path.write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}, "features": features}), encoding="utf-8")
    return path


def test_explicit_topology_repair_records_before_after_hashes(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "invalid.geojson", [{"type": "Feature", "id": "bowtie", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]}}])
    report = repair_geojson_topology(source, cfg=cfg)
    assert report["status"] == "GIS_TOPOLOGY_REPAIR_OK"
    assert report["state_counts"]["REPAIRED"] == 1
    row = report["features"][0]
    assert row["source_geometry_sha256"] != row["output_geometry_sha256"]
    assert report["invariants"]["row_accounting_closed"] is True
    assert report["invariants"]["output_accounting_closed"] is True
    assert report["invariants"]["repair_is_explicit"] is True


def test_valid_geometry_is_not_mutated(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    source = write_fc(tmp_path / "valid.geojson", [{"type": "Feature", "id": "valid", "properties": {}, "geometry": geometry}])
    report = repair_geojson_topology(source, cfg=cfg)
    row = report["features"][0]
    assert row["repair_state"] == "UNCHANGED_VALID"
    assert row["source_geometry_sha256"] == row["output_geometry_sha256"]


def test_z_geometry_fails_closed_as_unresolved(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "z.geojson", [{"type": "Feature", "id": "z", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0, 1], [2, 0, 1], [2, 2, 1], [0, 2, 1], [0, 0, 1]]]}}])
    report = repair_geojson_topology(source, cfg=cfg)
    assert report["status"] == "GIS_TOPOLOGY_REPAIR_WARN"
    assert report["state_counts"]["UNRESOLVED"] == 1
    assert report["output"]["feature_count"] == 0
    assert "GIS_REPAIR_COORDINATE_DIMENSION_UNSUPPORTED" in report["features"][0]["warnings"]
