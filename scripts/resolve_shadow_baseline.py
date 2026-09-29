#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "mucaro-shadow-baseline-resolution/1.0"


class ShadowBaselineError(RuntimeError):
    pass


def _full_sha(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if len(text) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ShadowBaselineError(f"{label} must be a full 40-character Git SHA")
    return text.lower()


def git_is_ancestor(base_sha: str, checkpoint_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, checkpoint_sha],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


def select_latest_closed_baseline(
    runs: list[dict[str, object]],
    *,
    checkpoint_sha: str,
    is_ancestor: Callable[[str, str], bool] = git_is_ancestor,
) -> dict[str, object]:
    checkpoint = _full_sha(checkpoint_sha, label="checkpoint_sha")
    for run in runs:
        if run.get("event") != "push" or run.get("conclusion") != "success":
            continue
        if run.get("head_branch") not in (None, "main"):
            continue
        candidate = _full_sha(run.get("head_sha"), label="workflow head_sha")
        if candidate == checkpoint:
            continue
        if not is_ancestor(candidate, checkpoint):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        return {
            "base_sha": candidate,
            "baseline_run_id": run_id,
            "baseline_run_url": run.get("html_url"),
            "baseline_created_at": run.get("created_at"),
        }
    raise ShadowBaselineError(
        "no successful main-push Shadow QA run is an ancestor of the requested checkpoint"
    )


def fetch_successful_main_runs(repository: str, workflow: str, token: str) -> list[dict[str, object]]:
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise ShadowBaselineError("repository must be owner/name")
    workflow_id = urllib.parse.quote(workflow, safe="")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/{workflow_id}/runs"
        "?branch=main&event=push&status=success&per_page=100"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mucaro-shadow-baseline-resolver",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - convert all transport/parser failures to fail-closed evidence
        raise ShadowBaselineError(f"unable to query successful Shadow QA runs: {exc}") from exc
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise ShadowBaselineError("GitHub Actions response did not contain workflow_runs")
    return [row for row in runs if isinstance(row, dict)]


def resolve(
    *,
    repository: str,
    checkpoint_sha: str,
    workflow: str,
    token: str | None,
    base_override: str | None,
) -> dict[str, object]:
    checkpoint = _full_sha(checkpoint_sha, label="checkpoint_sha")
    if base_override:
        base = _full_sha(base_override, label="base_override")
        if base == checkpoint:
            raise ShadowBaselineError("base_override must precede the checkpoint")
        if not git_is_ancestor(base, checkpoint):
            raise ShadowBaselineError("base_override is not an ancestor of the checkpoint")
        selection: dict[str, object] = {
            "base_sha": base,
            "baseline_run_id": None,
            "baseline_run_url": None,
            "baseline_created_at": None,
        }
        source = "EXPLICIT_OPERATOR_OVERRIDE"
    else:
        if not token:
            raise ShadowBaselineError("GH_TOKEN is required when no explicit base override is supplied")
        selection = select_latest_closed_baseline(
            fetch_successful_main_runs(repository, workflow, token),
            checkpoint_sha=checkpoint,
        )
        source = "LATEST_SUCCESSFUL_MAIN_PUSH_SHADOW_RUN"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "SHADOW_BASELINE_RESOLVED",
        "repository": repository,
        "workflow": workflow,
        "checkpoint_sha": checkpoint,
        "base_sha": selection["base_sha"],
        "source": source,
        "baseline_run_id": selection["baseline_run_id"],
        "baseline_run_url": selection["baseline_run_url"],
        "baseline_created_at": selection["baseline_created_at"],
        "resolved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "rule": (
            "Automatic runs carry unresolved residue forward from the latest successful main-push "
            "Shadow QA checkpoint; provisional/failed checkpoints never become the next baseline."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve the cumulative fail-closed Shadow QA baseline.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--workflow", default="shadow-qa.yml")
    parser.add_argument("--base-override")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = resolve(
            repository=args.repository,
            checkpoint_sha=args.checkpoint,
            workflow=args.workflow,
            token=os.environ.get("GH_TOKEN"),
            base_override=args.base_override,
        )
    except ShadowBaselineError as exc:
        print(f"ERROR_SHADOW_BASELINE {exc}", file=sys.stderr)
        return 2

    target = args.output.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "SHADOW_BASELINE_RESOLVED "
        f"base={payload['base_sha']} checkpoint={payload['checkpoint_sha']} "
        f"source={payload['source']} run={payload['baseline_run_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
