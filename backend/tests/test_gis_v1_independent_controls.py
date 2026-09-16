from __future__ import annotations

import json
import math
from pathlib import Path

from pyproj import Geod, Transformer
from shapely.geometry import Polygon
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "_architecture" / "gis_v1_control_manifest.json"


def controls():
    payload = json.loads(MANIFEST.read_text(encoding="utf-8")); return payload, {row["id"]: row for row in payload["controls"]}


def test_crs_controls_close_against_frozen_closed_form_values() -> None:
    payload, rows = controls(); tol = payload["tolerances"]["web_mercator_m"]
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    for key in ("CRS-001", "CRS-002"):
        row = rows[key]; x, y = transformer.transform(row["input"]["lon"], row["input"]["lat"])
        assert abs(x-row["expected"]["x"]) <= tol; assert abs(y-row["expected"]["y"]) <= tol


def test_geodesic_control_closes_against_frozen_geographiclib_value() -> None:
    payload, rows = controls(); row = rows["GEOD-001"]; a=row["input"]["a"]; b=row["input"]["b"]
    _, _, distance = Geod(ellps="WGS84").inv(a[0],a[1],b[0],b[1])
    assert abs(distance-row["expected"]["distance_m"]) <= payload["tolerances"]["geodesic_distance_m"]


def test_topology_repair_control_is_explicit_and_valid() -> None:
    _, rows=controls(); bowtie=Polygon([(0,0),(2,2),(0,2),(2,0),(0,0)]); assert bowtie.is_valid is rows["TOPO-001"]["expected"]["initial_valid"]; repaired=make_valid(bowtie); assert repaired.is_valid is rows["TOPO-001"]["expected"]["repair_valid"]


def test_overlay_control_closes_analytic_arithmetic() -> None:
    payload, rows=controls(); row=rows["OVERLAY-001"]; a=Polygon([(0,0),(2,0),(2,2),(0,2)]); b=Polygon([(1,1),(3,1),(3,3),(1,3)]); tol=payload["tolerances"]["area_units2"]
    assert math.isclose(a.intersection(b).area,row["expected"]["intersection_area"],abs_tol=tol); assert math.isclose(a.union(b).area,row["expected"]["union_area"],abs_tol=tol); assert math.isclose(a.symmetric_difference(b).area,row["expected"]["symmetric_difference_area"],abs_tol=tol)


def test_control_manifest_has_no_unbounded_claims() -> None:
    payload,_=controls(); assert "vertical CRS certification" in payload["explicit_nonclaims"]; assert "time-dependent coordinate operation certification" in payload["explicit_nonclaims"]; assert len(payload["controls"]) == 7
