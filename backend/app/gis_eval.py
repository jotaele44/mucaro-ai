from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .gis_processing import (
    GISProcessingError,
    compare_geojson_geometries,
    convert_csv_points_to_geojson,
    filter_geojson_by_bbox,
    inspect_geojson,
    measure_geojson,
    reproject_geojson,
    spatial_join_points_to_polygons,
)
from .release_receipts import write_release_receipt
from .utils import utc_now

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GIS_EVAL_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "gis_eval.json"
GIS_EVAL_SCHEMA_VERSION = "mucaro_gis_eval_fixture_v1"
GIS_EVAL_OK = "GIS_EVAL_OK"
GIS_EVAL_FAILED = "GIS_EVAL_FAILED"
GIS_QUALITY_OK = "GIS_QUALITY_CERT_OK"
GIS_QUALITY_FAILED = "GIS_QUALITY_CERT_FAILED"
GIS_QUALITY_PROFILE = "v0.1.23-local-release"
GIS_QUALITY_RECEIPT_TYPE = "gis_quality"

VALID_OPERATIONS = {"inspect", "point_in_polygon", "compare", "reproject", "csv_points", "bbox_filter", "measure"}


def current_git_head(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else "UNKNOWN_HEAD"


def _fixture_label(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _temp_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "gis_eval.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def _write_geojson(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _write_csv(path: Path, payload: dict[str, Any]) -> Path:
    headers = payload.get("header")
    rows = payload.get("rows")
    if not isinstance(headers, list) or not headers:
        raise ValueError(f"{path.name} CSV input must include a non-empty header array")
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} CSV input must include a rows array")
    fieldnames = [str(header) for header in headers]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{path.name} CSV row must be an object")
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return path


def _lookup_path(payload: Any, dotted_path: str) -> Any:
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
            continue
        return None
    return current


def _check_equal(name: str, observed: Any, expected: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": "PASS" if observed == expected else "FAIL",
        "observed": observed,
        "expected": expected,
    }


def _validate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    schema_version = fixture.get("schema_version")
    checks.append(
        _check_equal(
            "schema_version",
            schema_version,
            GIS_EVAL_SCHEMA_VERSION,
        )
    )

    cases = fixture.get("cases")
    if not isinstance(cases, list):
        checks.append(_check_equal("cases_array", type(cases).__name__, "list"))
        return {"status": "FAIL", "checks": checks}

    case_ids = [str(case.get("case_id") or "") for case in cases if isinstance(case, dict)]
    operations = [str(case.get("operation") or "") for case in cases if isinstance(case, dict)]
    negative_cases = [case for case in cases if isinstance(case, dict) and case.get("expect_error") is True]
    minimums = fixture.get("minimums", {})
    if not isinstance(minimums, dict):
        minimums = {}

    checks.extend(
        [
            _check_equal("case_ids_unique", len(case_ids), len(set(case_ids))),
            _check_equal("case_count_minimum", len(cases) >= int(minimums.get("case_count", 0)), True),
            _check_equal("operation_count_minimum", len(set(operations)) >= int(minimums.get("operation_count", 0)), True),
            _check_equal("negative_case_count_minimum", len(negative_cases) >= int(minimums.get("negative_case_count", 0)), True),
            _check_equal("operations_supported", sorted(set(operations) - VALID_OPERATIONS), []),
        ]
    )

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            checks.append(_check_equal(f"case_{index}_shape", type(case).__name__, "dict"))
            continue
        checks.append(_check_equal(f"{case.get('case_id', f'case_{index}')}.case_id_present", bool(case.get("case_id")), True))
        checks.append(_check_equal(f"{case.get('case_id', f'case_{index}')}.expected_status_present", bool(case.get("expected_status")), True))

    return {"status": "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL", "checks": checks}


def _materialize_inputs(case: dict[str, Any], input_root: Path) -> dict[str, Path]:
    case_id = str(case["case_id"])
    inputs = case.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError(f"{case_id} inputs must be an object")
    materialized: dict[str, Path] = {}
    for name, payload in inputs.items():
        if not isinstance(payload, dict):
            raise ValueError(f"{case_id}.{name} input must be an object")
        if payload.get("format") == "csv":
            materialized[name] = _write_csv(input_root / f"{case_id}_{name}.csv", payload)
        else:
            materialized[name] = _write_geojson(input_root / f"{case_id}_{name}.geojson", payload)
    return materialized


def _run_operation(case: dict[str, Any], paths: dict[str, Path], cfg: Settings) -> dict[str, Any]:
    operation = str(case.get("operation") or "")
    parameters = case.get("parameters", {})
    if not isinstance(parameters, dict):
        parameters = {}

    if operation == "inspect":
        return inspect_geojson(paths["geojson"], cfg=cfg)
    if operation == "point_in_polygon":
        return spatial_join_points_to_polygons(
            paths["points"],
            paths["polygons"],
            cfg=cfg,
            require_matching_crs=not bool(parameters.get("allow_crs_mismatch", False)),
        )
    if operation == "compare":
        return compare_geojson_geometries(
            paths["left"],
            paths["right"],
            cfg=cfg,
            match_on=str(parameters.get("match_on") or "id"),
        )
    if operation == "reproject":
        return reproject_geojson(
            paths["geojson"],
            cfg=cfg,
            target_crs=str(parameters.get("target_crs") or ""),
            source_crs=parameters.get("source_crs"),
        )
    if operation == "csv_points":
        return convert_csv_points_to_geojson(
            paths["csv"],
            latitude_column=str(parameters.get("latitude_column") or ""),
            longitude_column=str(parameters.get("longitude_column") or ""),
            cfg=cfg,
            id_column=parameters.get("id_column"),
            crs=str(parameters.get("crs") or "EPSG:4326"),
            delimiter=str(parameters.get("delimiter") or ","),
            skip_invalid_rows=bool(parameters.get("skip_invalid_rows", False)),
        )
    if operation == "bbox_filter":
        bbox = parameters.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError("bbox_filter parameters.bbox must be a four-number array")
        return filter_geojson_by_bbox(
            paths["geojson"],
            min_x=bbox[0],
            min_y=bbox[1],
            max_x=bbox[2],
            max_y=bbox[3],
            cfg=cfg,
            mode=str(parameters.get("mode") or "intersects"),
        )
    if operation == "measure":
        coordinate_units = parameters.get("coordinate_units", "source_crs_units")
        measurement_mode = parameters.get("measurement_mode", "planar")
        return measure_geojson(
            paths["geojson"],
            cfg=cfg,
            coordinate_units=str(coordinate_units),
            measurement_mode=str(measurement_mode),
        )
    raise ValueError(f"Unsupported GIS eval operation: {operation}")


def _evaluate_case(case: dict[str, Any], *, input_root: Path, cfg: Settings) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "unnamed_case")
    operation = str(case.get("operation") or "unknown")
    expected_status = str(case.get("expected_status") or "")
    expected_warnings = sorted(str(warning) for warning in case.get("expected_warnings", []))
    checks: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    error_detail = None

    try:
        paths = _materialize_inputs(case, input_root)
        observed = _run_operation(case, paths, cfg)
        observed_status = str(observed.get("status"))
        observed_warnings = sorted(str(warning) for warning in observed.get("warnings", []))
    except GISProcessingError as exc:
        observed_status = exc.status
        observed_warnings = []
        error_detail = exc.detail
    except Exception as exc:
        observed_status = "ERROR_GIS_EVAL_CASE_RUNTIME"
        observed_warnings = []
        error_detail = str(exc)

    checks.append(_check_equal("status", observed_status, expected_status))
    checks.append(_check_equal("warnings", observed_warnings, expected_warnings))

    expected_invariants = case.get("expected_invariants", {})
    if isinstance(expected_invariants, dict) and observed:
        invariants = observed.get("invariants", {})
        for key, expected in expected_invariants.items():
            checks.append(_check_equal(f"invariants.{key}", _lookup_path(invariants, str(key)), expected))
    elif expected_invariants:
        checks.append(_check_equal("invariants_available", False, True))

    expected_fields = case.get("expected_fields", {})
    if isinstance(expected_fields, dict) and observed:
        for dotted_path, expected in expected_fields.items():
            checks.append(_check_equal(str(dotted_path), _lookup_path(observed, str(dotted_path)), expected))
    elif expected_fields:
        checks.append(_check_equal("fields_available", False, True))

    expected_output = case.get("expected_output", {})
    if isinstance(expected_output, dict) and observed:
        output = observed.get("output", {})
        for dotted_path, expected in expected_output.items():
            checks.append(_check_equal(f"output.{dotted_path}", _lookup_path(output, str(dotted_path)), expected))
    elif expected_output:
        checks.append(_check_equal("output_available", False, True))

    return {
        "case_id": case_id,
        "operation": operation,
        "case_type": "negative" if case.get("expect_error") is True else "positive",
        "status": "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL",
        "observed_status": observed_status,
        "expected_status": expected_status,
        "observed_warnings": observed_warnings,
        "expected_warnings": expected_warnings,
        "error_detail": error_detail,
        "checks": checks,
    }


