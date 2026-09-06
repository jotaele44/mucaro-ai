from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .citations import validate_citations
from .config import Settings
from .ingestion import ingest_file
from .release_receipts import write_release_receipt
from .services import answer_question
from .utils import read_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWER_TRACE_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "answer_trace_eval.json"
ANSWER_TRACE_EVAL_OK = "ANSWER_TRACE_EVAL_OK"
ANSWER_TRACE_EVAL_FAILED = "ANSWER_TRACE_EVAL_FAILED"
ANSWER_TRACE_QUALITY_OK = "ANSWER_TRACE_QUALITY_CERT_OK"
ANSWER_TRACE_QUALITY_FAILED = "ANSWER_TRACE_QUALITY_CERT_FAILED"
ANSWER_TRACE_QUALITY_PROFILE = "v0.1.23-local-release"
ANSWER_TRACE_QUALITY_RECEIPT_TYPE = "answer_trace_quality"


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
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def _trace_shape_flags(trace: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    retrieved_ids = [str(chunk_id) for chunk_id in trace.get("retrieved_chunk_ids", [])]
    cited_ids = [str(chunk_id) for chunk_id in trace.get("cited_chunk_ids", [])]
    cited_records = trace.get("cited_chunks", [])
    if len(cited_ids) != len(set(cited_ids)):
        flags.append("ERROR_DUPLICATE_CITED_CHUNK_ID")
    if cited_ids and not isinstance(cited_records, list):
        return ["ERROR_CITATION_TRACE_INVALID"]
    if not isinstance(cited_records, list):
        return flags

    seen_record_ids: set[str] = set()
    for expected_order, record in enumerate(cited_records, start=1):
        if not isinstance(record, dict):
            flags.append("ERROR_CITATION_TRACE_INVALID")
            continue
        chunk_id = str(record.get("chunk_id") or "")
        if not chunk_id:
            flags.append("ERROR_CITATION_CHUNK_ID_MISSING")
        elif chunk_id not in cited_ids:
            flags.append("ERROR_TRACE_RECORD_NOT_CITED")
        elif chunk_id in seen_record_ids:
            flags.append("ERROR_DUPLICATE_CITATION_TRACE_RECORD")
        seen_record_ids.add(chunk_id)

        if record.get("citation_order") != expected_order:
            flags.append("ERROR_CITATION_ORDER_INVALID")
        rank = record.get("retrieval_rank")
        if not isinstance(rank, int) or rank < 1 or rank > max(1, len(retrieved_ids)):
            flags.append("ERROR_CITATION_RETRIEVAL_RANK_INVALID")
        if not str(record.get("source_id") or "").strip():
            flags.append("ERROR_CITATION_SOURCE_ID_MISSING")
        if record.get("chunk_index") is None:
            flags.append("ERROR_CITATION_CHUNK_INDEX_MISSING")
        if not str(record.get("text_snippet") or "").strip():
            flags.append("ERROR_CITATION_SNIPPET_MISSING")
    return sorted(set(flags))


def validate_answer_trace_quality(trace: dict[str, Any]) -> dict[str, Any]:
    validated = validate_citations(copy.deepcopy(trace))
    flags = set(validated.get("validation_flags", []))
    flags.update(_trace_shape_flags(validated))
    validated["validation_flags"] = sorted(flags)
    validated["citation_status"] = "CITATION_OK" if not any(flag.startswith("ERROR_") for flag in flags) else "CITATION_ERROR"
    return validated


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "unnamed_case")
    trace = case.get("trace")
    expected_status = str(case.get("expected_status") or "CITATION_OK")
    expected_flags = sorted(str(flag) for flag in case.get("expected_flags", []))
    if not isinstance(trace, dict):
        return {
            "case_id": case_id,
            "status": "FAIL",
            "observed_status": "TRACE_INVALID",
            "observed_flags": ["ERROR_TRACE_NOT_OBJECT"],
            "expected_status": expected_status,
            "expected_flags": expected_flags,
        }

    validated = validate_answer_trace_quality(trace)
    observed_status = str(validated.get("citation_status"))
    observed_flags = sorted(str(flag) for flag in validated.get("validation_flags", []))
    expected_flag_set = set(expected_flags)
    observed_flag_set = set(observed_flags)
    status_matches = observed_status == expected_status
    flags_match = expected_flag_set.issubset(observed_flag_set)
    case_status = "PASS" if status_matches and flags_match else "FAIL"
    return {
        "case_type": "static",
        "case_id": case_id,
        "status": case_status,
        "observed_status": observed_status,
        "observed_flags": observed_flags,
        "expected_status": expected_status,
        "expected_flags": expected_flags,
    }


