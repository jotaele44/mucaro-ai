from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .release_receipts import write_release_receipt
from .retrieval_eval import DEFAULT_FIXTURE, ROOT, build_retrieval_eval_report


LARGE_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval_large.json"
MIXED_FORMAT_FIXTURE = ROOT / "backend" / "tests" / "fixtures" / "retrieval_eval_mixed_formats.json"
RETRIEVAL_QUALITY_PROFILE = "v0.1.23-local-release"
RETRIEVAL_QUALITY_RECEIPT_TYPE = "retrieval_quality"
RETRIEVAL_QUALITY_OK = "RETRIEVAL_QUALITY_CERT_OK"
RETRIEVAL_QUALITY_FAILED = "RETRIEVAL_QUALITY_CERT_FAILED"


@dataclass(frozen=True)
class RetrievalQualityCase:
    name: str
    fixture_path: Path
    thresholds: dict[str, float]


RETRIEVAL_QUALITY_CASES = [
    RetrievalQualityCase(
        name="default_fixture_strict_category_binding",
        fixture_path=DEFAULT_FIXTURE,
        thresholds={
            "min_hybrid_recall": 0.80,
            "min_no_context_accuracy": 1.00,
            "min_semantic_recall": 0.80,
            "min_semantic_no_context": 1.00,
            "min_hybrid_precision": 0.20,
            "min_semantic_precision": 0.20,
            "min_expected_binding_recall": 0.95,
            "min_multi_binding_full_hit": 0.60,
            "min_distractor_free_rate": 1.00,
            "min_category_recall": 1.00,
            "min_category_binding_recall": 1.00,
        },
    ),
    RetrievalQualityCase(
        name="large_fixture_calibrated_multi_binding",
        fixture_path=LARGE_FIXTURE,
        thresholds={
            "min_hybrid_recall": 0.80,
            "min_no_context_accuracy": 1.00,
            "min_semantic_recall": 0.80,
            "min_semantic_no_context": 1.00,
            "min_hybrid_precision": 0.20,
            "min_semantic_precision": 0.20,
            "min_expected_binding_recall": 0.95,
            "min_multi_binding_full_hit": 0.60,
            "min_distractor_free_rate": 1.00,
            "min_category_recall": 1.00,
            "min_category_binding_recall": 0.80,
        },
    ),
    RetrievalQualityCase(
        name="mixed_format_fixture_strict_binding",
        fixture_path=MIXED_FORMAT_FIXTURE,
        thresholds={
            "min_hybrid_recall": 0.80,
            "min_no_context_accuracy": 1.00,
            "min_semantic_recall": 0.80,
            "min_semantic_no_context": 1.00,
            "min_hybrid_precision": 0.20,
            "min_semantic_precision": 0.20,
            "min_expected_binding_recall": 0.95,
            "min_multi_binding_full_hit": 0.60,
            "min_distractor_free_rate": 1.00,
            "min_category_recall": 1.00,
            "min_category_binding_recall": 1.00,
        },
    ),
]


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