def load_gis_eval_fixture(fixture_path: Path = DEFAULT_GIS_EVAL_FIXTURE) -> dict[str, Any]:
    with fixture_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("GIS eval fixture must be a JSON object")
    return payload


def build_gis_eval_report(*, fixture_path: Path = DEFAULT_GIS_EVAL_FIXTURE) -> dict[str, Any]:
    fixture = load_gis_eval_fixture(fixture_path)
    validation = _validate_fixture(fixture)
    cases = fixture.get("cases", []) if isinstance(fixture.get("cases"), list) else []
    case_results: list[dict[str, Any]] = []

    if validation["status"] == "PASS":
        with tempfile.TemporaryDirectory(prefix="mucaro-gis-eval-") as temp_dir:
            root = Path(temp_dir)
            cfg = _temp_settings(root)
            cfg.ensure_dirs()
            input_root = cfg.input_dir / "gis_eval"
            for case in cases:
                if isinstance(case, dict):
                    case_results.append(_evaluate_case(case, input_root=input_root, cfg=cfg))

    failed_cases = [case for case in case_results if case["status"] != "PASS"]
    operations = sorted({str(case.get("operation") or "") for case in cases if isinstance(case, dict)})
    negative_case_count = sum(1 for case in cases if isinstance(case, dict) and case.get("expect_error") is True)
    status = GIS_EVAL_OK if validation["status"] == "PASS" and not failed_cases and len(case_results) == len(cases) else GIS_EVAL_FAILED
    return {
        "schema_version": GIS_EVAL_SCHEMA_VERSION,
        "status": status,
        "fixture": _fixture_label(fixture_path),
        "generated_at": utc_now(),
        "fixture_validation": validation,
        "counts": {
            "cases": len(cases),
            "passed": sum(1 for case in case_results if case["status"] == "PASS"),
            "failed": len(failed_cases),
            "positive_cases": len(cases) - negative_case_count,
            "negative_cases": negative_case_count,
            "operations": len(set(operations)),
            "operation_names": operations,
        },
        "cases": case_results,
    }


