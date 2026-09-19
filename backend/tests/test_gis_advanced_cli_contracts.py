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
from backend.app.gis_corpus import DEFAULT_REAL_GIS_CORPUS_MANIFEST, validate_real_gis_corpus
from backend.app.gis_export import create_map_export_manifest
from backend.app.gis_geodesy import measure_geojson_ellipsoidal
from backend.app.gis_raster import (
    clip_raster_bbox,
    derive_raster_hillshade,
    derive_raster_terrain,
    inspect_raster,
    resample_raster,
    sample_raster,
)
from backend.app.gis_topology_repair import repair_geojson_topology
from backend.app.gis_vector_formats import inspect_vector_format
from backend.app.gis_vector_raster import analyze_vector_raster_intersection, compute_zonal_statistics


ROOT = Path(__file__).resolve().parents[2]


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


def write_terrain_raster(path: Path) -> Path:
    data = np.tile(np.arange(5, dtype="float32"), (5, 1))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=5,
        height=5,
        count=1,
        dtype="float32",
        crs="EPSG:32620",
        transform=from_origin(500000, 2000000, 1, 1),
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


def test_crs_grid_candidates_are_inventoried_and_selected_offline(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    point = write_geojson(
        tmp_path / "puerto-rico.geojson",
        [{"type": "Feature", "id": "point", "properties": {}, "geometry": {"type": "Point", "coordinates": [-66, 18.2]}}],
    )

    report = reproject_geojson_crs(point, target_crs="EPSG:32161", cfg=cfg)
    parameters = report["parameters"]
    candidates = parameters["candidate_inventory"]
    selected = candidates[parameters["selected_candidate_index"]]
    all_grid_rows = [
        grid
        for candidate in candidates
        for grid in candidate["grids"]
    ] + [
        grid
        for operation in parameters["unavailable_operation_inventory"]
        for grid in operation["grids"]
    ]

    assert parameters["network_policy"] == "offline_only"
    assert selected["offline_capable"] is True
    assert selected["requires_network_download"] is False
    assert selected["grid_provenance_frozen"] is True
    assert report["invariants"]["offline_policy_satisfied"] is True
    assert report["invariants"]["grid_inventory_complete"] is True
    assert "us_noaa_pvhpgn.tif" in {row["short_name"] for row in all_grid_rows}
    assert all(not grid["requires_network_download"] for grid in selected["grids"])
    assert all(grid["provenance_frozen"] for grid in selected["grids"])
    assert all(
        not grid["local_file_available"] or (grid["local_bytes"] > 0 and len(grid["local_sha256"]) == 64)
        for grid in all_grid_rows
    )
    if parameters["selected_candidate_index"] > 0:
        assert "GIS_CRS_OFFLINE_CANDIDATE_FALLBACK" in report["warnings"]
        assert report["status"] == "GIS_CRS_REPROJECT_WARN"

    contract = validate_gis_report_contract(report)
    assert contract["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, contract["failed_checks"]


def test_ellipsoidal_edge_conditions_and_invalid_coordinates_close_accounting(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_geojson(
        tmp_path / "geodesic-edges.geojson",
        [
            {"type": "Feature", "id": "antimeridian-line", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[179, 0], [-179, 0]]}},
            {"type": "Feature", "id": "polar-line", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 89], [90, 89]]}},
            {"type": "Feature", "id": "antimeridian-polygon", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[179, 10], [-179, 10], [-179, -10], [179, -10], [179, 10]]]}},
            {"type": "Feature", "id": "invalid-latitude", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [0, 91]]}},
        ],
    )

    report = measure_geojson_ellipsoidal(source, cfg=cfg)
    rows = {row["feature_id"]: row for row in report["measurements"]}
    assert report["status"] == "GIS_ELLIPSOIDAL_WARN"
    assert report["warnings"] == ["GIS_ELLIPSOIDAL_EDGE_CONDITIONS_PRESENT", "GIS_ELLIPSOIDAL_UNRESOLVED"]
    assert report["state_counts"] == {"MEASURED": 3, "NULL_EMPTY": 0, "UNRESOLVED": 1}
    assert report["edge_condition_counts"] == {
        "ANTIMERIDIAN_CROSSING": 2,
        "NEAR_NORTH_POLE": 1,
        "NEAR_SOUTH_POLE": 0,
        "TOUCHES_POLE": 0,
    }
    assert rows["antimeridian-line"]["edge_conditions"] == ["ANTIMERIDIAN_CROSSING"]
    assert rows["polar-line"]["edge_conditions"] == ["NEAR_NORTH_POLE"]
    assert rows["invalid-latitude"]["state"] == "UNRESOLVED"
    assert rows["invalid-latitude"]["warnings"] == ["ERROR_GIS_GEOD_COORDINATES"]
    assert report["invariants"]["row_accounting_closed"] is True
    assert report["invariants"]["edge_condition_accounting_closed"] is True

    contract = validate_gis_report_contract(report)
    assert contract["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, contract["failed_checks"]


def test_advanced_raster_and_export_cli_commands_emit_governed_receipts(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    raster = write_raster(tmp_path / "grid.tif")
    terrain_raster = write_terrain_raster(tmp_path / "terrain.tif")
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

    assert main(["gis", "zonal-stats", str(vector), str(raster), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_ZONAL_STATS_OK" in output
    assert "cell_accounting_closed=True" in output
    assert "INTERSECTS_VALID=1" in output

    assert main(
        [
            "gis",
            "resample-raster",
            str(raster),
            "--width",
            "4",
            "--height",
            "4",
            "--method",
            "nearest",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_RESAMPLE_OK" in output
    assert "dimensions_exact=True" in output
    assert "nodata_definition_preserved=True" in output

    assert main(
        [
            "gis",
            "derive-terrain",
            str(terrain_raster),
            "--elevation-units",
            "meters",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_TERRAIN_OK" in output
    assert "neighborhood_valid_cell_count=9" in output
    assert "flat_aspect_accounting_closed=True" in output
    assert "output_band_count_exact=True" in output

    assert main(
        [
            "gis",
            "derive-hillshade",
            str(terrain_raster),
            "--elevation-units",
            "meters",
            "--azimuth",
            "270",
            "--altitude",
            "45",
            "--format",
            "text",
        ],
        cfg=cfg,
    ) == 0
    output = capsys.readouterr().out
    assert "GIS_RASTER_HILLSHADE_OK" in output
    assert "valid_cell_count=9" in output
    assert "fully_lit_cell_count=9" in output
    assert "hillshade_range_bounded=True" in output

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
    terrain_raster = write_terrain_raster(tmp_path / "terrain.tif")
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
        compute_zonal_statistics(vector, raster, cfg=cfg),
        resample_raster(raster, width=4, height=4, method="bilinear", cfg=cfg),
        derive_raster_terrain(terrain_raster, elevation_units="meters", cfg=cfg),
        derive_raster_hillshade(
            terrain_raster,
            elevation_units="meters",
            azimuth_degrees=270,
            altitude_degrees=45,
            cfg=cfg,
        ),
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

    network_dependent_selection = copy.deepcopy(reports[0])
    selected_index = network_dependent_selection["parameters"]["selected_candidate_index"]
    network_dependent_selection["parameters"]["candidate_inventory"][selected_index][
        "requires_network_download"
    ] = True
    result = validate_gis_report_contract(network_dependent_selection)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "crs_engine.parameters.selected_candidate_offline" in {
        check["name"] for check in result["failed_checks"]
    }

    tampered_edge_count = copy.deepcopy(reports[2])
    tampered_edge_count["edge_condition_counts"]["ANTIMERIDIAN_CROSSING"] = 1
    result = validate_gis_report_contract(tampered_edge_count)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "ellipsoidal.edge_count.ANTIMERIDIAN_CROSSING" in {
        check["name"] for check in result["failed_checks"]
    }

    tampered_zonal = copy.deepcopy(reports[8])
    tampered_zonal["features"][0]["covered_cell_count"] = 99
    result = validate_gis_report_contract(tampered_zonal)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "vector_raster.cell_accounting" in {check["name"] for check in result["failed_checks"]}

    tampered_terrain = copy.deepcopy(reports[10])
    tampered_terrain["terrain_accounting"]["flat_cell_count"] = 1
    result = validate_gis_report_contract(tampered_terrain)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "terrain.aspect_accounting" in {check["name"] for check in result["failed_checks"]}

    malformed_terrain_bands = copy.deepcopy(reports[10])
    malformed_terrain_bands["output"]["bands"] = [None, None]
    result = validate_gis_report_contract(malformed_terrain_bands)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "terrain.output_bands" in {check["name"] for check in result["failed_checks"]}

    tampered_hillshade = copy.deepcopy(reports[11])
    tampered_hillshade["hillshade_accounting"]["shadow_cell_count"] = 99
    result = validate_gis_report_contract(tampered_hillshade)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "hillshade.illumination_accounting" in {
        check["name"] for check in result["failed_checks"]
    }

    malformed_hillshade_bands = copy.deepcopy(reports[11])
    malformed_hillshade_bands["output"]["bands"] = [None]
    result = validate_gis_report_contract(malformed_hillshade_bands)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "hillshade.output_bands" in {check["name"] for check in result["failed_checks"]}

    mismatched_hillshade_source = copy.deepcopy(reports[11])
    mismatched_hillshade_source["terrain_lineage"]["source_sha256"] = "0" * 64
    result = validate_gis_report_contract(mismatched_hillshade_source)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "hillshade.terrain_source_identity" in {
        check["name"] for check in result["failed_checks"]
    }


def test_real_corpus_cli_and_contract_share_one_receipt_boundary(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    cfg = Settings(
        project_root=ROOT,
        data_dir=cfg.data_dir,
        input_dir=cfg.input_dir,
        processed_dir=cfg.processed_dir,
        export_dir=cfg.export_dir,
        index_dir=cfg.index_dir,
        sqlite_path=cfg.sqlite_path,
        default_model_provider="mock",
        default_model_name="mock",
    )

    assert main(["gis", "validate-corpus", str(DEFAULT_REAL_GIS_CORPUS_MANIFEST), "--format", "text"], cfg=cfg) == 0
    output = capsys.readouterr().out
    assert "GIS_CORPUS_VALIDATION_OK" in output
    assert "asset_count=3" in output
    assert "type=geojson features=3" in output
    assert "type=geojson features=30" in output
    assert "type=raster features=None grid=64x64 crs=EPSG:32620" in output

    report = validate_real_gis_corpus(DEFAULT_REAL_GIS_CORPUS_MANIFEST, cfg=cfg)
    result = validate_gis_report_contract(report)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, result["failed_checks"]

    tampered = copy.deepcopy(report)
    tampered["assets"][2]["bands"][0]["valid_cell_count"] = 4095
    result = validate_gis_report_contract(tampered)
    assert result["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert "gis_corpus.assets.2.band_accounting" in {
        check["name"] for check in result["failed_checks"]
    }
