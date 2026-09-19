from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError
from backend.app.gis_raster import clip_raster_bbox, inspect_raster, resample_raster, sample_raster


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
    ); cfg.ensure_dirs(); return cfg


def make_raster(path: Path) -> Path:
    data = np.array([[1, 2], [3, -9999]], dtype="int16")
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1, dtype="int16", crs="EPSG:4326", transform=from_origin(-67, 19, 1, 1), nodata=-9999) as dst:
        dst.write(data, 1)
    return path


def test_inspect_preserves_nodata_and_band_accounting(tmp_path: Path) -> None:
    report = inspect_raster(make_raster(tmp_path / "a.tif"), cfg=make_settings(tmp_path))
    assert report["manifest"]["crs"] == "EPSG:4326"
    assert report["manifest"]["bands"][0]["nodata_cell_count"] == 1
    assert report["invariants"]["band_accounting_closed"] is True


def test_sample_distinguishes_sample_nodata_and_outside(tmp_path: Path) -> None:
    report = sample_raster(make_raster(tmp_path / "b.tif"), [(-66.5, 18.5), (-65.5, 17.5), (-70, 20)], cfg=make_settings(tmp_path))
    assert report["state_counts"] == {"SAMPLED": 1, "NODATA": 1, "OUTSIDE": 1}
    assert report["invariants"]["point_accounting_closed"] is True


def test_clip_hashes_output_and_rejects_nonoverlap(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path); source = make_raster(tmp_path / "c.tif")
    report = clip_raster_bbox(source, (-67, 18, -66, 19), cfg=cfg)
    assert report["output"]["sha256"]
    assert report["invariants"]["crs_preserved"] is True
    with pytest.raises(GISProcessingError) as exc:
        clip_raster_bbox(source, (0, 0, 1, 1), cfg=cfg)
    assert exc.value.status == "ERROR_GIS_RASTER_CLIP_OUTSIDE"


def test_raster_operations_fail_closed_on_missing_or_malformed_inputs(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    source = make_raster(tmp_path / "d.tif")

    with pytest.raises(GISProcessingError) as exc:
        sample_raster(tmp_path / "missing.tif", [(0, 0)], cfg=cfg)
    assert exc.value.status == "ERROR_GIS_RASTER_NOT_FOUND"

    with pytest.raises(GISProcessingError) as exc:
        sample_raster(source, [], cfg=cfg)
    assert exc.value.status == "ERROR_GIS_RASTER_POINTS"

    with pytest.raises(GISProcessingError) as exc:
        sample_raster(source, [(float("nan"), 18)], cfg=cfg)
    assert exc.value.status == "ERROR_GIS_RASTER_POINTS"

    with pytest.raises(GISProcessingError) as exc:
        clip_raster_bbox(source, (0, 0, float("inf"), 1), cfg=cfg)
    assert exc.value.status == "ERROR_GIS_RASTER_BBOX"


def test_resample_raster_preserves_governed_grid_metadata(tmp_path: Path) -> None:
    report = resample_raster(
        make_raster(tmp_path / "resample.tif"),
        width=4,
        height=4,
        method="nearest",
        cfg=make_settings(tmp_path),
    )

    assert report["status"] == "GIS_RASTER_RESAMPLE_OK"
    assert report["output"]["width"] == 4
    assert report["output"]["height"] == 4
    assert report["source"]["bounds"] == report["output"]["bounds"]
    assert report["source"]["nodata_values"] == report["output"]["nodata_values"]
    assert report["source"]["bands"][0] == {
        "band": 1,
        "valid_cell_count": 3,
        "nodata_cell_count": 1,
        "zero_cell_count": 0,
    }
    assert report["output"]["bands"][0] == {
        "band": 1,
        "valid_cell_count": 12,
        "nodata_cell_count": 4,
        "zero_cell_count": 0,
    }
    assert all(report["invariants"].values())
    assert not Path(report["output"]["path"]).with_suffix(".tmp.tif").exists()


@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    [
        ({"width": 0, "height": 2, "method": "nearest"}, "ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS"),
        ({"width": 2, "height": 2, "method": "cubic"}, "ERROR_GIS_RASTER_RESAMPLE_METHOD"),
    ],
)
def test_resample_raster_rejects_ungoverned_parameters(tmp_path: Path, kwargs: dict[str, object], expected_status: str) -> None:
    with pytest.raises(GISProcessingError) as exc:
        resample_raster(make_raster(tmp_path / f"{expected_status}.tif"), cfg=make_settings(tmp_path), **kwargs)
    assert exc.value.status == expected_status


def test_resample_raster_limits_total_values_across_bands(tmp_path: Path) -> None:
    source = tmp_path / "two-band.tif"
    data = np.array([[[1, 2], [3, -9999]], [[4, 5], [6, -9999]]], dtype="int16")
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=2,
        dtype="int16",
        crs="EPSG:4326",
        transform=from_origin(-67, 19, 1, 1),
        nodata=-9999,
    ) as target:
        target.write(data)

    with pytest.raises(GISProcessingError) as exc:
        resample_raster(source, width=5001, height=5000, method="nearest", cfg=make_settings(tmp_path))
    assert exc.value.status == "ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS"


def test_resample_raster_requires_declared_crs(tmp_path: Path) -> None:
    source = tmp_path / "missing-crs.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="int16",
        transform=from_origin(-67, 19, 1, 1),
        nodata=-9999,
    ) as target:
        target.write(np.array([[1, 2], [3, -9999]], dtype="int16"), 1)

    with pytest.raises(GISProcessingError) as exc:
        resample_raster(source, width=4, height=4, method="nearest", cfg=make_settings(tmp_path))
    assert exc.value.status == "ERROR_GIS_RASTER_RESAMPLE_CRS"
    assert not list((tmp_path / "data" / "exports" / "gis_reports").glob("*.tmp.tif"))
