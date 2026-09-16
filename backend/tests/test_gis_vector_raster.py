from pathlib import Path
import json
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError
from backend.app.gis_vector_raster import analyze_vector_raster_intersection


def make_settings(tmp_path: Path) -> Settings:
    cfg = Settings(project_root=tmp_path); cfg.ensure_dirs(); return cfg


def make_raster(path: Path) -> Path:
    data = np.array([[0, 2], [3, -9999]], dtype="int16")
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1, dtype="int16", crs="EPSG:4326", transform=from_origin(0, 2, 1, 1), nodata=-9999) as dst: dst.write(data, 1)
    return path


def make_vector(path: Path, crs: str = "EPSG:4326") -> Path:
    payload = {"type":"FeatureCollection","crs":{"type":"name","properties":{"name":crs}},"features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[0,1],[1,1],[1,2],[0,2],[0,1]]]},"properties":{}},{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[1,0],[2,0],[2,1],[1,1],[1,0]]]},"properties":{}},{"type":"Feature","geometry":None,"properties":{}}]}
    path.write_text(json.dumps(payload), encoding="utf-8"); return path


def test_vector_raster_preserves_zero_nodata_and_null(tmp_path: Path) -> None:
    report = analyze_vector_raster_intersection(make_vector(tmp_path / "v.geojson"), make_raster(tmp_path / "r.tif"), cfg=make_settings(tmp_path))
    assert report["features"][0]["mean"] == 0.0
    assert report["features"][0]["state"] == "INTERSECTS_VALID"
    assert report["features"][1]["state"] == "NODATA_ONLY"
    assert report["features"][2]["state"] == "NULL_EMPTY"
    assert report["invariants"]["feature_accounting_closed"] is True


def test_vector_raster_crs_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(GISProcessingError) as exc:
        analyze_vector_raster_intersection(make_vector(tmp_path / "v.geojson", "EPSG:3857"), make_raster(tmp_path / "r.tif"), cfg=make_settings(tmp_path))
    assert exc.value.status == "ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH"
