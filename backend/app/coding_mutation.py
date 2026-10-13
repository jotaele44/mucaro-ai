from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .coding_patch import validate_patch_proposal
from .coding_workspace import CodingWorkspaceError, inspect_repository, resolve_repository
from .config import Settings, settings
from .local_tools import resolve_local_tool
from .permissions import is_within_path

CODING_MUTATION_SCHEMA_VERSION = "mucaro-coding-mutation/1.0"
CODING_FILE_EDIT_SCHEMA_VERSION = "mucaro-coding-file-edit/1.0"
DEFAULT_FILE_EDIT_MAX_BYTES = 2 * 1024 * 1024
MAX_FILE_EDIT_BYTES = 8 * 1024 * 1024


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


def _normalize_repo_file_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PATH_INVALID", "path must be a canonical POSIX repository-relative file path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PATH_INVALID", path)
    normalized = pure.as_posix()
    if normalized != path:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PATH_NONCANONICAL", path)
    if pure.parts[0] == ".git":
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_GIT_INTERNAL", path)
    return normalized


def _resolve_file_edit_target(repo_root: Path, path: str) -> Path:
    normalized = _normalize_repo_file_path(path)
    target = repo_root.joinpath(*PurePosixPath(normalized).parts)
    cursor = repo_root
    for part in PurePosixPath(normalized).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CodingMutationError("ERROR_CODING_FILE_EDIT_SYMLINK", normalized)
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PARENT_MISSING", normalized) from exc
    if not parent.is_dir() or not is_within_path(parent, repo_root):
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PATH_BOUNDARY", normalized)
    return target


def _valid_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _atomic_write_file(target: Path, raw: bytes, *, mode: int) -> None:
    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".mucaro-file-edit-", dir=str(target.parent))
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
        temp_name = None
    except OSError as exc:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_WRITE", str(exc)) from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def _rollback_file_edit_and_raise(
    repo_root: Path,
    target: Path,
    *,
    path: str,
    expected_preimage: dict[str, Any],
    original_bytes: bytes | None,
    original_mode: int | None,
    before_head: str,
    before_dirty: set[str],
    receipt: dict[str, Any],
    detail: str,
    cfg: Settings,
) -> None:
    rollback_error: str | None = None
    try:
        if expected_preimage["state"] == "MISSING":
            if target.exists() or target.is_symlink():
                target.unlink()
        elif expected_preimage["state"] == "EXISTS":
            if original_bytes is None or original_mode is None:
                raise OSError("original bytes or mode unavailable")
            _atomic_write_file(target, original_bytes, mode=original_mode)
        else:
            raise OSError("unsupported preimage state")
    except (OSError, CodingMutationError) as exc:
        rollback_error = str(exc)

    restored = _current_preimage(repo_root, path)
    try:
        after_rollback = inspect_repository(repo_root, cfg=cfg)
    except CodingWorkspaceError as exc:  # fail closed: repository status itself is part of rollback proof
        after_rollback = {"head_sha": None, "dirty_paths": []}
        rollback_error = rollback_error or str(exc)
    rollback_succeeded = bool(
        rollback_error is None
        and restored == expected_preimage
        and after_rollback.get("head_sha") == before_head
        and set(after_rollback.get("dirty_paths") or []) == before_dirty
    )
    receipt.update(
        {
            "rollback_attempted": True,
            "rollback_succeeded": rollback_succeeded,
            "rollback_detail": rollback_error,
            "status": "CODING_FILE_EDIT_ROLLED_BACK" if rollback_succeeded else "CODING_FILE_EDIT_ROLLBACK_FAILED",
        }
    )
    raise CodingMutationError(receipt["status"], detail, receipt=receipt)


