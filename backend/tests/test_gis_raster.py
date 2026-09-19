from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError
from backend.app.gis_raster import clip_raster_bbox, inspect_raster, sample_raster


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