def _temp_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "answer_trace_eval.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def _write_generated_source(input_dir: Path, file_spec: dict[str, Any]) -> Path:
    relative_path = Path(str(file_spec.get("path") or "source.txt"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"generated source path must be relative and contained: {relative_path}")
    path = input_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(file_spec.get("text") or ""), encoding="utf-8")
    return path


def _generated_requirement_flags(
    result: dict[str, Any],
    case: dict[str, Any],
    source_ids_by_path: dict[str, str],
) -> list[str]:
    flags: list[str] = []
    cited_chunks = result.get("cited_chunks", [])
    cited_source_ids = {
        str(record.get("source_id"))
        for record in cited_chunks
        if isinstance(record, dict) and record.get("source_id")
    }
    min_cited_chunks = int(case.get("min_cited_chunks", 1))
    if not isinstance(cited_chunks, list) or len(cited_chunks) < min_cited_chunks:
        flags.append("ERROR_GENERATED_MIN_CITED_CHUNKS_NOT_MET")
    for source_path in case.get("required_source_files", []):
        source_id = source_ids_by_path.get(str(source_path))
        if source_id is None:
            flags.append("ERROR_REQUIRED_SOURCE_NOT_INGESTED")
        elif source_id not in cited_source_ids:
            flags.append("ERROR_REQUIRED_SOURCE_NOT_CITED")
    snippets = " ".join(
        str(record.get("text_snippet") or "")
        for record in cited_chunks
        if isinstance(record, dict)
    ).lower()
    for term in case.get("required_snippet_terms", []):
        if str(term).lower() not in snippets:
            flags.append("ERROR_REQUIRED_SNIPPET_TERM_MISSING")
    for source_path in case.get("forbidden_source_files", []):
        source_id = source_ids_by_path.get(str(source_path))
        if source_id is not None and source_id in cited_source_ids:
            flags.append("ERROR_FORBIDDEN_SOURCE_CITED")
    if case.get("require_export_paths", True):
        export_paths = result.get("export_paths")
        if not isinstance(export_paths, dict) or not all(Path(str(path)).exists() for path in export_paths.values()):
            flags.append("ERROR_TRACE_EXPORT_MISSING")
    return sorted(set(flags))


def _evaluate_generated_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "unnamed_generated_case")
    expected_status = str(case.get("expected_status") or "CITATION_OK")
    expected_flags = sorted(str(flag) for flag in case.get("expected_flags", []))
    files = case.get("files", [])
    if not isinstance(files, list) or not files:
        return {
            "case_type": "generated",
            "case_id": case_id,
            "status": "FAIL",
            "observed_status": "GENERATED_CASE_INVALID",
            "observed_flags": ["ERROR_GENERATED_FILES_MISSING"],
            "expected_status": expected_status,
            "expected_flags": expected_flags,
        }

    with tempfile.TemporaryDirectory(prefix="mucaro-answer-trace-eval-") as temp_dir:
        cfg = _temp_settings(Path(temp_dir))
        cfg.ensure_dirs()
        source_ids_by_path: dict[str, str] = {}
        try:
            for file_spec in files:
                if not isinstance(file_spec, dict):
                    raise ValueError("generated source file spec must be an object")
                path = _write_generated_source(cfg.input_dir, file_spec)
                ingest_result = ingest_file(path, cfg)
                source_ids_by_path[str(file_spec.get("path"))] = str(ingest_result["source"]["source_id"])
            result = answer_question(
                str(case.get("query") or ""),
                top_k=int(case.get("top_k", 5)),
                retrieval_strategy=str(case.get("retrieval_strategy") or "persistent"),
                model_provider="mock",
                cfg=cfg,
                persist=True,
            )
        except Exception as exc:
            return {
                "case_type": "generated",
                "case_id": case_id,
                "status": "FAIL",
                "observed_status": "GENERATED_CASE_ERROR",
                "observed_flags": [f"ERROR_GENERATED_CASE_EXCEPTION:{type(exc).__name__}"],
                "expected_status": expected_status,
                "expected_flags": expected_flags,
            }

        if str(result.get("status", "")).startswith("ERROR_"):
            observed_status = str(result.get("status"))
            observed_flags = [observed_status]
        else:
            validated = validate_answer_trace_quality(result)
            flags = set(str(flag) for flag in validated.get("validation_flags", []))
            flags.update(_generated_requirement_flags(validated, case, source_ids_by_path))
            observed_flags = sorted(flags)
            observed_status = "CITATION_OK" if not any(flag.startswith("ERROR_") for flag in observed_flags) else "CITATION_ERROR"

    expected_flag_set = set(expected_flags)
    observed_flag_set = set(observed_flags)
    case_status = "PASS" if observed_status == expected_status and expected_flag_set.issubset(observed_flag_set) else "FAIL"
    return {
        "case_type": "generated",
        "case_id": case_id,
        "status": case_status,
        "observed_status": observed_status,
        "observed_flags": observed_flags,
        "expected_status": expected_status,
        "expected_flags": expected_flags,
    }