def apply_file_edit_transaction(
    repo_root: str | Path,
    path: str,
    operation: str,
    *,
    expected_head_sha: str,
    expected_preimage: dict[str, Any],
    content: str | None = None,
    expected_content_sha256: str | None = None,
    max_bytes: int = DEFAULT_FILE_EDIT_MAX_BYTES,
    cfg: Settings = settings,
) -> dict[str, Any]:
    if operation not in {"CREATE", "REPLACE", "DELETE"}:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_OPERATION", "operation must be CREATE, REPLACE, or DELETE")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1 or max_bytes > MAX_FILE_EDIT_BYTES:
        raise CodingMutationError(
            "ERROR_CODING_FILE_EDIT_MAX_BYTES",
            f"max_bytes must be an integer within 1..{MAX_FILE_EDIT_BYTES}",
        )

    context = resolve_repository(repo_root, cfg)
    normalized_path = _normalize_repo_file_path(path)
    target = _resolve_file_edit_target(context.repo_root, normalized_path)
    before = inspect_repository(context.repo_root, cfg=cfg)
    if before["head_sha"] != expected_head_sha:
        raise CodingMutationError(
            "ERROR_CODING_FILE_EDIT_HEAD_DRIFT",
            f"expected {expected_head_sha}, observed {before['head_sha']}",
        )

    before_dirty = set(before.get("dirty_paths") or [])
    if normalized_path in before_dirty:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_DIRTY_OVERLAP", normalized_path)

    if not isinstance(expected_preimage, dict):
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PREIMAGE_REQUIRED", "expected_preimage must be an object")
    expected = _normalize_expected_preimages([expected_preimage])[0]
    if expected["path"] != normalized_path:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PREIMAGE_PATH_DRIFT", normalized_path)
    if expected["state"] == "NON_FILE":
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PREIMAGE_INVALID", "file edits cannot target NON_FILE preimages")
    if operation == "CREATE" and expected["state"] != "MISSING":
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_STATE_CONFLICT", "CREATE requires a MISSING preimage")
    if operation in {"REPLACE", "DELETE"} and expected["state"] != "EXISTS":
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_STATE_CONFLICT", f"{operation} requires an EXISTS preimage")

    observed = _current_preimage(context.repo_root, normalized_path)
    if observed != expected:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_PREIMAGE_DRIFT", normalized_path)

    proposed_bytes: bytes | None = None
    observed_content_sha256: str | None = None
    if operation in {"CREATE", "REPLACE"}:
        if not isinstance(content, str):
            raise CodingMutationError("ERROR_CODING_FILE_EDIT_CONTENT_REQUIRED", f"{operation} requires UTF-8 text content")
        proposed_bytes = content.encode("utf-8")
        if len(proposed_bytes) > max_bytes:
            raise CodingMutationError("ERROR_CODING_FILE_EDIT_CONTENT_TOO_LARGE", f"{len(proposed_bytes)}>{max_bytes}")
        observed_content_sha256 = hashlib.sha256(proposed_bytes).hexdigest()
        if not _valid_sha256(expected_content_sha256) or observed_content_sha256 != expected_content_sha256:
            raise CodingMutationError("ERROR_CODING_FILE_EDIT_CONTENT_DRIFT", "content SHA-256 does not match approved content")
    elif content is not None or expected_content_sha256 is not None:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_DELETE_CONTENT", "DELETE must not carry replacement content")

    original_bytes: bytes | None = None
    original_mode: int | None = None
    if expected["state"] == "EXISTS":
        try:
            original_bytes = target.read_bytes()
            original_mode = target.stat().st_mode & 0o7777
        except OSError as exc:
            raise CodingMutationError("ERROR_CODING_FILE_EDIT_FILE_READ", normalized_path) from exc

    try:
        if operation == "DELETE":
            target.unlink()
        else:
            if proposed_bytes is None:
                raise CodingMutationError(
                    "ERROR_CODING_FILE_EDIT_CONTENT_REQUIRED",
                    f"{operation} reached mutation without an approved UTF-8 postimage",
                )
            mode = original_mode if original_mode is not None else 0o644
            _atomic_write_file(target, proposed_bytes, mode=mode)
    except OSError as exc:
        raise CodingMutationError("ERROR_CODING_FILE_EDIT_WRITE", str(exc)) from exc

    postimage = _current_preimage(context.repo_root, normalized_path)
    after = inspect_repository(context.repo_root, cfg=cfg)
    after_dirty = set(after.get("dirty_paths") or [])
    newly_dirty = sorted(after_dirty - before_dirty)
    unrelated_before = before_dirty - {normalized_path}
    unrelated_after = after_dirty - {normalized_path}

    expected_postimage = (
        {"path": normalized_path, "state": "MISSING", "byte_size": None, "sha256": None}
        if operation == "DELETE"
        else {
            "path": normalized_path,
            "state": "EXISTS",
            "byte_size": len(proposed_bytes or b""),
            "sha256": observed_content_sha256,
        }
    )
    receipt = {
        "schema_version": CODING_FILE_EDIT_SCHEMA_VERSION,
        "repo_root": str(context.repo_root),
        "head_sha": before["head_sha"],
        "branch": before["branch"],
        "operation": operation,
        "path": normalized_path,
        "preimage": observed,
        "postimage": postimage,
        "expected_content_sha256": expected_content_sha256,
        "observed_content_sha256": observed_content_sha256,
        "newly_dirty_paths": newly_dirty,
        "unrelated_dirty_paths_before": sorted(unrelated_before),
        "unrelated_dirty_paths_after": sorted(unrelated_after),
    }

    violations: list[str] = []
    if after["head_sha"] != before["head_sha"]:
        violations.append("repository HEAD changed")
    if postimage != expected_postimage:
        violations.append("postimage did not match the approved mutation")
    if unrelated_after != unrelated_before:
        violations.append("unrelated dirty-path set changed")
    if set(newly_dirty) - {normalized_path}:
        violations.append("unexpected dirty paths were introduced")
    if violations:
        _rollback_file_edit_and_raise(
            context.repo_root,
            target,
            path=normalized_path,
            expected_preimage=expected,
            original_bytes=original_bytes,
            original_mode=original_mode,
            before_head=before["head_sha"],
            before_dirty=before_dirty,
            receipt=receipt,
            detail="; ".join(violations),
            cfg=cfg,
        )

    return {
        **receipt,
        "status": "CODING_FILE_EDIT_APPLIED",
        "mutation_performed": True,
        "rollback_attempted": False,
        "rollback_succeeded": None,
        "invariants": {
            "head_revalidated": True,
            "head_conserved_after_mutation": True,
            "canonical_repository_relative_path": True,
            "git_internal_path_blocked": True,
            "symlink_target_blocked": True,
            "preimage_revalidated": True,
            "content_hash_revalidated": operation in {"CREATE", "REPLACE"},
            "target_dirty_overlap_absent": True,
            "unrelated_dirty_paths_preserved": True,
            "postimage_verified": True,
        },
        "limitations": [
            "one UTF-8 text file is mutated per approved call",
            "directories, symlinks, Git internals, and arbitrary filesystem paths are not editable",
            "the transaction does not execute shell commands and does not commit",
            "CREATE requires an existing parent directory; directory creation is intentionally separate",
            "rollback failure remains explicit material residue",
        ],
        "falsification_criteria": [
            "reject HEAD, path, preimage, or proposed-content drift before mutation",
            "reject target overlap with pre-existing dirty work",
            "reject noncanonical paths, path escape, symlink traversal, and .git mutation",
            "rollback if HEAD changes, the postimage mismatches, or unrelated dirty state changes",
        ],
    }
