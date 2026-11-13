from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from geographiclib.geodesic import Geodesic
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "gis_v1_control_manifest.json"


def controls():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return payload, {row["id"]: row for row in payload["controls"]}


def test_crs_controls_close_against_frozen_closed_form_values() -> None:
    payload, rows = controls()
    tolerance = payload["tolerances"]["web_mercator_m"]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    for key in ("CRS-001", "CRS-002"):
        row = rows[key]
        x, y = transformer.transform(row["input"]["lon"], row["input"]["lat"])
        assert abs(x - row["expected"]["x"]) <= tolerance
        assert abs(y - row["expected"]["y"]) <= tolerance


def test_crs_axis_order_control_closes_in_both_declared_conventions() -> None:
    payload, rows = controls()
    row = rows["CRS-003"]
    source = row["input"]["source"]
    target = row["input"]["target"]
    traditional_xy = Transformer.from_crs(source, target, always_xy=True)
    authority_axis = Transformer.from_crs(source, target, always_xy=False)
    lon, lat = row["input"]["lon"], row["input"]["lat"]
    expected = row["expected"]
    tolerance = payload["tolerances"]["utm_m"]
    xy_result = traditional_xy.transform(lon, lat)
    authority_result = authority_axis.transform(lat, lon)
    swapped_result = traditional_xy.transform(lat, lon)
    for result in (xy_result, authority_result):
        assert abs(result[0] - expected["x"]) <= tolerance
        assert abs(result[1] - expected["y"]) <= tolerance
    assert abs(swapped_result[0] - expected["x"]) > 1_000_000
    assert not (
        100_000 <= swapped_result[0] <= 900_000
        and 0 <= swapped_result[1] <= 10_000_000
    )


def test_governed_datum_grid_control_closes_direct_proj_parity() -> None:
    payload, rows = controls()
    row = rows["CRS-004"]
    grid_path = ROOT / row["input"]["grid"]
    grid_bytes = grid_path.read_bytes()
    expected = row["expected"]
    pipeline = (
        "proj=pipeline step proj=unitconvert xy_in=deg xy_out=rad "
        f"step inv proj=hgridshift grids={json.dumps(grid_path.as_posix())} "
        "step proj=lcc lat_0=17.8333333333333 lon_0=-66.4333333333333 "
        "lat_1=18.4333333333333 lat_2=18.0333333333333 "
        "x_0=200000 y_0=200000 ellps=GRS80"
    )
    transformer = Transformer.from_pipeline(pipeline)
    x, y = transformer.transform(row["input"]["lon"], row["input"]["lat"], errcheck=True)
    tolerance = payload["tolerances"]["datum_grid_m"]

    assert len(grid_bytes) == expected["grid_bytes"]
    assert hashlib.sha256(grid_bytes).hexdigest() == expected["grid_sha256"]
    assert abs(x - expected["x"]) <= tolerance
    assert abs(y - expected["y"]) <= tolerance
    assert abs(x - expected["ballpark_x"]) > 0.25
    assert abs(y - expected["ballpark_y"]) > 0.9


def test_florida_datum_grid_control_closes_direct_proj_parity() -> None:
    payload, rows = controls()
    row = rows["CRS-005"]
    grid_path = ROOT / row["input"]["grid"]
    grid_bytes = grid_path.read_bytes()
    expected = row["expected"]
    pipeline = (
        "proj=pipeline step proj=unitconvert xy_in=deg xy_out=rad "
        f"step proj=hgridshift grids={json.dumps(grid_path.as_posix())} "
        "step proj=unitconvert xy_in=rad xy_out=deg"
    )
    transformer = Transformer.from_pipeline(pipeline)
    lon, lat = transformer.transform(row["input"]["lon"], row["input"]["lat"], errcheck=True)

    assert len(grid_bytes) == expected["grid_bytes"]
    assert hashlib.sha256(grid_bytes).hexdigest() == expected["grid_sha256"]
    assert lon == expected["lon"]
    assert lat == expected["lat"]


def test_geodesic_control_closes_against_independent_geographiclib_engine() -> None:
    payload, rows = controls()
    for key in ("GEOD-001", "GEOD-002", "GEOD-003"):
        row = rows[key]
        a = row["input"]["a"]
        b = row["input"]["b"]
        result = Geodesic.WGS84.Inverse(a[1], a[0], b[1], b[0])
        distance = result["s12"]
        assert (
            abs(distance - row["expected"]["distance_m"])
            <= payload["tolerances"]["geodesic_distance_m"]
        )


def test_geodesic_polygon_edge_controls_close_against_geographiclib() -> None:
    payload, rows = controls()
    for key in ("GEOD-004", "GEOD-005"):
        row = rows[key]
        polygon = Geodesic.WGS84.Polygon()
        for lon, lat in row["input"]["points"]:
            polygon.AddPoint(lat, lon)
        _, perimeter, signed_area = polygon.Compute(False, True)
        assert (
            abs(perimeter - row["expected"]["perimeter_m"])
            <= payload["tolerances"]["geodesic_distance_m"]
        )
        assert (
            abs(abs(signed_area) - row["expected"]["area_m2"])
            <= payload["tolerances"]["geodesic_area_m2"]
        )


def test_topology_repair_control_is_explicit_and_valid() -> None:
    _, rows = controls()
    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    assert bowtie.is_valid is rows["TOPO-001"]["expected"]["initial_valid"]
    repaired = make_valid(bowtie)
    assert repaired.is_valid is rows["TOPO-001"]["expected"]["repair_valid"]


