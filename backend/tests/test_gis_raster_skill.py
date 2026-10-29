from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from backend.app import gis_raster_skill as task, work_runs
from backend.app.artifact_identity import trace_ancestors
from backend.app.cli import main
from backend.app.conversations import create_conversation
from backend.app.main import create_app
from backend.app.permissions import grant_directory, revoke_directory
from backend.app.skill_runtime import SkillRuntimeError, activate_skill, execute_skill, mount_skill
from backend.app.tools import approve_tool_call, list_tool_calls
from backend.tests.test_api import make_settings

SKILL = "mucaro/gis-raster"


def raster(path, *, values=None, nodata=-9999, crs="EPSG:32620", compress=None):
    values = np.tile(np.arange(4, -1, -1, dtype="float32"), (5, 1)) if values is None else values
    data = values[np.newaxis] if values.ndim == 2 else values
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype=str(data.dtype),
        crs=crs,
        transform=from_origin(500000, 2000000, 1, 1),
        nodata=nodata,
        compress=compress,
    ) as output:
        output.write(data)
    return path


@pytest.fixture
def setup_raster(tmp_path):
    cfg = make_settings(tmp_path)
    source = raster(tmp_path / "elevation.tif")
    zones = tmp_path / "zones.geojson"
    zones.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": "EPSG:32620"}},
                "features": [
                    {
                        "type": "Feature",
                        "id": "zone-1",
                        "properties": {"raw": "Múcaro  "},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [500000, 1999995],
                                    [500005, 1999995],
                                    [500005, 2000000],
                                    [500000, 2000000],
                                    [500000, 1999995],
                                ]
                            ],
                        },
                    }
                ],
            }
        )
    )
    mount_skill(SKILL, cfg=cfg)
    activate_skill(SKILL, cfg=cfg, allowed_capabilities={"filesystem.read", "filesystem.write"})
    return cfg, source, zones


def arguments(operation, source, zones):
    args = {"operation": operation, "path": str(source)}
    if operation == "sample":
        args.update(
            points=[[500000.5, 1999999.5], [500004.5, 1999999.5], [500005, 1999999.5]], coordinate_crs="EPSG:32620"
        )
    if operation == "clip":
        args.update(min_x=500001, min_y=1999996, max_x=500004, max_y=1999999, coordinate_crs="EPSG:32620")
    if operation == "resample":
        args.update(width=10, height=10)
    if operation in task.ELEVATION_OPERATIONS:
        args["elevation_units"] = "meters"
    if operation == "condition_depressions":
        args["maximum_fill_depth_m"] = 0
    if operation in task.VECTOR_OPERATIONS:
        args["vector_path"] = str(zones)
    return args


@pytest.mark.parametrize("operation", list(task.OPERATIONS))
def test_raster_task_operations_use_real_engines_and_close_lineage(setup_raster, operation):
    cfg, source, zones = setup_raster
    original = source.read_bytes()
    response = execute_skill(SKILL, arguments(operation, source, zones), cfg=cfg)
    result = response["result"]
    assert result["kind"] == "GIS_RASTER_ANALYSIS" and result["trust"] == "UNTRUSTED_CONTENT"
    assert result["identity_state"] == "CANDIDATE_NOT_IDENTITY"
    assert source.read_bytes() == original
    assert Path(result["source_manifests"][0]["snapshot_path"]).read_bytes() == original
    assert all(value is not False for value in result["report"]["invariants"].values())
    parents = trace_ancestors(cfg.processed_dir, response["execution"]["artifact_lineage"]["output"]["artifact_id"])
    assert set(result["source_artifact_ids"] + result["artifact_ids"]).issubset(
        {parent["artifact_id"] for parent in parents}
    )
    report = result["report"]
    assert hashlib.sha256(Path(report["report_path"]).read_bytes()).hexdigest() == report["report_sha256"]
    if operation == "sample":
        assert report["samples"][0]["values"] == [4]
        assert report["samples"][1]["values"] == [0]
        assert report["samples"][2]["state"] == "OUTSIDE"
    if operation == "terrain":
        with rasterio.open(report["output"]["path"]) as output:
            assert output.read(1)[2, 2] == pytest.approx(45)
    if operation == "hillshade":
        assert len(result["artifact_ids"]) == 4
        assert Path(report["terrain_lineage"]["report_path"]).exists()
    if operation == "condition_depressions":
        assert report["depression_fill_accounting"]["raised_cell_count"] == 0
    if operation == "d8_flow":
        assert report["state_counts"]["FLOW_UNIQUE"] == 9
    if operation in task.VECTOR_OPERATIONS:
        assert report["features"][0]["valid_cell_count"] == 25
        assert report["features"][0]["mean"] == 2


