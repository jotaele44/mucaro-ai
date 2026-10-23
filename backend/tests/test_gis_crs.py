from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pytest

from backend.app.capability_contracts import (
    CAPABILITY_OUTPUT_CONTRACT_FAILED,
    CAPABILITY_OUTPUT_CONTRACT_OK,
    validate_gis_report_contract,
)
from backend.app.config import Settings
from backend.app.gis_crs import (
    GOVERNED_GRID_BYTES,
    GOVERNED_GRID_FILENAME,
    GOVERNED_GRID_MANIFEST_SHA256,
    GOVERNED_GRID_SHA256,
    reproject_geojson_crs,
)
from backend.app.gis_processing import GISProcessingError

PACKAGE_GRID_ROOT = Path(__file__).resolve().parents[1] / "app" / "gis_grids"
FLORIDA_GRID_FILENAME = "us_noaa_FL.tif"
FLORIDA_GRID_BYTES = 8374
FLORIDA_GRID_SHA256 = "73b385448f09e6c1122a87b5b3137a5b5775585af9445889e0a3a8c4c5951f54"


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


def copy_governed_grid_root(tmp_path: Path, name: str = "grid assets") -> Path:
    root = tmp_path / name
    shutil.copytree(PACKAGE_GRID_ROOT, root)
    return root


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


def test_puerto_rico_reprojection_uses_exact_governed_grid_binding(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "pr-grid.geojson", "EPSG:4326", [-66.0, 18.2])

    report = reproject_geojson_crs(source, target_crs="EPSG:32161", cfg=cfg)
    parameters = report["parameters"]
    governed = parameters["governed_grid"]
    selected = parameters["candidate_inventory"][parameters["selected_candidate_index"]]
    grid = selected["grids"][0]
    output = json.loads(Path(report["output"]["path"]).read_text(encoding="utf-8"))
    x, y = output["features"][0]["geometry"]["coordinates"]

    assert report["status"] == "GIS_CRS_REPROJECT_OK"
    assert report["warnings"] == []
    assert parameters["adapter"] == "pyproj_governed_local_grid_always_xy_v1"
    assert parameters["network_policy"] == "offline_only"
    assert parameters["execution_pipeline_sha256"]
    assert parameters["selected_candidate_index"] == 0
    assert parameters["selected_candidate_grid_count"] == 1
    assert "grids=us_noaa_pvhpgn.tif" in parameters["transformer_definition"]
    assert str(PACKAGE_GRID_ROOT) not in parameters["transformer_definition"]
    assert selected["selection_policy"] == "exact_source_bound_governed_grid"
    assert selected["accuracy"] == 2.0
    assert grid["local_bytes"] == GOVERNED_GRID_BYTES
    assert grid["local_sha256"] == GOVERNED_GRID_SHA256
    assert grid["manifest_sha256"] == GOVERNED_GRID_MANIFEST_SHA256
    assert grid["license_classification"] == "PUBLIC_DOMAIN"
    assert grid["requires_network_download"] is False
    assert governed["network_access_performed"] is False
    assert governed["independent_engine_validation"] == "OPEN"
    assert report["invariants"]["governed_grid_binding_satisfied"] is True
    assert x == pytest.approx(245840.12020116023, abs=1e-8)
    assert y == pytest.approx(240636.2774674058, abs=1e-8)
    assert abs(x - 245839.83344906286) > 0.25
    assert abs(y - 240637.22919110494) > 0.9
    contract = validate_gis_report_contract(report)
    assert contract["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, contract["failed_checks"]


def test_florida_reprojection_uses_second_exact_governed_grid_binding(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "fl-grid.geojson", "EPSG:4269", [-80.2, 25.8])

    report = reproject_geojson_crs(source, target_crs="EPSG:4152", cfg=cfg)
    parameters = report["parameters"]
    governed = parameters["governed_grid"]
    selected = parameters["candidate_inventory"][parameters["selected_candidate_index"]]
    grid = selected["grids"][0]
    output = json.loads(Path(report["output"]["path"]).read_text(encoding="utf-8"))
    lon, lat = output["features"][0]["geometry"]["coordinates"]

    assert report["status"] == "GIS_CRS_REPROJECT_OK"
    assert report["warnings"] == []
    assert parameters["adapter"] == "pyproj_governed_local_grid_always_xy_v1"
    assert parameters["source_crs"] == "EPSG:4269"
    assert parameters["target_crs"] == "EPSG:4152"
    assert parameters["transformer_description"] == "NAD83 to NAD83(HARN) (7)"
    assert "grids=us_noaa_FL.tif" in parameters["transformer_definition"]
    assert selected["selection_policy"] == "exact_source_bound_governed_grid"
    assert selected["accuracy"] == pytest.approx(0.05)
    assert grid["grid_id"] == "us_noaa_fl"
    assert grid["short_name"] == FLORIDA_GRID_FILENAME
    assert grid["local_bytes"] == FLORIDA_GRID_BYTES
    assert grid["local_sha256"] == FLORIDA_GRID_SHA256
    assert grid["manifest_sha256"] == GOVERNED_GRID_MANIFEST_SHA256
    assert grid["license_classification"] == "PUBLIC_DOMAIN"
    assert governed["grid_id"] == "us_noaa_fl"
    assert governed["independent_engine_validation"] == "OPEN"
    assert report["invariants"]["governed_grid_binding_satisfied"] is True
    assert lon == pytest.approx(-80.19999922602221, abs=1e-12)
    assert lat == pytest.approx(25.799999256211102, abs=1e-12)
    contract = validate_gis_report_contract(report)
    assert contract["status"] == CAPABILITY_OUTPUT_CONTRACT_OK, contract["failed_checks"]


def test_governed_grid_manifest_rejects_duplicate_crs_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "fl-duplicate.geojson", "EPSG:4269", [-80.2, 25.8])
    grid_root = copy_governed_grid_root(tmp_path, "grid-duplicate")
    manifest_path = grid_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    florida = next(row for row in manifest["grids"] if row["grid_id"] == "us_noaa_fl")
    manifest["grids"].append(json.loads(json.dumps(florida)))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "backend.app.gis_crs.GOVERNED_GRID_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(
            source,
            target_crs="EPSG:4152",
            cfg=cfg,
            _grid_root=grid_root,
        )

    assert exc.value.status == "ERROR_GIS_CRS_GRID_MANIFEST_INVALID"
    assert list(cfg.export_dir.iterdir()) == []


