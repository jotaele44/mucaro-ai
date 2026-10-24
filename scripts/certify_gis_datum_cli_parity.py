#!/usr/bin/env python3
"""Run frozen datum-grid controls through the PROJ command-line executable.

This is an independent executable control path from PyProj, not a claim that
PROJ CLI is an independent vendor or implementation of the PROJ engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "_architecture" / "gis_v1_control_manifest.json"
STATUS_OK = "GIS_DATUM_CLI_PARITY_OK"
STATUS_FAILED = "GIS_DATUM_CLI_PARITY_FAILED"
STATUS_UNAVAILABLE = "GIS_DATUM_CLI_PARITY_UNAVAILABLE"


def _grid_path(row: dict[str, Any]) -> Path:
    path = ROOT / str(row["input"]["grid"])
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"grid is not a regular file: {path}")
    return path


def _pipeline_args(template: str, grid_path: Path) -> list[str]:
    rendered = template.format(grid=str(grid_path))
    return [f"+{token}" for token in shlex.split(rendered)]


def _run_cct(cct: str, pipeline: str, grid_path: Path, lon: float, lat: float) -> tuple[float, float, str]:
    proc = subprocess.run(
        [cct, "-d", "12", "-z", "0", "-t", "0", *_pipeline_args(pipeline, grid_path)],
        input=f"{lon} {lat}\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout.strip() or f"cct returncode={proc.returncode}")
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise RuntimeError(f"cct produced no coordinate output: {proc.stdout.strip()}")
    values = lines[-1].split()
    if len(values) < 2:
        raise RuntimeError(f"cct coordinate output is incomplete: {lines[-1]}")
    try:
        return float(values[0]), float(values[1]), proc.stdout
    except ValueError as exc:
        raise RuntimeError(f"cct coordinate output is not numeric: {lines[-1]}") from exc


def _run_control(cct: str, row: dict[str, Any]) -> dict[str, Any]:
    grid = _grid_path(row)
    expected = row["expected"]
    input_row = row["input"]
    grid_bytes = grid.stat().st_size
    grid_sha256 = hashlib.sha256(grid.read_bytes()).hexdigest()
    pipeline_template = (
        "proj=pipeline step proj=unitconvert xy_in=deg xy_out=rad "
        + ("step inv proj=hgridshift grids={grid} " if row["id"] == "CRS-004" else "step proj=hgridshift grids={grid} ")
        + ("step proj=lcc lat_0=17.8333333333333 lon_0=-66.4333333333333 "
           "lat_1=18.4333333333333 lat_2=18.0333333333333 x_0=200000 y_0=200000 ellps=GRS80"
           if row["id"] == "CRS-004"
           else "step proj=unitconvert xy_in=rad xy_out=deg")
    )
    x, y, raw_output = _run_cct(
        cct,
        pipeline_template,
        grid,
        float(input_row["lon"]),
        float(input_row["lat"]),
    )
    expected_x = float(expected.get("x", expected.get("lon")))
    expected_y = float(expected.get("y", expected.get("lat")))
    tolerance = 1e-6
    return {
        "id": row["id"],
        "grid": str(grid.relative_to(ROOT)),
        "grid_bytes": grid_bytes,
        "grid_sha256": grid_sha256,
        "expected_grid_bytes": expected["grid_bytes"],
        "expected_grid_sha256": expected["grid_sha256"],
        "observed": {"x": x, "y": y},
        "expected": {"x": expected_x, "y": expected_y},
        "absolute_error": {"x": abs(x - expected_x), "y": abs(y - expected_y)},
        "tolerance": tolerance,
        "raw_output": raw_output.strip(),
        "passed": (
            grid_bytes == expected["grid_bytes"]
            and grid_sha256 == expected["grid_sha256"]
            and abs(x - expected_x) <= tolerance
            and abs(y - expected_y) <= tolerance
        ),
        "scope": "PROJ_COMMAND_LINE_EXECUTABLE_PARITY",
        "independent_engine_certification": "OPEN",
    }


def build_report(manifest_path: Path = DEFAULT_MANIFEST, *, cct: str | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = {str(row["id"]): row for row in manifest["controls"] if isinstance(row, dict)}
    control_rows = [rows["CRS-004"], rows["CRS-005"]]
    executable = cct or shutil.which("cct")
    if not executable:
        return {
            "schema_version": "mucaro-gis-datum-cli-parity/1.0",
            "status": STATUS_UNAVAILABLE,
            "scope": "PROJ_COMMAND_LINE_EXECUTABLE_PARITY",
            "manifest": str(manifest_path.relative_to(ROOT)),
            "controls": [],
            "limitations": [
                "The PROJ cct executable is unavailable in this runtime.",
                "This control does not certify an independent vendor or engine implementation.",
            ],
        }
    controls = [_run_control(executable, row) for row in control_rows]
    return {
        "schema_version": "mucaro-gis-datum-cli-parity/1.0",
        "status": STATUS_OK if all(row["passed"] for row in controls) else STATUS_FAILED,
        "scope": "PROJ_COMMAND_LINE_EXECUTABLE_PARITY",
        "executable": executable,
        "manifest": {
            "path": str(manifest_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "control_ids": [row["id"] for row in control_rows],
        },
        "controls": controls,
        "invariants": {
            "control_denominator_closed": len(controls) == 2 and {row["id"] for row in controls} == {"CRS-004", "CRS-005"},
            "grid_byte_hash_bindings_closed": all(
                row["grid_bytes"] == row["expected_grid_bytes"] and row["grid_sha256"] == row["expected_grid_sha256"]
                for row in controls
            ),
            "coordinate_tolerances_closed": all(row["passed"] for row in controls),
            "independent_engine_certification": "OPEN",
        },
        "limitations": [
            "This is a separate PROJ cct executable path from the PyProj adapter, but it uses the same underlying PROJ engine family.",
            "The control proves frozen pipeline/file parity only; it does not prove independent-vendor or universal datum accuracy.",
            "Vertical and time-dependent CRS transformations remain outside this bounded scope.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run frozen datum-grid controls through the PROJ cct executable.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--cct", help="Explicit cct executable path.")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()
    report = build_report(args.manifest.expanduser().resolve(), cct=args.cct)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["status"])
        print(f"scope={report['scope']}")
        for row in report.get("controls", []):
            print(f"{row['id']} passed={row['passed']} error_x={row['absolute_error']['x']:.12g} error_y={row['absolute_error']['y']:.12g}")
        print("independent_engine_certification=OPEN")
    return 0 if report["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
