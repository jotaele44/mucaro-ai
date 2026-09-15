from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

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
    }
