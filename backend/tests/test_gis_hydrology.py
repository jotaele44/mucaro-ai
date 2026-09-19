from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

from backend.app.config import Settings
from backend.app.gis_hydrology import (
    D8_DIRECTION_CODEBOOK,
    D8_STATE_CODEBOOK,
    derive_raster_d8_flow,
)
from backend.app.gis_processing import GISProcessingError

ROOT = Path(__file__).resolve().parents[2]
REAL_RASTER = ROOT / "backend" / "tests" / "fixtures" / "gis_corpus" / "usgs_3dep_pr_elevation_utm20n_64x64.tif"


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


def write_elevation(
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


def test_d8_routes_east_and_closes_terminal_accumulation(tmp_path: Path) -> None:
    data = np.tile(np.arange(4, -1, -1, dtype="float32"), (5, 1))
    report = derive_raster_d8_flow(
        write_elevation(tmp_path / "east-descent.tif", data),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )

    assert report["status"] == "GIS_RASTER_D8_OK"
    assert report["warnings"] == []
    assert report["direction_counts"] == {
        "E": 9,
        "SE": 0,
        "S": 0,
        "SW": 0,
        "W": 0,
        "NW": 0,
        "N": 0,
        "NE": 0,
    }
    assert report["state_counts"] == {
        "FLOW_UNIQUE": 9,
        "BOUNDARY_TERMINAL": 16,
        "SINK_UNRESOLVED": 0,
        "FLAT_UNRESOLVED": 0,
        "TIE_UNRESOLVED": 0,
    }
    assert report["flow_accounting"]["terminal_accumulation_cell_count_total"] == 25.0
    assert report["flow_accounting"]["maximum_accumulation_cell_count"] == 4.0
    assert all(report["invariants"].values())

    with rasterio.open(report["output"]["path"]) as output:
        direction = output.read(1, masked=True)
        accumulation = output.read(2, masked=True)
        area = output.read(3, masked=True)
        state = output.read(4, masked=True)
        assert output.count == 4
        assert output.dtypes == ("float64", "float64", "float64", "float64")
        assert output.descriptions == (
            "d8_candidate_bitmask_esri",
            "flow_accumulation_cells_including_self",
            "contributing_area_square_metres",
            "flow_state_code",
        )
        assert direction[2, 2] == D8_DIRECTION_CODEBOOK["E"]
        assert accumulation[2, 2] == 2.0
        assert accumulation[2, 4] == 4.0
        assert area[2, 4] == 4.0
        assert state[2, 2] == D8_STATE_CODEBOOK["FLOW_UNIQUE"]
        assert state[2, 4] == D8_STATE_CODEBOOK["BOUNDARY_TERMINAL"]


def test_d8_accumulates_branched_bowl_into_explicit_sink(tmp_path: Path) -> None:
    data = np.full((5, 5), 10.0, dtype="float32")
    data[1:4, 1:4] = 1.0
    data[2, 2] = 0.0
    report = derive_raster_d8_flow(
        write_elevation(tmp_path / "bowl.tif", data),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )

    assert report["status"] == "GIS_RASTER_D8_WARN"
    assert report["warnings"] == ["GIS_D8_SINKS_UNRESOLVED"]
    assert report["state_counts"] == {
        "FLOW_UNIQUE": 8,
        "BOUNDARY_TERMINAL": 16,
        "SINK_UNRESOLVED": 1,
        "FLAT_UNRESOLVED": 0,
        "TIE_UNRESOLVED": 0,
    }
    assert report["flow_accounting"]["maximum_accumulation_cell_count"] == 9.0
    assert report["flow_accounting"]["terminal_accumulation_cell_count_total"] == 25.0
    assert report["invariants"]["terminal_contribution_accounting_closed"] is True


def test_d8_preserves_tied_candidates_and_unresolved_flats(tmp_path: Path) -> None:
    tie_data = np.array(
        [[20, 20, 20], [20, 10, 9], [20, 9, 20]],
        dtype="float32",
    )
    tie_report = derive_raster_d8_flow(
        write_elevation(tmp_path / "tie.tif", tie_data),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert tie_report["warnings"] == ["GIS_D8_TIES_UNRESOLVED"]
    assert tie_report["state_counts"]["TIE_UNRESOLVED"] == 1
    assert tie_report["flow_accounting"]["tie_candidate_direction_count"] == 2
    with rasterio.open(tie_report["output"]["path"]) as output:
        assert output.read(1)[1, 1] == D8_DIRECTION_CODEBOOK["E"] + D8_DIRECTION_CODEBOOK["S"]
        assert output.read(4)[1, 1] == D8_STATE_CODEBOOK["TIE_UNRESOLVED"]

    flat_report = derive_raster_d8_flow(
        write_elevation(tmp_path / "flat.tif", np.ones((3, 3), dtype="float32")),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert flat_report["warnings"] == ["GIS_D8_FLATS_UNRESOLVED"]
    assert flat_report["state_counts"]["FLAT_UNRESOLVED"] == 1
    assert flat_report["flow_accounting"]["zero_direction_cell_count"] == 9


def test_d8_uses_true_diagonal_distance(tmp_path: Path) -> None:
    data = np.array(
        [[20, 20, 20], [20, 10, 9], [20, 20, 8]],
        dtype="float32",
    )
    report = derive_raster_d8_flow(
        write_elevation(tmp_path / "diagonal.tif", data),
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )
    assert report["direction_counts"]["SE"] == 1
    with rasterio.open(report["output"]["path"]) as output:
        assert output.read(1)[1, 1] == D8_DIRECTION_CODEBOOK["SE"]


@pytest.mark.parametrize(
    ("factory_kwargs", "derive_kwargs", "expected_status"),
    [
        ({"crs": "EPSG:4326"}, {}, "ERROR_GIS_D8_CRS"),
        ({"crs": "EPSG:2263"}, {}, "ERROR_GIS_D8_UNITS"),
        ({"transform": Affine(1, 0.1, 500000, 0, -1, 2000000)}, {}, "ERROR_GIS_D8_TRANSFORM"),
        ({}, {"band": 2}, "ERROR_GIS_D8_BAND"),
        ({}, {"elevation_units": "feet"}, "ERROR_GIS_D8_UNITS"),
        ({}, {"elevation_tolerance_m": -1}, "ERROR_GIS_D8_TOLERANCE"),
        ({}, {"gradient_tie_tolerance": float("nan")}, "ERROR_GIS_D8_TOLERANCE"),
    ],
)
def test_d8_rejects_unsafe_grid_and_parameters(
    tmp_path: Path,
    factory_kwargs: dict[str, object],
    derive_kwargs: dict[str, object],
    expected_status: str,
) -> None:
    source = write_elevation(
        tmp_path / f"{expected_status}.tif",
        np.tile(np.arange(4, -1, -1, dtype="float32"), (5, 1)),
        **factory_kwargs,
    )
    parameters = {"elevation_units": "meters", **derive_kwargs}
    with pytest.raises(GISProcessingError) as exc:
        derive_raster_d8_flow(source, cfg=make_settings(tmp_path), **parameters)
    assert exc.value.status == expected_status
    assert not list((tmp_path / "data" / "exports" / "gis_reports").glob("*.tmp.tif"))


def test_d8_rejects_empty_and_all_boundary_inputs(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(GISProcessingError) as exc:
        derive_raster_d8_flow(
            write_elevation(
                tmp_path / "all-nodata.tif",
                np.full((5, 5), -9999.0, dtype="float32"),
            ),
            elevation_units="meters",
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_GIS_D8_EMPTY"

    sparse = np.full((5, 5), -9999.0, dtype="float32")
    sparse[2, 2] = 1.0
    with pytest.raises(GISProcessingError) as exc:
        derive_raster_d8_flow(
            write_elevation(tmp_path / "no-complete.tif", sparse),
            elevation_units="meters",
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_GIS_D8_NO_COMPLETE_NEIGHBORHOOD"


def test_d8_executes_against_frozen_usgs_3dep_manifestation(tmp_path: Path) -> None:
    report = derive_raster_d8_flow(
        REAL_RASTER,
        elevation_units="meters",
        cfg=make_settings(tmp_path),
    )

    assert report["source"]["sha256"] == "9cc3ba603944b5e6e1a9251082338c1f94783ca376480d19237a3ad4ef789415"
    assert report["source"]["valid_cell_count"] == 4096
    assert report["flow_accounting"]["complete_neighborhood_cell_count"] == 3844
    assert report["flow_accounting"]["boundary_terminal_cell_count"] == 252
    assert report["invariants"]["terminal_contribution_accounting_closed"] is True
    assert report["invariants"]["terminal_area_accounting_closed"] is True
    assert report["output"]["sha256"]