def build_answer_trace_eval_report(
    *,
    fixture_path: Path = DEFAULT_ANSWER_TRACE_FIXTURE,
    min_case_pass_rate: float = 1.0,
    require_negative_cases: bool = True,
) -> dict[str, Any]:
    fixture = read_json(fixture_path, {})
    cases = fixture.get("cases") if isinstance(fixture, dict) else None
    generated_cases = fixture.get("generated_cases", []) if isinstance(fixture, dict) else []
    if not isinstance(cases, list) or not cases:
        return {
            "status": ANSWER_TRACE_EVAL_FAILED,
            "fixture": _fixture_label(fixture_path),
            "min_case_pass_rate": min_case_pass_rate,
            "require_negative_cases": require_negative_cases,
            "counts": {"cases": 0, "passed": 0, "failed": 1, "expected_errors": 0},
            "failures": [{"case_id": "fixture", "detail": "Fixture must contain a non-empty cases array."}],
            "cases": [],
        }

    static_case_results = [_evaluate_case(case) for case in cases if isinstance(case, dict)]
    generated_case_results = (
        [_evaluate_generated_case(case) for case in generated_cases if isinstance(case, dict)]
        if isinstance(generated_cases, list)
        else []
    )
    case_results = [*static_case_results, *generated_case_results]
    expected_errors = sum(1 for case in cases if isinstance(case, dict) and case.get("expected_status") == "CITATION_ERROR")
    expected_errors += sum(
        1
        for case in generated_cases
        if isinstance(generated_cases, list) and isinstance(case, dict) and case.get("expected_status") == "CITATION_ERROR"
    )
    passed = sum(1 for case in case_results if case.get("status") == "PASS")
    failed_cases = [case for case in case_results if case.get("status") != "PASS"]
    case_count = len(case_results)
    case_pass_rate = passed / case_count if case_count else 0.0
    failures = list(failed_cases)
    if require_negative_cases and expected_errors == 0:
        failures.append({"case_id": "fixture", "detail": "At least one negative citation case is required."})
    if case_pass_rate < min_case_pass_rate:
        failures.append(
            {
                "case_id": "threshold",
                "detail": f"case_pass_rate={case_pass_rate:.4f} below min_case_pass_rate={min_case_pass_rate:.4f}",
            }
        )
    status = ANSWER_TRACE_EVAL_FAILED if failures else ANSWER_TRACE_EVAL_OK
    return {
        "status": status,
        "fixture": _fixture_label(fixture_path),
        "min_case_pass_rate": min_case_pass_rate,
        "require_negative_cases": require_negative_cases,
        "case_pass_rate": round(case_pass_rate, 6),
        "counts": {
            "cases": case_count,
            "static_cases": len(static_case_results),
            "generated_cases": len(generated_case_results),
            "passed": passed,
            "failed": len(failed_cases),
            "expected_errors": expected_errors,
        },
        "failures": failures,
        "cases": case_results,
    }


