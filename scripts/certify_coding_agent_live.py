"""Certify the coding agent against a real model: one bounded, read-only turn on a throwaway repository.

The model must find the repository's HEAD with the `status` tool, read MARKER.txt with the `read` tool, and
report both. The marker is a random nonce that exists only in the tool result, so a pass proves the tool output
reached the model and was used, not that the model guessed. Mutating tools are refused throughout.

Nothing from your own repositories is involved: the repository is created in a temporary directory with
its own isolated settings. The API key is read only from the environment by the provider layer, and is never
printed, stored or placed in the receipt.

Outcomes follow the other certifiers' honesty rule: PASS needs executed evidence; FAIL means a model answered and the
agent did not succeed; BLOCKED means a precondition is missing (live mode off, no key, no reachable server, an unknown
model), so nothing was proven either way. An empty or absent input never counts as a pass.

    # with ANTHROPIC_API_KEY already set in your shell (never on the command line):
    MUCARO_AI_LIVE_MODE=1 python scripts/certify_coding_agent_live.py \
        --provider anthropic --model <a model your key can use> --output receipt.json
    python scripts/certify_coding_agent_live.py --provider local --model <name> --output receipt.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.coding_agent import AgentError, Chat, provider_chat, run_agent
from backend.app.config import Settings
from backend.app.model_router import ModelProviderError
from backend.app.operator_audit import ApprovalDecision
from backend.app.permissions import grant_directory

SCHEMA_VERSION = "mucaro-coding-agent-live-cert/1.0"
STATUS_OK = "CODING_AGENT_LIVE_CERT_OK"
STATUS_FAILED = "CODING_AGENT_LIVE_CERT_FAILED"
STATUS_BLOCKED = "CODING_AGENT_LIVE_CERT_BLOCKED"
EXIT_CODES = {STATUS_OK: 0, STATUS_FAILED: 2, STATUS_BLOCKED: 3}
MAX_STEPS = 4
FINAL_EXCERPT_CHARS = 500
GOAL = (
    "Find this repository's current HEAD commit and read the file MARKER.txt using your tools. "
    "Then finish with a summary that states the first 7 characters of the HEAD commit SHA and the exact marker text."
)
# A provider that answers with one of these has told us what is missing: no claim can be made either way.
BLOCKING_PROVIDER_STATUSES = frozenset(
    {
        "ERROR_LIVE_MODE_DISABLED",
        "ERROR_MODEL_API_KEY_MISSING",
        "ERROR_MODEL_SDK_MISSING",
        "ERROR_MODEL_AUTH_REJECTED",
        "ERROR_MODEL_NOT_FOUND",
        "ERROR_MODEL_RATE_LIMITED",
        "ERROR_MODEL_UNAVAILABLE",
        "ERROR_LOCAL_MODEL_URL_NOT_LOOPBACK",
        "ERROR_LOCAL_MODEL_UNAVAILABLE",
        "ERROR_LOCAL_MODEL_NOT_INSTALLED",
    }
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def make_probe_repository(parent: Path) -> tuple[Settings, Path, str]:
    """A throwaway repository outside any real project, with isolated settings that grant it. Returns (cfg, repo, marker)."""
    app_root = parent / "app"
    data = app_root / "data"
    cfg = Settings(
        project_root=app_root,
        data_dir=data,
        input_dir=data / "input",
        processed_dir=data / "processed",
        export_dir=data / "exports",
        index_dir=data / "indexes",
        sqlite_path=data / "processed" / "probe.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    repo = parent / "probe-repo"
    repo.mkdir()
    marker = f"MUCARO-LIVE-CERT-{secrets.token_hex(8)}"
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "certifier@example.invalid")
    _git(repo, "config", "user.name", "Mucaro certifier")
    (repo / "MARKER.txt").write_text(marker + "\n", encoding="utf-8")
    (repo / "README.md").write_text("Throwaway repository for the Mucaro coding agent live certification.\n", encoding="utf-8")
    _git(repo, "add", "MARKER.txt", "README.md")
    _git(repo, "commit", "--quiet", "-m", "probe")
    grant_directory(repo, cfg, granted_by="certify_coding_agent_live", reason="throwaway probe repository")
    return cfg, repo, marker


def _receipt(status: str, provider: str, model: str, body: dict[str, Any]) -> dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain=v1", "-z"], cwd=ROOT, text=True, capture_output=True, check=False).stdout
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "provider": provider,
        "model": model,
        "source": {"head_sha": head, "tree": "DIRTY" if dirty else "CLEAN"},
        **body,
        "limitations": [
            "one bounded, read-only turn on a throwaway repository: it shows the JSON action protocol works with this model, not that the model is good at coding",
            "a PASS applies to this provider, model and source revision only, on the day it ran",
        ],
    }
    payload["receipt_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return payload


def certify(
    provider: str,
    model: str,
    *,
    chat_factory: Callable[[str, str], Chat] = provider_chat,
    workdir: Path | None = None,
    max_steps: int = MAX_STEPS,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mucaro-agent-cert-") as scratch:
        cfg, repo, marker = make_probe_repository(workdir or Path(scratch))
        head_before = _git(repo, "rev-parse", "HEAD")
        try:
            chat = chat_factory(provider, model)
            result = run_agent(
                GOAL, repo, chat=chat, approve=lambda *_: ApprovalDecision(False, "callback"), cfg=cfg, max_steps=max_steps
            )
        except ModelProviderError as exc:
            if exc.status in BLOCKING_PROVIDER_STATUSES:
                return _receipt(
                    STATUS_BLOCKED, provider, model,
                    {"blocked_on": {"status": exc.status, "detail": exc.detail}, "evidence": "none: no model turn completed"},
                )
            return _receipt(STATUS_FAILED, provider, model, {"failure": {"status": exc.status, "detail": exc.detail}, "invariants": {}})
        except AgentError as exc:  # the agent could not even start a turn: a defect, not a missing input
            return _receipt(STATUS_FAILED, provider, model, {"failure": {"status": exc.status, "detail": exc.detail}, "invariants": {}})

        steps = result["steps"]
        outcomes = [(step.get("tool"), step.get("outcome_status")) for step in steps if "tool" in step]
        final = result.get("final") or ""
        head_after = _git(repo, "rev-parse", "HEAD")
        invariants = {
            "agent_completed": result["status"] == "AGENT_COMPLETED",
            "status_tool_succeeded": ("status", "CODING_REPOSITORY_STATUS_OK") in outcomes,
            "read_tool_succeeded": ("read", "CODING_FILE_READ_OK") in outcomes,
            "final_contains_marker_from_tool_result": marker in final,
            "final_contains_head_prefix": head_before[:7] in final,
            "no_mutating_step_executed": not any(step.get("approved") is True for step in steps),
            "repository_head_conserved": head_before == head_after,
            "repository_clean_after": _git(repo, "status", "--porcelain=v1") == "",
        }
        status = STATUS_OK if all(invariants.values()) else STATUS_FAILED
        return _receipt(
            status, provider, model,
            {
                "invariants": invariants,
                "steps": [{"step": step["step"], "tool": step.get("tool"), "outcome_status": step.get("outcome_status")} for step in steps if "tool" in step],
                "invalid_action_steps": sum(1 for step in steps if "reply" in step and "tool" not in step),
                "step_count": result["step_count"],
                "transcript_sha256": result["transcript_sha256"],
                "final_excerpt": final[:FINAL_EXCERPT_CHARS],
            },
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded, read-only coding-agent turn against a real model on a throwaway repository.")
    parser.add_argument("--provider", required=True, choices=["anthropic", "local"], help="Which provider to certify.")
    parser.add_argument("--model", required=True, help="The exact model to certify (a receipt says which model it covers).")
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS, help="Tool turns allowed (1-30).")
    parser.add_argument("--output", type=Path, help="Write the receipt here.")
    args = parser.parse_args(argv)
    if not 1 <= args.max_steps <= 30:
        parser.error("--max-steps must be 1 to 30")

    receipt = certify(args.provider, args.model, chat_factory=provider_chat, max_steps=args.max_steps)  # resolved now, so tests can inject
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt["status"])
    print(json.dumps(receipt, sort_keys=True))
    return EXIT_CODES[receipt["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
