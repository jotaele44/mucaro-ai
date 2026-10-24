from __future__ import annotations

import shutil

import pytest

from scripts.certify_gis_datum_cli_parity import DEFAULT_MANIFEST, build_report


@pytest.mark.skipif(shutil.which("cct") is None, reason="PROJ cct executable is not available")
def test_datum_cli_parity_closes_both_frozen_regional_controls() -> None:
    report = build_report(DEFAULT_MANIFEST)

    assert report["status"] == "GIS_DATUM_CLI_PARITY_OK"
    assert report["scope"] == "PROJ_COMMAND_LINE_EXECUTABLE_PARITY"
    assert [row["id"] for row in report["controls"]] == ["CRS-004", "CRS-005"]
    assert all(row["passed"] for row in report["controls"])
    assert report["invariants"]["grid_byte_hash_bindings_closed"] is True
    assert report["invariants"]["independent_engine_certification"] == "OPEN"