def build_answer_trace_quality_certification(
    root: Path,
    *,
    profile: str = ANSWER_TRACE_QUALITY_PROFILE,
    fixture_path: Path = DEFAULT_ANSWER_TRACE_FIXTURE,
    min_case_pass_rate: float = 1.0,
    require_negative_cases: bool = True,
    command: list[str] | None = None,
    write_receipt: bool = True,
) -> dict[str, Any]:
    head_sha = current_git_head(root)
    report = build_answer_trace_eval_report(
        fixture_path=fixture_path,
        min_case_pass_rate=min_case_pass_rate,
        require_negative_cases=require_negative_cases,
    )
    status = ANSWER_TRACE_QUALITY_OK if report["status"] == ANSWER_TRACE_EVAL_OK else ANSWER_TRACE_QUALITY_FAILED
    result: dict[str, Any] = {
        "status": status,
        "profile": profile,
        "head_sha": head_sha,
        "fixture": report.get("fixture"),
        "min_case_pass_rate": min_case_pass_rate,
        "require_negative_cases": require_negative_cases,
        "report": report,
        "failed_cases": report.get("failures", []),
        "receipt": None,
    }
    if write_receipt:
        try:
            receipt = write_release_receipt(
                root,
                receipt_type=ANSWER_TRACE_QUALITY_RECEIPT_TYPE,
                status=status,
                head_sha=head_sha,
                command=command or [sys.executable, "scripts/certify_answer_trace_quality.py"],
                payload={
                    "profile": profile,
                    "fixture": report.get("fixture"),
                    "min_case_pass_rate": min_case_pass_rate,
                    "require_negative_cases": require_negative_cases,
                    "report": report,
                },
            )
            result["receipt"] = str(receipt.relative_to(root) if receipt.is_relative_to(root) else receipt)
        except OSError as exc:
            result["status"] = ANSWER_TRACE_QUALITY_FAILED
            result["receipt_error"] = f"{type(exc).__name__}: {exc}"
    return result


def format_answer_trace_eval_text(report: dict[str, Any]) -> list[str]:
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    lines = [
        f"fixture={report.get('fixture')}",
        f"cases={counts.get('cases')}",
        f"static_cases={counts.get('static_cases')}",
        f"generated_cases={counts.get('generated_cases')}",
        f"passed={counts.get('passed')}",
        f"failed={counts.get('failed')}",
        f"expected_errors={counts.get('expected_errors')}",
        f"case_pass_rate={report.get('case_pass_rate')}",
        f"min_case_pass_rate={report.get('min_case_pass_rate')}",
        f"require_negative_cases={report.get('require_negative_cases')}",
    ]
    for case in report.get("cases", []):
        if isinstance(case, dict):
            lines.append(
                f"{case.get('status')} {case.get('case_id')} "
                f"observed={case.get('observed_status')} expected={case.get('expected_status')} "
                f"flags={','.join(case.get('observed_flags', [])) or 'none'}"
            )
    for failure in report.get("failures", []):
        if isinstance(failure, dict):
            lines.append(f"failure={failure.get('case_id')} {failure.get('detail') or failure.get('observed_flags')}")
    lines.append(str(report.get("status")))
    return lines


def format_answer_trace_quality_certification_text(report: dict[str, Any]) -> list[str]:
    lines = [
        f"profile={report.get('profile')}",
        f"head_sha={report.get('head_sha')}",
        f"fixture={report.get('fixture')}",
        f"min_case_pass_rate={report.get('min_case_pass_rate')}",
        f"require_negative_cases={report.get('require_negative_cases')}",
    ]
    if report.get("receipt"):
        lines.append(f"receipt={report.get('receipt')}")
    eval_report = report.get("report")
    if isinstance(eval_report, dict):
        lines.extend(format_answer_trace_eval_text(eval_report)[:-1])
    if report.get("receipt_error"):
        lines.append(f"receipt_error={report.get('receipt_error')}")
    lines.append(str(report.get("status")))
    return lines
