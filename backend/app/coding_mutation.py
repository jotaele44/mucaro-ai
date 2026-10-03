from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from .coding_patch import validate_patch_proposal
from .coding_workspace import inspect_repository, resolve_repository
from .config import Settings, settings
from .permission_engine import PermissionEngineError, PermissionRequest, require
from .local_tools import resolve_local_tool
from .permissions import is_within_path

CODING_MUTATION_SCHEMA_VERSION = "mucaro-coding-mutation/1.0"


class CodingMutationError(RuntimeError):
    def __init__(self, status: str, detail: str, *, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.receipt = receipt


def _git_apply(repo_root: Path, patch_bytes: bytes, *, reverse: bool = False) -> tuple[bool, str]:
    git = resolve_local_tool("git")
    if not git:
        raise CodingMutationError("ERROR_CODING_GIT_UNAVAILABLE", "Git is not bundled in this Mucaro runtime.")
    argv = [git, "apply"]
    if reverse:
        argv.append("--reverse")
    argv.extend(["--whitespace=error-all" if not reverse else "--whitespace=nowarn", "-"])
    try:
        proc = subprocess.run(
            argv,
            cwd=repo_root,
            input=patch_bytes,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except OSError as exc:
        raise CodingMutationError("ERROR_CODING_GIT_UNAVAILABLE", "Bundled Git could not be started.") from exc
    except subprocess.TimeoutExpired as exc:
        raise CodingMutationError("ERROR_CODING_MUTATION_TIMEOUT", "git apply timed out") from exc
    detail = proc.stderr.decode("utf-8", errors="replace").strip() or proc.stdout.decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, detail


def _current_preimage(repo_root: Path, path: str) -> dict[str, Any]:
    target = (repo_root / path).resolve()
    if not is_within_path(target, repo_root):
        raise CodingMutationError("ERROR_CODING_MUTATION_PATH_BOUNDARY", path)
    if not target.exists():
        return {"path": path, "state": "MISSING", "byte_size": None, "sha256": None}
    if not target.is_file():
        return {"path": path, "state": "NON_FILE", "byte_size": None, "sha256": None}
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise CodingMutationError("ERROR_CODING_MUTATION_FILE_READ", path) from exc
    return {"path": path, "state": "EXISTS", "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _normalize_expected_preimages(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGES_REQUIRED", "expected_preimages must be a non-empty list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not row["path"]:
            raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGE_INVALID", "every expected preimage requires a path")
        path = row["path"]
        if path in seen:
            raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGE_DUPLICATE", path)
        seen.add(path)
        state = row.get("state")
        if state not in {"EXISTS", "MISSING", "NON_FILE"}:
            raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGE_INVALID", f"invalid state for {path}")
        if state == "EXISTS" and (not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64):
            raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGE_INVALID", f"missing sha256 for {path}")
        rows.append({"path": path, "state": state, "byte_size": row.get("byte_size"), "sha256": row.get("sha256")})
    return sorted(rows, key=lambda item: item["path"])


def _rollback_and_raise(
    repo_root: Path,
    patch_bytes: bytes,
    expected: list[dict[str, Any]],
    receipt: dict[str, Any],
    detail: str,
) -> None:
    rollback_ok, rollback_detail = _git_apply(repo_root, patch_bytes, reverse=True)
    restored = sorted((_current_preimage(repo_root, row["path"]) for row in expected), key=lambda item: item["path"])
    rollback_succeeded = bool(rollback_ok and restored == expected)
    receipt.update(
        {
            "rollback_attempted": True,
            "rollback_succeeded": rollback_succeeded,
            "rollback_detail": rollback_detail or None,
            "status": "CODING_MUTATION_ROLLED_BACK" if rollback_succeeded else "CODING_MUTATION_ROLLBACK_FAILED",
        }
    )
    raise CodingMutationError(receipt["status"], detail, receipt=receipt)


def apply_patch_transaction(
    repo_root: str | Path,
    patch: str,
    *,
    expected_head_sha: str,
    expected_patch_sha256: str,
    expected_preimages: list[dict[str, Any]],
    cfg: Settings = settings,
) -> dict[str, Any]:
    context = resolve_repository(repo_root, cfg)
    try:
        require(PermissionRequest(capability="filesystem.write", subject="coding_mutation.apply_patch_transaction", target=str(context.repo_root), origin="operator"), cfg=cfg)
    except PermissionEngineError as exc:
        raise CodingMutationError(exc.status, exc.detail) from exc
    before = inspect_repository(context.repo_root, cfg=cfg)
    if before["head_sha"] != expected_head_sha:
        raise CodingMutationError("ERROR_CODING_MUTATION_HEAD_DRIFT", f"expected {expected_head_sha}, observed {before['head_sha']}")

    patch_bytes = patch.encode("utf-8")
    observed_patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    if observed_patch_sha256 != expected_patch_sha256:
        raise CodingMutationError("ERROR_CODING_MUTATION_PATCH_DRIFT", "patch SHA-256 does not match approved proposal")

    proposal = validate_patch_proposal(context.repo_root, patch, cfg=cfg)
    if not proposal["applicable"]:
        raise CodingMutationError("ERROR_CODING_MUTATION_PATCH_NOT_APPLICABLE", proposal.get("git_apply_detail") or "patch rejected")
    if proposal["dirty_overlap"]:
        raise CodingMutationError("ERROR_CODING_MUTATION_DIRTY_OVERLAP", ",".join(proposal["dirty_overlap"]))

    expected = _normalize_expected_preimages(expected_preimages)
    if [row["path"] for row in expected] != proposal["patch_paths"]:
        raise CodingMutationError("ERROR_CODING_MUTATION_CANDIDATE_DRIFT", "approved preimage path set does not equal current patch candidate set")
    observed_preimages = sorted((_current_preimage(context.repo_root, row["path"]) for row in expected), key=lambda item: item["path"])
    if observed_preimages != expected:
        raise CodingMutationError("ERROR_CODING_MUTATION_PREIMAGE_DRIFT", "one or more approved patch preimages changed")

    ok, detail = _git_apply(context.repo_root, patch_bytes)
    if not ok:
        raise CodingMutationError("ERROR_CODING_MUTATION_APPLY_FAILED", detail or "git apply failed")

    postimages = sorted((_current_preimage(context.repo_root, path) for path in proposal["patch_paths"]), key=lambda item: item["path"])
    after = inspect_repository(context.repo_root, cfg=cfg)
    before_dirty = set(before["dirty_paths"])
    after_dirty = set(after["dirty_paths"])
    newly_dirty = sorted(after_dirty - before_dirty)
    unexpected_new_dirty = sorted(set(newly_dirty) - set(proposal["patch_paths"]))

    base_receipt = {
        "schema_version": CODING_MUTATION_SCHEMA_VERSION,
        "repo_root": str(context.repo_root),
        "head_sha": before["head_sha"],
        "branch": before["branch"],
        "patch_sha256": observed_patch_sha256,
        "patch_paths": proposal["patch_paths"],
        "preimages": observed_preimages,
        "postimages": postimages,
        "newly_dirty_paths": newly_dirty,
        "unexpected_new_dirty_paths": unexpected_new_dirty,
    }
    if after["head_sha"] != before["head_sha"]:
        _rollback_and_raise(context.repo_root, patch_bytes, expected, base_receipt, "mutation changed repository HEAD")
    if unexpected_new_dirty:
        _rollback_and_raise(context.repo_root, patch_bytes, expected, base_receipt, "mutation produced unexpected dirty paths")

    return {
        **base_receipt,
        "status": "CODING_MUTATION_APPLIED",
        "rollback_attempted": False,
        "rollback_succeeded": None,
        "mutation_performed": True,
        "invariants": {
            "head_revalidated": True,
            "head_conserved_after_mutation": True,
            "patch_hash_revalidated": True,
            "preimages_revalidated": True,
            "dirty_overlap_absent": True,
            "candidate_set_preserved": True,
            "unexpected_new_dirty_absent": True,
        },
        "limitations": [
            "this transaction applies a pre-approved Git patch only; arbitrary shell mutation is not exposed",
            "HEAD is intentionally unchanged because the transaction does not commit",
            "unrelated pre-existing dirty paths are preserved and excluded from patch targets",
            "rollback is best-effort and rollback failure remains explicit material residue",
        ],
        "falsification_criteria": [
            "reject HEAD drift before mutation",
            "reject patch-byte drift",
            "reject preimage drift or candidate-set drift",
            "reject dirty overlap with patch targets",
            "rollback if HEAD changes or mutation introduces paths outside the approved candidate set",
        ],
    }