def test_governed_grid_manifest_rejects_malformed_crs_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "fl-malformed.geojson", "EPSG:4269", [-80.2, 25.8])
    grid_root = copy_governed_grid_root(tmp_path, "grid-malformed")
    manifest_path = grid_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    florida = next(row for row in manifest["grids"] if row["grid_id"] == "us_noaa_fl")
    florida["allowed_operation"]["source_crs"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "backend.app.gis_crs.GOVERNED_GRID_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(
            source,
            target_crs="EPSG:4152",
            cfg=cfg,
            _grid_root=grid_root,
        )

    assert exc.value.status == "ERROR_GIS_CRS_GRID_OPERATION_MISMATCH"
    assert list(cfg.export_dir.iterdir()) == []


def test_governed_grid_supports_package_paths_with_spaces(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "pr-spaces.geojson", "EPSG:4326", [-66.0, 18.2])
    grid_root = copy_governed_grid_root(tmp_path)

    report = reproject_geojson_crs(
        source,
        target_crs="EPSG:32161",
        cfg=cfg,
        _grid_root=grid_root,
    )

    assert report["status"] == "GIS_CRS_REPROJECT_OK"
    assert report["parameters"]["governed_grid"]["grid_sha256"] == GOVERNED_GRID_SHA256


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    [
        ("grid_sha256", "0" * 64, "crs_engine.governed_grid.grid_hash"),
        ("independent_engine_validation", "PASS", "crs_engine.governed_grid.independent_scope"),
    ],
)
def test_governed_grid_contract_rejects_receipt_drift_and_overclaim(
    tmp_path: Path,
    field: str,
    value: str,
    failed_check: str,
) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / f"pr-contract-{field}.geojson", "EPSG:4326", [-66.0, 18.2])
    report = reproject_geojson_crs(source, target_crs="EPSG:32161", cfg=cfg)
    tampered = json.loads(json.dumps(report))
    tampered["parameters"]["governed_grid"][field] = value

    contract = validate_gis_report_contract(tampered)

    assert contract["status"] == CAPABILITY_OUTPUT_CONTRACT_FAILED
    assert failed_check in {check["name"] for check in contract["failed_checks"]}


@pytest.mark.parametrize(
    ("mutation", "expected_status"),
    [
        ("missing", "ERROR_GIS_CRS_GRID_MISSING"),
        ("size", "ERROR_GIS_CRS_GRID_SIZE_MISMATCH"),
        ("hash", "ERROR_GIS_CRS_GRID_HASH_MISMATCH"),
        ("manifest", "ERROR_GIS_CRS_GRID_MANIFEST_HASH_MISMATCH"),
        ("path_escape", "ERROR_GIS_CRS_GRID_PATH_INVALID"),
    ],
)
def test_governed_grid_integrity_failures_publish_no_output(
    tmp_path: Path,
    mutation: str,
    expected_status: str,
) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / f"pr-{mutation}.geojson", "EPSG:4326", [-66.0, 18.2])
    grid_root = copy_governed_grid_root(tmp_path, f"grid-{mutation}")
    grid_path = grid_root / GOVERNED_GRID_FILENAME

    if mutation == "missing":
        grid_path.unlink()
    elif mutation == "size":
        grid_path.write_bytes(grid_path.read_bytes() + b"x")
    elif mutation == "hash":
        payload = bytearray(grid_path.read_bytes())
        payload[-1] ^= 1
        grid_path.write_bytes(payload)
    elif mutation == "manifest":
        manifest_path = grid_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["network_policy"] = "allow_remote"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif mutation == "path_escape":
        outside = tmp_path / GOVERNED_GRID_FILENAME
        shutil.copy2(grid_path, outside)
        grid_path.unlink()
        grid_path.symlink_to(outside)

    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(
            source,
            target_crs="EPSG:32161",
            cfg=cfg,
            _grid_root=grid_root,
        )

    assert exc.value.status == expected_status
    assert list(cfg.export_dir.iterdir()) == []


def test_governed_grid_fails_closed_outside_operation_area(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "outside-pr.geojson", "EPSG:4326", [-80.0, 25.0])

    with pytest.raises(GISProcessingError) as exc:
        reproject_geojson_crs(source, target_crs="EPSG:32161", cfg=cfg)

    assert exc.value.status == "ERROR_GIS_CRS_TRANSFORM"
    assert list(cfg.export_dir.iterdir()) == []


def test_non_governed_crs_pair_retains_general_offline_policy(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = write_fc(tmp_path / "utm.geojson", "EPSG:4326", [-66.0, 18.2])

    report = reproject_geojson_crs(source, target_crs="EPSG:32620", cfg=cfg)

    assert report["status"] == "GIS_CRS_REPROJECT_OK"
    assert report["parameters"]["adapter"] == "pyproj_transformer_group_always_xy_v1"
    assert report["parameters"]["governed_grid"] is None
    assert report["parameters"]["execution_pipeline_sha256"] is None


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
