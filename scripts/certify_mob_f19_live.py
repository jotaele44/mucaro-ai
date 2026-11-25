#!/usr/bin/env python3
"""MOB-F19 live receipt: list this repository's pull requests and read one exactly, through the governed tools.

Both reads go through the tool runtime the app uses: each call is requested, approved against its argument digest
and run, so the tool audit chain covers them. The PR detail must agree with its list row (number, title, head SHA,
state), and a call that was never approved must refuse to run. Needs the GitHub CLI with a read token (in GitHub
Actions, GH_TOKEN set to the job token). The receipt records identifiers and hashes, never the token.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.tools import (  # noqa: E402
    ToolRuntimeError,
    approve_tool_call,
    request_tool_call,
    run_tool_call,
    verify_tool_audit_chain,
)
from backend.app.utils import canonical_sha256, utc_now  # noqa: E402

STATUS_OK = "MOB_F19_LIVE_PR_READ_OK"
STATUS_FAIL = "MOB_F19_LIVE_PR_READ_FAILED"
COMPARED_FIELDS = ("number", "title", "head_sha", "head_ref", "base_ref", "state", "draft", "url")


def _git_head() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "mob-f19.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def _governed(tool: str, arguments: dict, cfg: Settings) -> dict:
    call = request_tool_call(tool, arguments, cfg=cfg, requested_by="operator", reason="MOB-F19 live receipt")["tool_call"]
    approve_tool_call(call["tool_call_id"], cfg=cfg, approved_by="operator", expected_arguments_sha256=call["arguments_sha256"])
    executed = run_tool_call(call["tool_call_id"], cfg=cfg)["tool_call"]
    return executed


def certify(repo: str, state: str, work: Path, *, head_sha: str) -> dict:
    cfg = _settings(work)
    checks: dict[str, bool] = {}
    detail: dict = {}

    # Negative gate: a call nobody approved never reaches GitHub.
    unapproved = request_tool_call("github.prs_list", {"repo": repo, "state": state}, cfg=cfg, requested_by="operator", reason="must not run")["tool_call"]
    try:
        run_tool_call(unapproved["tool_call_id"], cfg=cfg)
        checks["unapproved_call_refused"] = False
    except ToolRuntimeError as exc:
        checks["unapproved_call_refused"] = exc.status.startswith("ERROR_")
        detail["unapproved_refusal"] = exc.status

    listed = _governed("github.prs_list", {"repo": repo, "state": state, "per_page": 20}, cfg)
    list_result = listed.get("result") or {}
    rows = list_result.get("pull_requests") or []
    checks["list_executed"] = listed.get("status") == "EXECUTED" and list_result.get("status") == "GITHUB_PRS_OK"
    checks["list_has_retrieved_utc"] = bool(list_result.get("retrieved_utc"))
    checks["list_nonempty"] = bool(rows)
    checks["list_rows_belong_to_repository"] = bool(rows) and all(row.get("repository") == repo for row in rows)

    if rows:
        target = rows[0]
        viewed = _governed("github.pr_view", {"repo": repo, "number": target["number"]}, cfg)
        view_result = viewed.get("result") or {}
        pr = view_result.get("pull_request") or {}
        checks["detail_executed"] = viewed.get("status") == "EXECUTED" and view_result.get("status") == "GITHUB_PR_OK"
        checks["detail_has_retrieved_utc"] = bool(view_result.get("retrieved_utc"))
        mismatched = [field for field in COMPARED_FIELDS if pr.get(field) != target.get(field)]
        checks["detail_matches_list_row"] = not mismatched
        checks["detail_head_sha_is_full"] = isinstance(pr.get("head_sha"), str) and len(pr["head_sha"]) == 40
        detail.update({
            "pr_number": target["number"],
            "pr_head_sha": pr.get("head_sha"),
            "pr_state": pr.get("state"),
            "pr_draft": pr.get("draft"),
            "detail_mismatched_fields": mismatched,
            "pr_view_tool_call_id": viewed.get("tool_call_id"),
            "pr_view_result_sha256": canonical_sha256(view_result),
        })
    else:
        for name in ("detail_executed", "detail_has_retrieved_utc", "detail_matches_list_row", "detail_head_sha_is_full"):
            checks[name] = False

    chain = verify_tool_audit_chain(cfg)
    checks["tool_audit_chain_ok"] = chain["status"] == "TOOL_AUDIT_CHAIN_OK"
    receipt = {
        "schema_version": "mucaro-mob-f19-live-pr-read/1.0",
        "requirement_id": "MOB-F19",
        "status": STATUS_OK if all(checks.values()) else STATUS_FAIL,
        "head_sha": head_sha,
        "repository": repo,
        "state_filter": state,
        "captured_utc": utc_now(),
        "listed_count": len(rows),
        "listed_numbers": [row.get("number") for row in rows],
        "prs_list_tool_call_id": listed.get("tool_call_id"),
        "prs_list_result_sha256": canonical_sha256(list_result),
        "audit_chain": chain,
        "checks": checks,
        **detail,
    }
    receipt["receipt_sha256"] = hashlib.sha256(json.dumps(receipt, sort_keys=True).encode("utf-8")).hexdigest()
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="MOB-F19: live, governed pull-request list and exact detail.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--state", default="all", choices=["open", "closed", "all"])
    parser.add_argument("--head-sha", help="Source SHA this run represents; defaults to git HEAD.")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mucaro-mob-f19-") as temporary:
        try:
            receipt = certify(args.repo, args.state, Path(temporary), head_sha=(args.head_sha or _git_head()).strip())
        except ToolRuntimeError as exc:
            receipt = {"schema_version": "mucaro-mob-f19-live-pr-read/1.0", "requirement_id": "MOB-F19", "status": STATUS_FAIL,
                       "error": exc.status, "detail": exc.detail}
    path = output / "mob-f19-live-pr-read-receipt.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt["status"])
    print(json.dumps({key: receipt.get(key) for key in ("head_sha", "repository", "listed_count", "pr_number", "receipt_sha256", "checks", "error")}, sort_keys=True))
    return 0 if receipt["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
