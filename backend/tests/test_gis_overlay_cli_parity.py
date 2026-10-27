from __future__ import annotations

import shutil
import json
import tempfile
from pathlib import Path

import pytest

from scripts.certify_gis_overlay_cli_parity import DEFAULT_MANIFEST, build_report


@pytest.mark.skipif(shutil.which("geosop") is None, reason="GEOS geosop executable is not available")
def test_overlay_cli_parity_closes_frozen_rectangle_controls() -> None:
    report = build_report(DEFAULT_MANIFEST)

    assert report["status"] == "GIS_OVERLAY_CLI_PARITY_OK"
    assert report["scope"] == "GEOS_COMMAND_LINE_EXECUTABLE_OVERLAY_PARITY"
    assert [row["operation"] for row in report["controls"]] == ["intersection", "union", "symDifference"]
    assert all(row["passed"] for row in report["controls"])
    assert report["invariants"]["control_denominator_closed"] is True
    assert report["invariants"]["area_tolerances_closed"] is True
    assert report["invariants"]["geometry_type_accounting_closed"] is True
    assert report["invariants"]["independent_engine_certification"] == "OPEN"


@pytest.mark.skipif(shutil.which("geosop") is None, reason="GEOS geosop executable is not available")
def test_overlay_cli_parity_rejects_tampered_expected_area() -> None:
    payload = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    control = next(row for row in payload["controls"] if row["id"] == "OVERLAY-CLI-001")
    control["expected"]["areas"]["intersection"] = 2.0
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory) / "tampered-manifest.json"
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        report = build_report(temporary)

    assert report["status"] == "GIS_OVERLAY_CLI_PARITY_FAILED"
    assert report["controls"][0]["passed"] is False
