from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .coding_workspace import inspect_repository_diff, read_repository_file
from .config import Settings, settings

CODING_REVIEW_SCHEMA_VERSION = "mucaro-coding-review/1.0"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class CodingReviewError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _validate_finding(finding: dict[str, Any]) -> None:
    if not isinstance(finding, dict):
        raise CodingReviewError("ERROR_CODING_REVIEW_FINDING", "finding must be an object")
    if finding.get("severity") not in SEVERITY_ORDER:
        raise CodingReviewError("ERROR_CODING_REVIEW_SEVERITY", str(finding.get("severity")))
    if not isinstance(finding.get("path"), str) or not finding["path"]:
        raise CodingReviewError("ERROR_CODING_REVIEW_PATH", "finding path is required")
    for key in ("start_line", "end_line"):
        if not isinstance(finding.get(key), int) or isinstance(finding.get(key), bool) or finding[key] <= 0:
            raise CodingReviewError("ERROR_CODING_REVIEW_LINE", key)
    if finding["end_line"] < finding["start_line"]:
        raise CodingReviewError("ERROR_CODING_REVIEW_LINE", "end_line precedes start_line")
    for key in ("title", "detail", "falsification"):
        if not isinstance(finding.get(key), str) or not finding[key].strip():
            raise CodingReviewError("ERROR_CODING_REVIEW_TEXT", key)


def build_code_review_receipt(
    repo_root: str | Path,
    findings: list[dict[str, Any]],
    *,
    cfg: Settings = settings,
    base: str = "HEAD",
) -> dict[str, Any]:
    diff = inspect_repository_diff(repo_root, cfg=cfg, base=base)
    changed = set(diff["changed_files"]) | set(diff["untracked_files"])
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()

    for finding in findings:
        _validate_finding(finding)
        path = finding["path"]
        if path not in changed:
            raise CodingReviewError("ERROR_CODING_REVIEW_PATH_NOT_CHANGED", path)
        key = (path, finding["start_line"], finding["end_line"], finding["title"])
        if key in seen:
            raise CodingReviewError("ERROR_CODING_REVIEW_DUPLICATE", repr(key))
        seen.add(key)
        source = read_repository_file(repo_root, path, cfg=cfg)
        if source["encoding_state"] != "UTF8" or source["content"] is None:
            raise CodingReviewError("ERROR_CODING_REVIEW_NON_TEXT", path)
        lines = source["content"].splitlines()
        if finding["end_line"] > len(lines):
            raise CodingReviewError("ERROR_CODING_REVIEW_LINE", f"{path}:{finding['end_line']} exceeds {len(lines)}")
        excerpt = "\n".join(lines[finding["start_line"] - 1:finding["end_line"]])
        rows.append(
            {
                **finding,
                "source_sha256": source["sha256"],
                "excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "evidence_excerpt": excerpt,
            }
        )

    rows.sort(key=lambda row: (SEVERITY_ORDER[row["severity"]], row["path"], row["start_line"], row["title"]))
    counts = {severity: sum(row["severity"] == severity for row in rows) for severity in SEVERITY_ORDER}
    payload = {
        "schema_version": CODING_REVIEW_SCHEMA_VERSION,
        "status": "CODING_REVIEW_FINDINGS" if rows else "CODING_REVIEW_NO_FINDINGS",
        "repo_root": diff["repo_root"],
        "head_sha": diff["head_sha"],
        "base_ref": diff["base_ref"],
        "base_commit_sha": diff["base_commit_sha"],
        "diff_sha256": diff["diff_sha256"],
        "changed_files": diff["changed_files"],
        "untracked_files": diff["untracked_files"],
        "finding_count": len(rows),
        "severity_counts": counts,
        "findings": rows,
        "invariants": {
            "every_finding_binds_changed_path": all(row["path"] in changed for row in rows),
            "every_finding_binds_line_range": all(row["start_line"] <= row["end_line"] for row in rows),
            "finding_count_closed": len(rows) == sum(counts.values()),
        },
        "limitations": [
            "this receipt validates supplied review findings; it does not claim exhaustive automated defect discovery",
            "absence of findings is not proof of defect absence",
        ],
        "falsification_criteria": [
            "reject findings not bound to changed files",
            "reject line ranges outside the current UTF-8 source manifestation",
            "reject duplicate evidence bindings",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