def format_gis_eval_text(report: dict[str, Any]) -> list[str]:
    counts = report.get("counts", {})
    lines = [f"fixture={report.get('fixture')}"]
    if isinstance(counts, dict):
        lines.extend(
            [
                f"case_count={counts.get('cases')}",
                f"passed={counts.get('passed')}",
                f"failed={counts.get('failed')}",
                f"positive_cases={counts.get('positive_cases')}",
                f"negative_cases={counts.get('negative_cases')}",
                f"operation_count={counts.get('operations')}",
                f"operations={','.join(str(name) for name in counts.get('operation_names', []))}",
            ]
        )
    validation = report.get("fixture_validation", {})
    if isinstance(validation, dict):
        lines.append(f"fixture_validation={validation.get('status')}")
        for check in validation.get("checks", []):
            if isinstance(check, dict):
                lines.append(f"{check.get('status')} fixture.{check.get('name')}")
    for case in report.get("cases", []):
        if isinstance(case, dict):
            lines.append(
                f"{case.get('status')} {case.get('operation')} {case.get('case_id')} "
                f"observed={case.get('observed_status')} expected={case.get('expected_status')}"
            )
            for check in case.get("checks", []):
                if isinstance(check, dict) and check.get("status") != "PASS":
                    lines.append(
                        f"FAIL {case.get('case_id')}.{check.get('name')} "
                        f"observed={check.get('observed')} expected={check.get('expected')}"
                    )
    return [*lines, ""]


def build_gis_quality_certification(
    root: Path,
    *,
    profile: str = GIS_QUALITY_PROFILE,
    fixture_path: Path = DEFAULT_GIS_EVAL_FIXTURE,
    command: list[str] | None = None,
    write_receipt: bool = True,
) -> dict[str, Any]:
    head_sha = current_git_head(root)
    report = build_gis_eval_report(fixture_path=fixture_path)
    status = GIS_QUALITY_OK if report["status"] == GIS_EVAL_OK else GIS_QUALITY_FAILED
    result: dict[str, Any] = {
        "status": status,
        "profile": profile,
        "head_sha": head_sha,
        "fixture": report.get("fixture"),
        "report": report,
        "failed_cases": [case for case in report.get("cases", []) if isinstance(case, dict) and case.get("status") != "PASS"],
        "receipt": None,
    }
    if write_receipt:
        try:
            receipt = write_release_receipt(
                root,
                receipt_type=GIS_QUALITY_RECEIPT_TYPE,
                status=status,
                head_sha=head_sha,
                command=command or [sys.executable, "scripts/certify_gis_quality.py"],
                payload={
                    "profile": profile,
                    "fixture": report.get("fixture"),
                    "report": report,
                },
            )
            result["receipt"] = str(receipt.relative_to(root) if receipt.is_relative_to(root) else receipt)
        except OSError as exc:
            result["status"] = GIS_QUALITY_FAILED
            result["receipt_error"] = f"{type(exc).__name__}: {exc}"
    return result


def format_gis_quality_certification_text(report: dict[str, Any]) -> list[str]:
    lines = [
        f"profile={report.get('profile')}",
        f"head_sha={report.get('head_sha')}",
        f"fixture={report.get('fixture')}",
    ]
    if report.get("receipt"):
        lines.append(f"receipt={report.get('receipt')}")
    eval_report = report.get("report", {})
    if isinstance(eval_report, dict):
        for line in format_gis_eval_text(eval_report)[:-1]:
            if line.startswith(("PASS ", "FAIL ")):
                lines.append(line)
            elif "=" in line:
                lines.append(f"eval_{line}")
            else:
                lines.append(line)
    for case in report.get("failed_cases", []):
        if isinstance(case, dict):
            lines.append(f"FAILED_CASE {case.get('case_id')} observed={case.get('observed_status')}")
    if report.get("receipt_error"):
        lines.append(f"receipt_error={report.get('receipt_error')}")
    return [*lines, ""]
