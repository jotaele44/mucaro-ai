from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .gis_processing import (
    GISProcessingError,
    compare_geojson_geometries,
    inspect_geojson,
    reproject_geojson,
    spatial_join_points_to_polygons,
)
from .utils import utc_now

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GIS_EVAL_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "gis_eval.json"
GIS_EVAL_SCHEMA_VERSION = "mucaro_gis_eval_fixture_v1"
GIS_EVAL_OK = "GIS_EVAL_OK"
GIS_EVAL_FAILED = "GIS_EVAL_FAILED"

VALID_OPERATIONS = {"inspect", "point_in_polygon", "compare", "reproject"}


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
            raise ValueError(f"{case_id}.{name} input must be a GeoJSON object")
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
    lines = [
        str(report.get("status")),
        f"fixture={report.get('fixture')}",
    ]
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
