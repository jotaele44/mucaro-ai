from __future__ import annotations

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


def test_control_manifest_has_no_unbounded_claims() -> None:
    payload, _ = controls()
    assert "vertical CRS certification" in payload["explicit_nonclaims"]
    assert "time-dependent coordinate operation certification" in payload["explicit_nonclaims"]
    assert "unbounded antimeridian, polar, or large-geometry certification" in payload["explicit_nonclaims"]
    assert len(payload["controls"]) == 12
