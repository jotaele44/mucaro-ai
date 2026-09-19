from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

from backend.app.config import Settings
from backend.app.gis_processing import GISProcessingError
from backend.app.gis_raster import (
    clip_raster_bbox,
    derive_raster_hillshade,
    derive_raster_terrain,
    inspect_raster,
    resample_raster,
    sample_raster,
)


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


def make_terrain_raster(
    path: Path,
    data: np.ndarray,
    *,
    crs: str = "EPSG:32620",
    transform: Affine | None = None,
    nodata: float = -9999.0,
) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=str(data.dtype),
        crs=crs,
        transform=transform or from_origin(500000, 2000000, 1, 1),
        nodata=nodata,
    ) as target:
        target.write(data, 1)
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


def test_derive_terrain_reports_analytic_east_rising_plane(tmp_path: Path) -> None:
    data = np.tile(np.arange(5, dtype="float32"), (5, 1))
    report = derive_raster_terrain(
        make_terrain_raster(tmp_path / "east-rising.tif", data),
        band=1,
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )

    assert report["status"] == "GIS_RASTER_TERRAIN_OK"
    assert report["terrain_accounting"] == {
        "total_cell_count": 25,
        "source_valid_cell_count": 25,
        "source_nodata_cell_count": 0,
        "neighborhood_valid_cell_count": 9,
        "neighborhood_excluded_cell_count": 16,
        "flat_cell_count": 0,
        "slope_valid_cell_count": 9,
        "aspect_valid_cell_count": 9,
    }
    assert report["output"]["bands"][0]["minimum"] == pytest.approx(45.0)
    assert report["output"]["bands"][0]["maximum"] == pytest.approx(45.0)
    assert report["output"]["bands"][1]["minimum"] == pytest.approx(270.0)
    assert report["output"]["bands"][1]["maximum"] == pytest.approx(270.0)
    assert all(report["invariants"].values())

    with rasterio.open(report["output"]["path"]) as output:
        assert output.count == 2
        assert output.descriptions == ("slope_degrees", "aspect_degrees_clockwise_from_north")
        assert int(output.read(1, masked=True).count()) == 9
        assert int(output.read(2, masked=True).count()) == 9

    north_rising = np.tile(np.arange(4, -1, -1, dtype="float32")[:, None], (1, 5))
    north_report = derive_raster_terrain(
        make_terrain_raster(tmp_path / "north-rising.tif", north_rising),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert north_report["output"]["bands"][0]["minimum"] == pytest.approx(45.0)
    assert north_report["output"]["bands"][1]["minimum"] == pytest.approx(180.0)
    assert north_report["output"]["bands"][1]["maximum"] == pytest.approx(180.0)


def test_derive_terrain_excludes_nodata_neighborhoods_and_flat_aspect(tmp_path: Path) -> None:
    data = np.tile(np.arange(7, dtype="float32"), (7, 1))
    data[3, 3] = -9999.0
    nodata_report = derive_raster_terrain(
        make_terrain_raster(tmp_path / "nodata.tif", data),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert nodata_report["terrain_accounting"]["source_nodata_cell_count"] == 1
    assert nodata_report["terrain_accounting"]["neighborhood_valid_cell_count"] == 16
    assert nodata_report["terrain_accounting"]["neighborhood_excluded_cell_count"] == 33
    assert nodata_report["terrain_accounting"]["slope_valid_cell_count"] == 16

    flat = np.ones((5, 5), dtype="float32")
    flat_report = derive_raster_terrain(
        make_terrain_raster(tmp_path / "flat.tif", flat),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert flat_report["terrain_accounting"]["flat_cell_count"] == 9
    assert flat_report["terrain_accounting"]["slope_valid_cell_count"] == 9
    assert flat_report["terrain_accounting"]["aspect_valid_cell_count"] == 0
    assert flat_report["output"]["bands"][0]["zero_cell_count"] == 9
    assert flat_report["output"]["bands"][1]["nodata_cell_count"] == 25


def test_derive_hillshade_binds_terrain_lineage_and_illumination_direction(tmp_path: Path) -> None:
    data = np.tile(np.arange(5, dtype="float32"), (5, 1))
    source = make_terrain_raster(tmp_path / "east-rising-hillshade.tif", data)
    west_light = derive_raster_hillshade(
        source,
        elevation_units="meters",
        azimuth_degrees=270,
        altitude_degrees=45,
        cfg=make_settings(tmp_path),
    )

    assert west_light["status"] == "GIS_RASTER_HILLSHADE_OK"
    assert west_light["parameters"]["azimuth_degrees_clockwise_from_north"] == 270.0
    assert west_light["terrain_lineage"]["output_sha256"]
    assert west_light["hillshade_accounting"] == {
        "total_cell_count": 25,
        "valid_cell_count": 9,
        "nodata_cell_count": 16,
        "flat_cell_count": 0,
        "shadow_cell_count": 0,
        "illuminated_cell_count": 9,
        "fully_lit_cell_count": 9,
    }
    assert west_light["output"]["bands"][0]["minimum"] == pytest.approx(255.0)
    assert west_light["output"]["bands"][0]["maximum"] == pytest.approx(255.0)
    assert all(west_light["invariants"].values())

    with rasterio.open(west_light["output"]["path"]) as output:
        assert output.count == 1
        assert output.dtypes == ("float32",)
        assert output.nodatavals == (-9999.0,)
        assert output.descriptions == ("hillshade_0_255",)
        assert int(output.read(1, masked=True).count()) == 9

    east_light = derive_raster_hillshade(
        source,
        elevation_units="meters",
        azimuth_degrees=90,
        altitude_degrees=45,
        cfg=make_settings(tmp_path),
    )
    assert east_light["hillshade_accounting"]["shadow_cell_count"] == 9
    assert east_light["output"]["bands"][0]["maximum"] == pytest.approx(0.0, abs=1e-6)


def test_derive_hillshade_preserves_flat_and_nodata_states(tmp_path: Path) -> None:
    flat = np.ones((5, 5), dtype="float32")
    flat_report = derive_raster_hillshade(
        make_terrain_raster(tmp_path / "flat-hillshade.tif", flat),
        elevation_units="meters",
        azimuth_degrees=315,
        altitude_degrees=30,
        cfg=make_settings(tmp_path),
    )
    assert flat_report["hillshade_accounting"]["flat_cell_count"] == 9
    assert flat_report["hillshade_accounting"]["valid_cell_count"] == 9
    assert flat_report["output"]["bands"][0]["minimum"] == pytest.approx(127.5)
    assert flat_report["output"]["bands"][0]["nodata_cell_count"] == 16

    nodata = np.tile(np.arange(7, dtype="float32"), (7, 1))
    nodata[3, 3] = -9999.0
    nodata_report = derive_raster_hillshade(
        make_terrain_raster(tmp_path / "nodata-hillshade.tif", nodata),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert nodata_report["hillshade_accounting"]["valid_cell_count"] == 16
    assert nodata_report["hillshade_accounting"]["nodata_cell_count"] == 33
    assert nodata_report["invariants"]["terrain_validity_mask_preserved"] is True


@pytest.mark.parametrize(
    ("kwargs", "expected_status"),
    [
        ({"azimuth_degrees": -1}, "ERROR_GIS_HILLSHADE_AZIMUTH"),
        ({"azimuth_degrees": 360}, "ERROR_GIS_HILLSHADE_AZIMUTH"),
        ({"azimuth_degrees": float("nan")}, "ERROR_GIS_HILLSHADE_AZIMUTH"),
        ({"altitude_degrees": 0}, "ERROR_GIS_HILLSHADE_ALTITUDE"),
        ({"altitude_degrees": 91}, "ERROR_GIS_HILLSHADE_ALTITUDE"),
        ({"altitude_degrees": float("inf")}, "ERROR_GIS_HILLSHADE_ALTITUDE"),
    ],
)
def test_derive_hillshade_rejects_invalid_illumination_before_writing_artifacts(
    tmp_path: Path,
    kwargs: dict[str, float],
    expected_status: str,
) -> None:
    source = make_terrain_raster(
        tmp_path / f"{expected_status}.tif",
        np.ones((5, 5), dtype="float32"),
    )
    with pytest.raises(GISProcessingError) as exc:
        derive_raster_hillshade(
            source,
            elevation_units="meters",
            cfg=make_settings(tmp_path),
            **kwargs,
        )
    assert exc.value.status == expected_status
    report_dir = tmp_path / "data" / "exports" / "gis_reports"
    assert not list(report_dir.glob("*"))


def test_derive_hillshade_fails_closed_when_no_valid_neighborhood_exists(tmp_path: Path) -> None:
    source = make_terrain_raster(
        tmp_path / "all-nodata.tif",
        np.full((5, 5), -9999.0, dtype="float32"),
    )
    cfg = make_settings(tmp_path)

    with pytest.raises(GISProcessingError) as exc:
        derive_raster_hillshade(source, elevation_units="meters", cfg=cfg)

    assert exc.value.status == "ERROR_GIS_HILLSHADE_EMPTY"
    report_dir = tmp_path / "data" / "exports" / "gis_reports"
    assert len(list(report_dir.glob("*.json"))) == 1
    assert len(list(report_dir.glob("*.tif"))) == 1
    assert not list(report_dir.glob("*.tmp.tif"))


@pytest.mark.parametrize(
    ("source_factory", "kwargs", "expected_status"),
    [
        (
            lambda path: make_terrain_raster(path, np.ones((5, 5), dtype="float32"), crs="EPSG:4326"),
            {"elevation_units": "meters"},
            "ERROR_GIS_TERRAIN_CRS",
        ),
        (
            lambda path: make_terrain_raster(path, np.ones((5, 5), dtype="float32")),
            {"band": 2, "elevation_units": "meters"},
            "ERROR_GIS_TERRAIN_BAND",
        ),
        (
            lambda path: make_terrain_raster(path, np.ones((5, 5), dtype="float32")),
            {"elevation_units": "feet"},
            "ERROR_GIS_TERRAIN_UNITS",
        ),
        (
            lambda path: make_terrain_raster(path, np.ones((5, 5), dtype="float32"), crs="EPSG:2263"),
            {"elevation_units": "meters"},
            "ERROR_GIS_TERRAIN_UNITS",
        ),
        (
            lambda path: make_terrain_raster(
                path,
                np.ones((5, 5), dtype="float32"),
                transform=Affine(1, 0.1, 500000, 0, -1, 2000000),
            ),
            {"elevation_units": "meters"},
            "ERROR_GIS_TERRAIN_TRANSFORM",
        ),
        (
            lambda path: make_terrain_raster(path, np.ones((2, 2), dtype="float32")),
            {"elevation_units": "meters"},
            "ERROR_GIS_TERRAIN_DIMENSIONS",
        ),
    ],
)
def test_derive_terrain_rejects_unsafe_grids_and_parameters(
    tmp_path: Path,
    source_factory,
    kwargs: dict[str, object],
    expected_status: str,
) -> None:
    with pytest.raises(GISProcessingError) as exc:
        derive_raster_terrain(
            source_factory(tmp_path / f"{expected_status}.tif"),
            cfg=make_settings(tmp_path),
            **kwargs,
        )
    assert exc.value.status == expected_status
    assert not list((tmp_path / "data" / "exports" / "gis_reports").glob("*.tmp.tif"))