@pytest.mark.parametrize("operation", task.VECTOR_OPERATIONS)
def test_raster_zones_preserve_fractional_coverage_accounting(setup_raster, operation):
    cfg, source, zones = setup_raster
    args = {**arguments(operation, source, zones), "inclusion_rule": "fractional_coverage"}
    result = execute_skill(SKILL, args, cfg=cfg)["result"]
    assert result["report"]["features"][0]["covered_area"] == 25
    assert result["report"]["features"][0]["mean"] == 2
    assert result["report"]["invariants"]["coverage_accounting_closed"]


def test_partial_band_nodata_and_zero_are_distinct_and_json_safe(setup_raster):
    cfg, source, zones = setup_raster
    raster(source, values=np.array([[[0]], [[-9999]]], dtype="float32"))
    args = {
        "operation": "sample",
        "path": str(source),
        "points": [[500000.5, 1999999.5]],
        "coordinate_crs": "EPSG:32620",
    }
    result = execute_skill(SKILL, args, cfg=cfg)["result"]
    assert result["report"]["samples"][0]["state"] == "PARTIAL_NODATA"
    assert result["report"]["samples"][0]["values"] == [0, None]
    assert result["report"]["samples"][0]["band_states"] == ["SAMPLED", "NODATA"]
    assert result["report"]["invariants"]["point_accounting_closed"]
    assert result["status"] == "GIS_SKILL_REVIEW_REQUIRED"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "operation", ["inspect", "resample", "terrain", "hillshade", "condition_depressions", "d8_flow"]
)
def test_nan_nodata_preserves_raw_metadata_and_a_strict_json_view(setup_raster, operation):
    cfg, source, zones = setup_raster
    values = np.tile(np.arange(4, -1, -1, dtype="float32"), (5, 1))
    values[0, 0] = np.nan
    raster(source, values=values, nodata=np.nan)
    result = execute_skill(SKILL, arguments(operation, source, zones), cfg=cfg)["result"]
    assert result["source_manifests"][0]["raster"]["bands"][0] == {
        "band": 1,
        "valid_cell_count": 24,
        "nodata_cell_count": 1,
        "zero_cell_count": 5,
    }
    assert any(item["state"] == "NAN" for item in result["numeric_metadata_states"])
    assert result["source_manifests"][0]["raster"]["nodata_values"] == [None]
    assert result["status"] == "GIS_SKILL_REVIEW_REQUIRED"
    json.dumps(result, allow_nan=False)
    json.loads(
        Path(result["report"]["report_path"]).read_text(),
        parse_constant=lambda value: pytest.fail(f"Non-standard JSON number: {value}"),
    )
    response = TestClient(create_app(cfg)).post(
        f"/skills/{SKILL}/execute", json={"arguments": arguments(operation, source, zones)}
    )
    assert response.status_code == 200


def test_compressed_raster_bomb_is_rejected_before_any_pixel_read(setup_raster, monkeypatch):
    cfg, source, _ = setup_raster
    raster(source, values=np.zeros((1000, 1000), dtype="uint8"), nodata=None, compress="deflate")
    assert source.stat().st_size < 100_000
    open_raster = task.rasterio.open
    reads = []

    class MetadataOnly:
        def __init__(self, dataset):
            self.dataset = dataset

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.dataset.close()

        def __getattr__(self, name):
            return getattr(self.dataset, name)

        def read(self, *args, **kwargs):
            reads.append(True)
            raise AssertionError("Oversized raster must be rejected before pixels are decoded.")

    monkeypatch.setattr(task.rasterio, "open", lambda *args, **kwargs: MetadataOnly(open_raster(*args, **kwargs)))
    with pytest.raises(SkillRuntimeError, match="ERROR_GIS_RASTER_SIZE_LIMIT"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)
    assert reads == []


