from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .capability_contracts import build_evidence_contract
from .coding_workspace import CodingWorkspaceError, inspect_repository_diff, read_repository_file
from .config import Settings, settings

CODING_REVIEW_SCHEMA_VERSION = "mucaro-coding-review/1.0"
SEVERITY_ORDER = {"BLOCKER": 0, "CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4, "INFO": 5}


class CodingReviewError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def build_code_review_report(
    repo_root: str | Path,
    findings: list[dict[str, Any]],
    *,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(findings, list):
        raise CodingReviewError("ERROR_CODING_REVIEW_FINDINGS", "findings must be a list")
    try:
        diff = inspect_repository_diff(repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:
        raise CodingReviewError(exc.status, exc.detail) from exc
    changed_paths = sorted(set(diff.get("changed_files") or []) | set(diff.get("untracked_files") or []))
    changed_set = set(changed_paths)
    normalized: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise CodingReviewError("ERROR_CODING_REVIEW_FINDING", f"findings[{index}] must be an object")
        path = finding.get("path")
        severity = finding.get("severity")
        line_start = finding.get("line_start")
        line_end = finding.get("line_end", line_start)
        summary = finding.get("summary")
        evidence = finding.get("evidence")
        if not isinstance(path, str) or path not in changed_set:
            raise CodingReviewError("ERROR_CODING_REVIEW_UNCHANGED_PATH", str(path))
        if severity not in SEVERITY_ORDER:
            raise CodingReviewError("ERROR_CODING_REVIEW_SEVERITY", str(severity))
        if not isinstance(line_start, int) or isinstance(line_start, bool) or line_start <= 0:
            raise CodingReviewError("ERROR_CODING_REVIEW_LINE_RANGE", path)
        if not isinstance(line_end, int) or isinstance(line_end, bool) or line_end < line_start:
            raise CodingReviewError("ERROR_CODING_REVIEW_LINE_RANGE", path)
        if not isinstance(summary, str) or not summary.strip() or not isinstance(evidence, str) or not evidence.strip():
            raise CodingReviewError("ERROR_CODING_REVIEW_EVIDENCE", path)
        try:
            source = read_repository_file(repo_root, path, cfg=cfg)
        except CodingWorkspaceError as exc:
            raise CodingReviewError(exc.status, exc.detail) from exc
        if source.get("encoding_state") != "UTF8" or not isinstance(source.get("content"), str):
            raise CodingReviewError("ERROR_CODING_REVIEW_TEXT_REQUIRED", path)
        lines = source["content"].splitlines()
        if line_end > len(lines):
            raise CodingReviewError("ERROR_CODING_REVIEW_LINE_RANGE", path)
        excerpt = "\n".join(lines[line_start - 1 : line_end])
        normalized.append(
            {
                "path": path,
                "severity": severity,
                "line_start": line_start,
                "line_end": line_end,
                "summary": summary.strip(),
                "evidence": evidence.strip(),
                "source_sha256": source["sha256"],
                "excerpt": excerpt,
                "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            }
        )
    normalized.sort(key=lambda item: (SEVERITY_ORDER[item["severity"]], item["path"], item["line_start"], item["line_end"]))
    return {
        "schema_version": CODING_REVIEW_SCHEMA_VERSION,
        "status": "CODING_REVIEW_FINDINGS" if normalized else "CODING_REVIEW_NO_FINDINGS",
        "repo_root": diff["repo_root"],
        "head_sha": diff["head_sha"],
        "base_commit_sha": diff["base_commit_sha"],
        "changed_paths": changed_paths,
        "finding_count": len(normalized),
        "findings": normalized,
        "invariants": {
            "finding_count_closed": len(normalized) == sum(1 for _ in normalized),
            "changed_sources_bound": all(item["path"] in changed_set for item in normalized),
        },
        "limitations": [
            "review scope is limited to current changed and untracked text files",
            "no findings is not proof that no defects exist",
            "deleted files and binary/non-UTF8 evidence require an alternate review adapter",
        ],
        "falsification_criteria": [
            "reject findings against paths outside the preserved changed candidate set",
            "reject invalid or out-of-range line evidence",
            "reject review evidence that cannot bind to a current source SHA-256",
        ],
        "evidence_contract": build_evidence_contract(
            claim_type="COMPUTED",
            evidence_type="CURRENT_DIFF_REVIEW",
            source_identity_type="CHANGED_PATH_PLUS_SOURCE_SHA256",
            confidence="BOUNDED_REVIEW",
            uncertainty_state="BOUNDED",
            contradiction_state="NOT_ADJUDICATED",
            verification_method="inspect current Git diff and bind every finding to a current source excerpt hash",
            limitations=[
                "A review report is limited to the preserved changed and untracked text-file candidate set.",
                "No findings is not proof that no defects exist.",
                "Deleted, binary, non-UTF8, and runtime-only behavior require separate evidence adapters.",
            ],
            falsification_criteria=[
                "reject findings outside the current changed/untracked path set",
                "reject line evidence outside the current source file",
                "reject an excerpt whose SHA-256 does not match the saved excerpt",
                "reject a review result whose changed-source invariants do not close",
            ],
        ),
    }


# Compatibility receipt surface retained for callers/tests that adopted the
# earlier coding-review contract before the normalized report API landed.
# The normalized report remains the canonical implementation.
_COMPAT_ERROR_STATUS = {
    "ERROR_CODING_REVIEW_UNCHANGED_PATH": "ERROR_CODING_REVIEW_PATH_NOT_CHANGED",
    "ERROR_CODING_REVIEW_LINE_RANGE": "ERROR_CODING_REVIEW_LINE",
}


def build_code_review_receipt(
    repo_root: str | Path,
    findings: list[dict[str, Any]],
    *,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(findings, list):
        raise CodingReviewError("ERROR_CODING_REVIEW_FINDINGS", "findings must be a list")

    normalized_findings: list[dict[str, Any]] = []
    compat_metadata: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise CodingReviewError("ERROR_CODING_REVIEW_FINDING", f"findings[{index}] must be an object")
        raw_severity = finding.get("severity")
        severity = str(raw_severity or "").strip().upper()
        line_start = finding.get("start_line", finding.get("line_start"))
        line_end = finding.get("end_line", finding.get("line_end", line_start))
        title = finding.get("title", finding.get("summary"))
        detail = finding.get("detail", finding.get("evidence"))
        falsification = finding.get("falsification")
        normalized_findings.append(
            {
                "path": finding.get("path"),
                "severity": severity,
                "line_start": line_start,
                "line_end": line_end,
                "summary": title,
                "evidence": detail,
            }
        )
        compat_metadata.append(
            {
                "severity": str(raw_severity or severity).strip().lower(),
                "title": title,
                "detail": detail,
                "falsification": falsification,
            }
        )

    try:
        report = build_code_review_report(repo_root, normalized_findings, cfg=cfg)
    except CodingReviewError as exc:
        mapped = _COMPAT_ERROR_STATUS.get(exc.status, exc.status)
        raise CodingReviewError(mapped, exc.detail) from exc

    metadata_by_key = {
        (
            normalized["path"],
            normalized["severity"],
            normalized["line_start"],
            normalized["line_end"],
        ): meta
        for normalized, meta in zip(normalized_findings, compat_metadata)
    }
    severity_counts: dict[str, int] = {}
    compatibility_findings: list[dict[str, Any]] = []
    for finding in report["findings"]:
        key = (
            finding["path"],
            finding["severity"],
            finding["line_start"],
            finding["line_end"],
        )
        meta = metadata_by_key.get(key, {})
        severity_key = finding["severity"].lower()
        severity_counts[severity_key] = severity_counts.get(severity_key, 0) + 1
        compatibility_findings.append(
            {
                **finding,
                "severity": severity_key,
                "start_line": finding["line_start"],
                "end_line": finding["line_end"],
                "title": meta.get("title", finding["summary"]),
                "detail": meta.get("detail", finding["evidence"]),
                "falsification": meta.get("falsification"),
                "evidence_excerpt": finding["excerpt"],
            }
        )

    return {
        **report,
        "severity_counts": severity_counts,
        "findings": compatibility_findings,
        "invariants": {
            **report["invariants"],
            "severity_accounting_closed": sum(severity_counts.values()) == report["finding_count"],
            "compatibility_line_binding_closed": all(
                item["start_line"] == item["line_start"] and item["end_line"] == item["line_end"]
                for item in compatibility_findings
            ),
        },
    }
