from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from backend.app.capability_contracts import (
    CAPABILITY_OUTPUT_CONTRACT_FAILED,
    CAPABILITY_OUTPUT_CONTRACT_OK,
    validate_gis_report_contract,
)
from backend.app.cli import main
from backend.app.config import Settings
from backend.app.gis_crs import reproject_geojson_crs
from backend.app.gis_export import create_map_export_manifest
from backend.app.gis_geodesy import measure_geojson_ellipsoidal
from backend.app.gis_raster import clip_raster_bbox, inspect_raster, sample_raster
from backend.app.gis_topology_repair import repair_geojson_topology
from backend.app.gis_vector_formats import inspect_vector_format
from backend.app.gis_vector_raster import analyze_vector_raster_intersection


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


def write_raster(path: Path, *, crs: str = "EPSG:4326") -> Path:
    data = np.array([[0, 2], [3, -9999]], dtype="int16")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        crs=crs,
        transform=from_origin(0, 2, 1, 1),
        nodata=-9999,
    ) as target:
        target.write(data, 1)
    return path


def test_advanced_vector_cli_commands_emit_governed_receipts(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    point = write_geojson(
        tmp_path / "point.geojson",
        [{"type": "Feature", "id": "point", "properties": {}, "geometry": {"type": "Point", "coordinates": [-66, 18.2]}}],
    )
    bowtie = write_geojson(
        tmp_path / "bowtie.geojson",
        [
            {
                "type": "Feature",
                "id": "bowtie",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]},
            }
        ],
    )
    line = write_geojson(
        tmp_path / "line.geojson",
        [{"type": "Feature", "id": "line", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[-66, 18], [-66, 19]]}}],
    )

    assert main(["gis", "reproject-crs", str(point), "--target-crs", "EPSG:32620", "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_CRS_REPROJECT_OK" in output
    assert "adapter=pyproj_transformer_group_always_xy_v1" in output
    assert "axis_order_explicit=True" in output
    assert "transform_pipeline_frozen=True" in output

    assert main(["gis", "repair-topology", str(bowtie), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_TOPOLOGY_REPAIR_OK" in output
    assert "adapter=shapely_make_valid_v1" in output
    assert "repair_is_explicit=True" in output
    assert "REPAIRED=1" in output

    assert main(["gis", "measure-ellipsoidal", str(line), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_ELLIPSOIDAL_OK" in output
    assert "adapter=pyproj_geod_wgs84_v1" in output
    assert "ellipsoid=WGS84" in output
    assert "MEASURED=1" in output

    assert main(["gis", "inspect-vector", str(point), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_VECTOR_FORMAT_OK" in output
    assert "source_format=GEOJSON" in output
    assert "manifest_feature_count=1" in output


def test_advanced_raster_and_export_cli_commands_emit_governed_receipts(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    raster = write_raster(tmp_path / "grid.tif")
    vector = write_geojson(
        tmp_path / "cells.geojson",
        [
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
    )

    assert main(["gis", "inspect-raster", str(raster), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_INSPECT_OK" in output
    assert "manifest_band_count=1" in output
    assert "band_accounting_closed=True" in output

    assert main(
        [
            "gis",
            "sample-raster",
            str(raster),
            "--point",
            "0.5",
            "1.5",
            "--point",
            "1.5",
            "0.5",
            "--point",
            "5",
            "5",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_SAMPLE_OK" in output
    assert "SAMPLED=1" in output
    assert "NODATA=1" in output
    assert "OUTSIDE=1" in output

    assert main(
        [
            "gis",
            "clip-raster-bbox",
            str(raster),
            "--min-x",
            "0",
            "--min-y",
            "1",
            "--max-x",
            "1",
            "--max-y",
            "2",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_CLIP_OK" in output
    assert "source_and_output_hash_present=True" in output

    assert main(["gis", "vector-raster", str(vector), str(raster), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_VECTOR_RASTER_OK" in output
    assert "nodata_separate_from_zero=True" in output
    assert "INTERSECTS_VALID=1" in output
    assert "NODATA_ONLY=1" in output

    rendered = tmp_path / "map.png"
    rendered.write_bytes(b"rendered-map")
    assert main(
        [
            "gis",
            "map-export-manifest",
            str(rendered),
            "--layer",
            str(vector),
            "EPSG:4326",
            "--extent",
            "0",
            "0",
            "2",
            "2",
            "--output-crs",
            "EPSG:4326",
            "--renderer",
            "fixture-renderer",
            "--renderer-version",
            "1.0",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_MAP_EXPORT_MANIFEST_OK" in output
    assert "renderer=fixture-renderer" in output
    assert "bound_layer_count=1" in output
    assert "all_layer_hashes_present=True" in output


def test_advanced_gis_contract_accepts_real_reports_and_rejects_false_accounting(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    point = write_geojson(
        tmp_path / "point.geojson",
        [{"type": "Feature", "id": "point", "properties": {}, "geometry": {"type": "Point", "coordinates": [-66, 18.2]}}],
    )
    bowtie = write_geojson(
        tmp_path / "bowtie.geojson",
        [{"type": "Feature", "id": "bowtie", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]]}}],
    )
    line = write_geojson(
        tmp_path / "line.geojson",
        [{"type": "Feature", "id": "line", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[-66, 18], [-66, 19]]}}],
    )
    raster = write_raster(tmp_path / "grid.tif")
    vector = write_geojson(
        tmp_path / "cell.geojson",
        [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 1], [1, 1], [1, 2], [0, 2], [0, 1]]]}, "properties": {}}],
    )
    rendered = tmp_path / "map.png"
    rendered.write_bytes(b"rendered-map")

    reports = [
        reproject_geojson_crs(point, target_crs="EPSG:32620", cfg=cfg),
        repair_geojson_topology(bowtie, cfg=cfg),
        measure_geojson_ellipsoidal(line, cfg=cfg),
        inspect_vector_format(point, cfg=cfg),
        inspect_raster(raster, cfg=cfg),
        sample_raster(raster, [(0.5, 1.5)], cfg=cfg),
        clip_raster_bbox(raster, (0, 1, 1, 2), cfg=cfg),
        analyze_vector_raster_intersection(vector, raster, cfg=cfg),
        create_map_export_manifest(
            cfg=cfg,
            output_path=rendered,
            layers=[{"path": str(vector), "crs": "EPSG:4326", "name": "cell", "role": "source"}],
            extent=[0, 0, 2, 2],
            output_crs="EPSG:4326",
            renderer="fixture-renderer",
            renderer_version="1.0",
        ),
    ]

    for report in reports:
        result = validate_gis_report_contract(report)
        assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, result["failed_checks"]

    tampered = copy.deepcopy(reports[1])
    tampered["invariants"]["output_accounting_closed"] = False
    result = validate_gis_report_contract(tampered)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "invariants.output_accounting_closed" in {
        check["name"] for check in result["failed_checks"]
    }

    unresolved_pipeline = copy.deepcopy(reports[0])
    unresolved_pipeline["parameters"]["transformer_definition"] = "unavailable until proj_trans is called"
    result = validate_gis_report_contract(unresolved_pipeline)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "crs_engine.parameters.pipeline_definition" in {
        check["name"] for check in result["failed_checks"]
    }
