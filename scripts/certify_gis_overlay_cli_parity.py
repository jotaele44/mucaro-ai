#!/usr/bin/env python3
"""Run bounded planar overlay controls through the GEOS geosop executable.

This is an independent executable path from Mucaro's Python/Shapely adapter.
It is executable parity evidence, not independent-vendor certification: the
local geosop binary belongs to the GEOS implementation family.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "_architecture" / "gis_v1_control_manifest.json"
STATUS_OK = "GIS_OVERLAY_CLI_PARITY_OK"
STATUS_FAILED = "GIS_OVERLAY_CLI_PARITY_FAILED"
STATUS_UNAVAILABLE = "GIS_OVERLAY_CLI_PARITY_UNAVAILABLE"


def _run(executable: str, args: list[str]) -> str:
    process = subprocess.run(
        [executable, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = process.stdout.strip()
    if process.returncode != 0:
        raise RuntimeError(output or f"geosop returncode={process.returncode}")
    return output


def _result_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise RuntimeError("geosop produced no result")
    return lines[-1]


def _numeric_result(output: str) -> float:
    value = _result_line(output)
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"geosop result is not numeric: {value}") from exc


def _geometry_type(wkt: str) -> str:
    match = re.match(r"^([A-Za-z]+)", wkt.strip())
    return match.group(1).upper() if match else "UNKNOWN"


def _engine_version(executable: str) -> str:
    output = _run(executable, ["-h"])
    return output.splitlines()[0].strip()


def _manifest_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _run_operation(executable: str, control: dict[str, Any], operation: str, expected_area: float, expected_type: str) -> dict[str, Any]:
    input_row = control["input"]
    output_wkt = _result_line(
        _run(
            executable,
            [
                "-a",
                str(input_row["a_wkt"]),
                "-b",
                str(input_row["b_wkt"]),
                "-f",
                "wkt",
                operation,
            ],
        )
    )
    observed_area = _numeric_result(_run(executable, ["-a", output_wkt, "area"]))
    tolerance = 1e-9
    observed_type = _geometry_type(output_wkt)
    area_error = abs(observed_area - expected_area)
    return {
        "control_id": control["id"],
        "operation": operation,
        "expected_area": expected_area,
        "observed_area": observed_area,
        "area_error": area_error,
        "expected_geometry_type": expected_type,
        "observed_geometry_type": observed_type,
        "tolerance": tolerance,
        "output_wkt": output_wkt,
        "passed": area_error <= tolerance and observed_type == expected_type,
    }


def build_report(manifest_path: Path = DEFAULT_MANIFEST, *, geosop: str | None = None) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    controls = {str(row["id"]): row for row in manifest["controls"] if isinstance(row, dict)}
    control = controls["OVERLAY-CLI-001"]
    executable = geosop or shutil.which("geosop")
    if not executable:
        return {
            "schema_version": "mucaro-gis-overlay-cli-parity/1.0",
            "status": STATUS_UNAVAILABLE,
            "scope": "GEOS_COMMAND_LINE_EXECUTABLE_OVERLAY_PARITY",
            "manifest": _manifest_label(manifest_path),
            "controls": [],
            "limitations": [
                "The GEOS geosop executable is unavailable in this runtime.",
                "This control does not certify an independent vendor or engine implementation.",
            ],
        }

    version_output = _engine_version(executable)
    expected_areas = control["expected"]["areas"]
    expected_types = control["expected"]["geometry_types"]
    operations = list(control["input"]["operations"])
    operation_results = [
        _run_operation(executable, control, operation, float(expected_areas[operation]), str(expected_types[operation]))
        for operation in operations
    ]
    return {
        "schema_version": "mucaro-gis-overlay-cli-parity/1.0",
        "status": STATUS_OK if all(row["passed"] for row in operation_results) else STATUS_FAILED,
        "scope": "GEOS_COMMAND_LINE_EXECUTABLE_OVERLAY_PARITY",
        "executable": executable,
        "engine_version": version_output,
        "manifest": {
            "path": _manifest_label(manifest_path),
            "control_ids": [control["id"]],
        },
        "controls": operation_results,
        "invariants": {
            "control_denominator_closed": len(operation_results) == len(operations) == 3,
            "area_tolerances_closed": all(row["area_error"] <= row["tolerance"] for row in operation_results),
            "geometry_type_accounting_closed": all(
                row["observed_geometry_type"] == row["expected_geometry_type"] for row in operation_results
            ),
            "independent_engine_certification": "OPEN",
        },
        "limitations": [
            "This is a separate geosop executable path from Mucaro's Python/Shapely adapter.",
            "The executable is in the GEOS implementation family and therefore does not prove independent-vendor certification.",
            "The fixture is limited to bounded planar rectangle overlay operations in XY space.",
            "Z/M mutation, geodesic overlay, legal-boundary accuracy, and arbitrary topology policy remain outside this control.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded overlay controls through the GEOS geosop executable.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--geosop", help="Explicit geosop executable path.")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()
    report = build_report(args.manifest.expanduser().resolve(), geosop=args.geosop)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["status"])
        print(f"scope={report['scope']}")
        for row in report.get("controls", []):
            print(
                f"{row['operation']} passed={row['passed']} "
                f"area_error={row['area_error']:.12g} "
                f"geometry_type={row['observed_geometry_type']}"
            )
        print("independent_engine_certification=OPEN")
    return 0 if report["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