def test_virtual_dataset_disguised_as_tiff_never_reaches_gdal(setup_raster, monkeypatch):
    cfg, source, _ = setup_raster
    source.write_text(
        '<VRTDataset rasterXSize="5" rasterYSize="5"><SourceFilename>/etc/passwd</SourceFilename></VRTDataset>'
    )
    monkeypatch.setattr(task.rasterio, "open", lambda *args, **kwargs: pytest.fail("VRT reached GDAL"))
    with pytest.raises(SkillRuntimeError, match="DRIVER_DENIED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)


@pytest.mark.parametrize(
    "change,error",
    [({"coordinate_crs": "EPSG:4326"}, "COORDINATE_CRS"), ({"points": [[float("nan"), 0]]}, "ARGUMENTS_INVALID")],
)
def test_sample_coordinates_require_finite_values_and_matching_declared_crs(setup_raster, change, error):
    cfg, source, zones = setup_raster
    with pytest.raises(SkillRuntimeError) as refused:
        execute_skill(SKILL, {**arguments("sample", source, zones), **change}, cfg=cfg)
    assert error in refused.value.status or error in str(refused.value)


def test_nonfinite_valid_cells_and_scaled_elevation_fail_before_derivation(setup_raster):
    cfg, source, zones = setup_raster
    raster(source, values=np.array([[np.inf, 0], [1, 2]], dtype="float32"))
    with pytest.raises(SkillRuntimeError, match="NONFINITE_DATA"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)
    raster(source)
    with rasterio.open(source, "r+") as dataset:
        dataset.scales = (0.1,)
    with pytest.raises(SkillRuntimeError, match="SCALED_ELEVATION"):
        execute_skill(SKILL, arguments("terrain", source, zones), cfg=cfg)


def test_raster_schema_rejects_hidden_or_unsafe_parameters_before_acquisition(setup_raster):
    cfg, source, _ = setup_raster
    for args in (
        {"operation": "terrain", "path": str(source)},
        {"operation": "inspect", "path": str(source), "maximum_fill_depth_m": 1},
        {"operation": "sample", "path": str(source), "points": [[0]], "coordinate_crs": "EPSG:32620"},
        {"operation": "inspect", "path": str(source), "driver": "VRT"},
    ):
        with pytest.raises(SkillRuntimeError) as refused:
            execute_skill(SKILL, args, cfg=cfg)
        assert refused.value.status == "ERROR_SKILL_ARGUMENTS_INVALID"
    assert not list(cfg.input_dir.glob("gis_raster-snapshot-*"))


def test_raster_permission_revocation_and_stale_hash_are_enforced(setup_raster, tmp_path):
    cfg, source, _ = setup_raster
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    external = outside / "private.tif"
    external.write_bytes(source.read_bytes())
    link = cfg.project_root / "link.tif"
    link.symlink_to(external)
    with pytest.raises(SkillRuntimeError, match="DIRECTORY_PERMISSION_REQUIRED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(link)}, cfg=cfg)
    grant_directory(outside, cfg, granted_by="test")
    assert (
        execute_skill(SKILL, {"operation": "inspect", "path": str(link)}, cfg=cfg)["result"]["report"]["status"]
        == "GIS_RASTER_INSPECT_OK"
    )
    revoke_directory(outside, cfg)
    with pytest.raises(SkillRuntimeError, match="DIRECTORY_PERMISSION_REQUIRED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(link)}, cfg=cfg)
    with pytest.raises(SkillRuntimeError, match="SOURCE_CHANGED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source), "expected_sha256": "0" * 64}, cfg=cfg)


def test_raster_parallel_execution_retains_distinct_reports_and_parents(setup_raster):
    cfg, source, _ = setup_raster
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg), range(4))
        )
    assert len({response["result"]["report"]["report_path"] for response in results}) == 4
    for response in results:
        assert (
            len(trace_ancestors(cfg.processed_dir, response["execution"]["artifact_lineage"]["output"]["artifact_id"]))
            >= 3
        )


def test_raster_cli_api_and_conversation_use_shared_runtime(setup_raster, capsys):
    cfg, source, _ = setup_raster
    args = {"operation": "inspect", "path": str(source)}
    client = TestClient(create_app(cfg))
    assert client.post(f"/skills/{SKILL}/execute", json={"arguments": args}).status_code == 200
    assert main(["skills", "execute", SKILL, "--arguments", json.dumps(args)], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["result"]["source_manifests"][0]["raster"]["width"] == 5
    work_runs.set_execution_mode("inline")
    try:
        conversation = create_conversation("Raster work", cfg)["conversation_id"]
        run = work_runs.submit_message(conversation, f"/skill {SKILL} {json.dumps(args)}", cfg=cfg)["run"]
        assert work_runs.get_run(run["run_id"], cfg)["state"] == "AWAITING_APPROVAL"
        call = list_tool_calls(cfg)["tool_calls"][0]
        approve_tool_call(call["tool_call_id"], cfg=cfg)
        work_runs.resume_waiting_run(run["run_id"], cfg)
        assert work_runs.get_run(run["run_id"], cfg)["state"] == "COMPLETED"
    finally:
        work_runs.set_execution_mode("thread")


def test_raster_tampered_terrain_intermediate_cannot_enter_output_lineage(setup_raster, monkeypatch):
    cfg, source, zones = setup_raster
    handler = task.derive_raster_hillshade

    def tamper(path, **kwargs):
        report = handler(path, **kwargs)
        Path(report["terrain_lineage"]["output_path"]).write_bytes(b"tampered intermediate")
        return report

    monkeypatch.setattr(task, "derive_raster_hillshade", tamper)
    with pytest.raises(SkillRuntimeError, match="OUTPUT_HASH_MISMATCH"):
        execute_skill(SKILL, arguments("hillshade", source, zones), cfg=cfg)


def test_raster_source_integrity_is_rechecked_after_the_engine(setup_raster, monkeypatch):
    cfg, source, _ = setup_raster
    handler = task.inspect_raster

    def tamper(path, **kwargs):
        report = handler(path, **kwargs)
        path.chmod(0o600)
        path.write_bytes(b"changed frozen source")
        return report

    monkeypatch.setattr(task, "inspect_raster", tamper)
    with pytest.raises(SkillRuntimeError, match="SOURCE_CHANGED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)


def test_raster_band_output_and_unit_boundaries_stop_wrong_derivations(setup_raster):
    cfg, source, zones = setup_raster
    for args, status in [
        ({**arguments("terrain", source, zones), "band": 2}, "BAND_INVALID"),
        ({"operation": "resample", "path": str(source), "width": 1000, "height": 1000}, "OUTPUT_SIZE_LIMIT"),
    ]:
        with pytest.raises(SkillRuntimeError, match=status):
            execute_skill(SKILL, args, cfg=cfg)
    with rasterio.open(source, "r+") as dataset:
        dataset.set_band_unit(1, "ft")
    with pytest.raises(SkillRuntimeError, match="ELEVATION_UNIT_CONFLICT"):
        execute_skill(SKILL, arguments("terrain", source, zones), cfg=cfg)


@pytest.mark.parametrize(
    "policy,key,value",
    [
        (task.GDAL_ENV, "GDAL_PAM_ENABLED", True),
        (task.RASTER_EXECUTION_POLICY["vector_input_policy"], "max_features", 10000),
    ],
    ids=["gdal_options", "borrowed_vector_policy"],
)
def test_gdal_policy_changes_invalidate_existing_raster_activation(setup_raster, monkeypatch, policy, key, value):
    cfg, source, _ = setup_raster
    monkeypatch.setitem(policy, key, value)
    with pytest.raises(SkillRuntimeError) as refused:
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)
    assert refused.value.status == "ERROR_SKILL_DIGEST_MISMATCH"


def test_zonal_numeric_overflow_cannot_be_reported_as_a_valid_sum(setup_raster):
    cfg, source, zones = setup_raster
    raster(source, values=np.full((5, 5), 1e308, dtype="float64"))
    with pytest.raises(SkillRuntimeError, match="NONFINITE_RESULT"):
        execute_skill(SKILL, arguments("zonal_statistics", source, zones), cfg=cfg)


def test_multigrid_tiff_is_not_silently_reduced_to_its_first_grid(setup_raster):
    from PIL import Image

    cfg, source, _ = setup_raster
    Image.new("L", (2, 2)).save(source, save_all=True, append_images=[Image.new("L", (2, 2))])
    with pytest.raises(SkillRuntimeError, match="MULTIGRID_UNSUPPORTED"):
        execute_skill(SKILL, {"operation": "inspect", "path": str(source)}, cfg=cfg)