def build_retrieval_quality_certification(
    root: Path,
    *,
    profile: str = RETRIEVAL_QUALITY_PROFILE,
    top_k: int = 5,
    command: list[str] | None = None,
    write_receipt: bool = True,
) -> dict[str, Any]:
    head_sha = current_git_head(root)
    case_reports = []
    failed_cases = []
    receipt_error = None
    for case in RETRIEVAL_QUALITY_CASES:
        report = build_retrieval_eval_report(fixture_path=case.fixture_path, top_k=top_k, **case.thresholds)
        case_report = {
            "name": case.name,
            "fixture": _fixture_label(case.fixture_path),
            "thresholds": case.thresholds,
            "status": report["status"],
            "counts": report.get("counts"),
            "category_threshold_failures": report.get("category_threshold_failures", []),
            "distractor_threshold_failures": report.get("distractor_threshold_failures", []),
            "report": report,
        }
        case_reports.append(case_report)
        if report["status"] != "RETRIEVAL_EVAL_OK":
            failed_cases.append(
                {
                    "name": case.name,
                    "fixture": _fixture_label(case.fixture_path),
                    "status": report["status"],
                    "category_threshold_failures": report.get("category_threshold_failures", []),
                    "distractor_threshold_failures": report.get("distractor_threshold_failures", []),
                }
            )

    status = RETRIEVAL_QUALITY_FAILED if failed_cases else RETRIEVAL_QUALITY_OK
    result: dict[str, Any] = {
        "status": status,
        "profile": profile,
        "head_sha": head_sha,
        "top_k": top_k,
        "cases": case_reports,
        "failed_cases": failed_cases,
        "receipt": None,
    }
    if write_receipt:
        try:
            receipt = write_release_receipt(
                root,
                receipt_type=RETRIEVAL_QUALITY_RECEIPT_TYPE,
                status=status,
                head_sha=head_sha,
                command=command or [sys.executable, "scripts/certify_retrieval_quality.py"],
                payload={
                    "profile": profile,
                    "top_k": top_k,
                    "cases": case_reports,
                    "failed_cases": failed_cases,
                },
            )
            result["receipt"] = str(receipt.relative_to(root) if receipt.is_relative_to(root) else receipt)
        except OSError as exc:
            receipt_error = f"{type(exc).__name__}: {exc}"
            result["status"] = RETRIEVAL_QUALITY_FAILED
    if receipt_error is not None:
        result["receipt_error"] = receipt_error
    return result


def format_retrieval_quality_certification_text(report: dict[str, Any]) -> list[str]:
    lines = [
        f"profile={report.get('profile')}",
        f"head_sha={report.get('head_sha')}",
        f"top_k={report.get('top_k')}",
    ]
    if report.get("receipt"):
        lines.append(f"receipt={report.get('receipt')}")
    for case in report.get("cases", []):
        if not isinstance(case, dict):
            continue
        counts = case.get("counts") if isinstance(case.get("counts"), dict) else {}
        lines.append(
            f"{case.get('status')} {case.get('name')} fixture={case.get('fixture')} "
            f"queries={counts.get('queries')} source_ids={counts.get('source_ids')} "
            f"source_formats={counts.get('source_formats')} "
            f"distractor_queries={counts.get('distractor_queries')} "
            f"distractor_bindings={counts.get('distractor_bindings')} "
            f"query_categories={counts.get('query_categories')}"
        )
        thresholds = case.get("thresholds") if isinstance(case.get("thresholds"), dict) else {}
        lines.append(
            f"thresholds {case.get('name')} "
            f"min_category_recall={thresholds.get('min_category_recall')} "
            f"min_category_binding_recall={thresholds.get('min_category_binding_recall')} "
            f"min_expected_binding_recall={thresholds.get('min_expected_binding_recall')} "
            f"min_multi_binding_full_hit={thresholds.get('min_multi_binding_full_hit')} "
            f"min_distractor_free_rate={thresholds.get('min_distractor_free_rate')}"
        )
        for failure in case.get("category_threshold_failures", []):
            if isinstance(failure, dict):
                lines.append(
                    f"category_threshold_failure case={case.get('name')} "
                    f"strategy={failure.get('strategy')} category={failure.get('category')} "
                    f"recall={failure.get('recall_at_k')} "
                    f"binding_recall={failure.get('expected_binding_recall_at_k')}"
                )
        for failure in case.get("distractor_threshold_failures", []):
            if isinstance(failure, dict):
                lines.append(
                    f"distractor_threshold_failure case={case.get('name')} "
                    f"strategy={failure.get('strategy')} "
                    f"distractor_free={failure.get('distractor_free_rate')} "
                    f"distractor_hits={failure.get('distractor_hits')}"
                )
    if report.get("receipt_error"):
        lines.append(f"receipt_error={report.get('receipt_error')}")
    lines.append(str(report.get("status")))
    return lines