def test_overlay_control_closes_analytic_arithmetic() -> None:
    payload, rows = controls()
    row = rows["OVERLAY-001"]
    a = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
    b = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
    tolerance = payload["tolerances"]["area_units2"]
    assert math.isclose(a.intersection(b).area, row["expected"]["intersection_area"], abs_tol=tolerance)
    assert math.isclose(a.union(b).area, row["expected"]["union_area"], abs_tol=tolerance)
    assert math.isclose(
        a.symmetric_difference(b).area,
        row["expected"]["symmetric_difference_area"],
        abs_tol=tolerance,
    )


def test_d8_monotonic_plane_control_closes_without_mucaro_engine() -> None:
    _, rows = controls()
    row = rows["HYDRO-001"]
    grid = row["input"]["grid"]
    height = len(grid)
    width = len(grid[0])
    accumulation = {(r, c): 1.0 for r in range(height) for c in range(width)}
    downstream = {}
    for r in range(1, height - 1):
        for c in range(1, width - 1):
            gradients = {
                "E": grid[r][c] - grid[r][c + 1],
                "W": grid[r][c] - grid[r][c - 1],
                "N": grid[r][c] - grid[r - 1][c],
                "S": grid[r][c] - grid[r + 1][c],
            }
            maximum = max(gradients.values())
            winners = [name for name, value in gradients.items() if value == maximum and value > 0]
            assert winners == ["E"]
            downstream[(r, c)] = (r, c + 1)
    for source in sorted(downstream, key=lambda cell: grid[cell[0]][cell[1]], reverse=True):
        accumulation[downstream[source]] += accumulation[source]
    terminal_cells = [cell for cell in accumulation if cell not in downstream]
    expected = row["expected"]
    assert len(downstream) == expected["flow_edge_count"] == expected["east_flow_count"]
    assert (height - 2) * (width - 2) == expected["complete_neighborhood_count"]
    assert height * width - len(downstream) == expected["boundary_terminal_count"]
    assert sum(accumulation[cell] for cell in terminal_cells) == expected["terminal_accumulation_total"]
    assert max(accumulation.values()) == expected["maximum_accumulation"]


def test_d8_tie_control_preserves_all_top_candidates_without_routing() -> None:
    _, rows = controls()
    row = rows["HYDRO-002"]
    grid = row["input"]["grid"]
    center = grid[1][1]
    candidates = {
        "E": (1, center - grid[1][2]),
        "S": (4, center - grid[2][1]),
        "W": (16, center - grid[1][0]),
        "N": (64, center - grid[0][1]),
    }
    maximum = max(drop for _, drop in candidates.values())
    winners = [name for name, (_, drop) in candidates.items() if drop == maximum and drop > 0]
    bitmask = sum(candidates[name][0] for name in winners)
    expected = row["expected"]
    assert winners == expected["top_candidate_directions"]
    assert bitmask == expected["candidate_bitmask"]
    assert expected["tie_unresolved_count"] == 1
    assert expected["flow_edge_count"] == 0
    assert expected["terminal_accumulation_total"] == 9.0


def test_depression_bowl_control_closes_volume_without_mucaro_engine() -> None:
    _, rows = controls()
    row = rows["HYDRO-003"]
    grid = row["input"]["grid"]
    spill_elevation = row["expected"]["spill_elevation_m"]
    boundary = [
        value
        for r, values in enumerate(grid)
        for c, value in enumerate(values)
        if r in {0, len(grid) - 1} or c in {0, len(values) - 1}
    ]
    interior = [value for values in grid[1:-1] for value in values[1:-1]]
    depths = [max(spill_elevation - value, 0.0) for value in interior]
    expected = row["expected"]

    assert min(boundary) == spill_elevation
    assert len(boundary) == expected["outlet_seed_count"]
    assert sum(depth > 0.0 for depth in depths) == expected["raised_cell_count"]
    assert len(boundary) == expected["unchanged_cell_count"]
    assert max(depths) == expected["maximum_fill_depth_m"]
    assert sum(depths) == expected["total_fill_depth_m"]
    assert sum(depths) * row["input"]["cell_size_m"] ** 2 == expected["fill_volume_cubic_metres"]


def test_depression_nodata_adjacency_control_identifies_outlets_without_mucaro_engine() -> None:
    _, rows = controls()
    row = rows["HYDRO-004"]
    grid = row["input"]["grid"]
    nodata_row, nodata_column = row["expected"]["nodata_coordinate"]
    adjacent = [
        (nodata_row + row_delta, nodata_column + column_delta)
        for row_delta in (-1, 0, 1)
        for column_delta in (-1, 0, 1)
        if (row_delta, column_delta) != (0, 0)
        and grid[nodata_row + row_delta][nodata_column + column_delta] is not None
    ]
    representative_row, representative_column = row["expected"]["representative_outlet_coordinate"]

    assert len(adjacent) == row["expected"]["nodata_adjacent_outlet_count"]
    assert (representative_row, representative_column) in adjacent
    assert grid[representative_row][representative_column] == row["expected"]["representative_outlet_elevation_m"]


def test_control_manifest_has_no_unbounded_claims() -> None:
    payload, _ = controls()
    assert "vertical CRS certification" in payload["explicit_nonclaims"]
    assert "time-dependent coordinate operation certification" in payload["explicit_nonclaims"]
    assert "independent-engine certification of the Puerto Rico datum-grid transformation" in payload["explicit_nonclaims"]
    assert "independent-engine certification of governed regional datum-grid transformations" in payload["explicit_nonclaims"]
    assert "unbounded antimeridian, polar, or large-geometry certification" in payload["explicit_nonclaims"]
    assert "unbounded depression conditioning, depression breaching, flat routing, watershed delineation, stream extraction, runoff, flood, groundwater, or hydraulic modeling" in payload["explicit_nonclaims"]
    assert len(payload["controls"]) == 18
