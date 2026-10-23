from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .coding_workspace import inspect_repository, resolve_repository
from .config import Settings, settings
from .permissions import is_within_path

CODING_FILE_EDIT_SCHEMA_VERSION = "mucaro-coding-file-edit/1.0"
_ALLOWED_OPERATIONS = {"CREATE", "REPLACE", "DELETE"}


class CodingFileEditError(RuntimeError):
    def __init__(self, status: str, detail: str, *, receipt: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.receipt = receipt


def _canonical_relative_path(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PATH_REQUIRED", "path must be a non-empty repository-relative path")
    if "\\" in path:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PATH_INVALID", "path must use repository POSIX separators")
    pure = PurePosixPath(path)
    if pure.is_absolute() or path.startswith("/") or any(part in {"", ".", ".."} for part in pure.parts):
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PATH_INVALID", path)
    if pure.parts and pure.parts[0] == ".git":
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_GIT_METADATA_FORBIDDEN", path)
    canonical = pure.as_posix()
    if canonical in {"", "."}:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PATH_INVALID", path)
    return canonical


def _reject_symlink_components(repo_root: Path, relative_path: str) -> None:
    cursor = repo_root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_SYMLINK_FORBIDDEN", relative_path)


def _snapshot(repo_root: Path, relative_path: str) -> dict[str, Any]:
    target = repo_root / relative_path
    resolved_parent = target.parent.resolve()
    if not is_within_path(resolved_parent, repo_root):
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PATH_BOUNDARY", relative_path)
    _reject_symlink_components(repo_root, relative_path)
    if not target.exists():
        return {"path": relative_path, "state": "MISSING", "byte_size": None, "sha256": None, "mode": None}
    if not target.is_file():
        return {"path": relative_path, "state": "NON_FILE", "byte_size": None, "sha256": None, "mode": None}
    try:
        raw = target.read_bytes()
        mode = target.stat().st_mode & 0o777
    except OSError as exc:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_READ", relative_path) from exc
    return {
        "path": relative_path,
        "state": "EXISTS",
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": mode,
    }


def _atomic_write(target: Path, raw: bytes, *, mode: int | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".mucaro-edit-", dir=str(target.parent))
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _prune_empty_created_parents(repo_root: Path, target: Path) -> None:
    cursor = target.parent
    while cursor != repo_root and is_within_path(cursor, repo_root):
        try:
            cursor.rmdir()
        except OSError:
            break
        cursor = cursor.parent


def _validate_expected_preimage(
    observed: dict[str, Any],
    *,
    expected_state: str,
    expected_sha256: str | None,
) -> None:
    if expected_state not in {"EXISTS", "MISSING"}:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PREIMAGE_INVALID", "expected_preimage_state must be EXISTS or MISSING")
    if expected_state == "EXISTS":
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PREIMAGE_HASH_REQUIRED", "EXISTS preimages require a 64-character SHA-256")
    elif expected_sha256 is not None:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PREIMAGE_HASH_INVALID", "MISSING preimages must not carry a SHA-256")
    if observed["state"] != expected_state:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PREIMAGE_DRIFT", f"expected {expected_state}, observed {observed['state']}")
    if expected_state == "EXISTS" and observed["sha256"] != expected_sha256:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_PREIMAGE_DRIFT", "preimage SHA-256 changed")


def edit_repository_file_transaction(
    repo_root: str | Path,
    path: str,
    *,
    operation: str,
    expected_head_sha: str,
    expected_preimage_state: str,
    expected_preimage_sha256: str | None = None,
    content: str | None = None,
    expected_content_sha256: str | None = None,
    cfg: Settings = settings,
) -> dict[str, Any]:
    context = resolve_repository(repo_root, cfg)
    canonical_path = _canonical_relative_path(path)
    target = context.repo_root / canonical_path

    if operation not in _ALLOWED_OPERATIONS:
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_OPERATION_INVALID", str(operation))

    before = inspect_repository(context.repo_root, cfg=cfg)
    if before["head_sha"] != expected_head_sha:
        raise CodingFileEditError(
            "ERROR_CODING_FILE_EDIT_HEAD_DRIFT",
            f"expected {expected_head_sha}, observed {before['head_sha']}",
        )
    if canonical_path in set(before["dirty_paths"]):
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_DIRTY_TARGET", canonical_path)

    observed_preimage = _snapshot(context.repo_root, canonical_path)
    _validate_expected_preimage(
        observed_preimage,
        expected_state=expected_preimage_state,
        expected_sha256=expected_preimage_sha256,
    )

    if operation == "CREATE" and expected_preimage_state != "MISSING":
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_CREATE_PREIMAGE", "CREATE requires a MISSING preimage")
    if operation in {"REPLACE", "DELETE"} and expected_preimage_state != "EXISTS":
        raise CodingFileEditError("ERROR_CODING_FILE_EDIT_EXISTING_PREIMAGE", f"{operation} requires an EXISTS preimage")

    proposed_raw: bytes | None = None
    observed_content_sha256: str | None = None
    if operation in {"CREATE", "REPLACE"}:
        if not isinstance(content, str):
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_CONTENT_REQUIRED", f"{operation} requires UTF-8 text content")
        proposed_raw = content.encode("utf-8")
        observed_content_sha256 = hashlib.sha256(proposed_raw).hexdigest()
        if not isinstance(expected_content_sha256, str) or len(expected_content_sha256) != 64:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_CONTENT_HASH_REQUIRED", "content mutation requires expected_content_sha256")
        if observed_content_sha256 != expected_content_sha256:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_CONTENT_DRIFT", "content SHA-256 does not match approved content")
    else:
        if content is not None or expected_content_sha256 is not None:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_DELETE_CONTENT_FORBIDDEN", "DELETE must not carry content")

    original_raw = target.read_bytes() if observed_preimage["state"] == "EXISTS" else None
    original_mode = observed_preimage.get("mode")
    target_existed = target.exists()
    receipt: dict[str, Any] = {
        "schema_version": CODING_FILE_EDIT_SCHEMA_VERSION,
        "repo_root": str(context.repo_root),
        "head_sha": before["head_sha"],
        "branch": before["branch"],
        "operation": operation,
        "path": canonical_path,
        "preimage": observed_preimage,
        "expected_content_sha256": expected_content_sha256,
    }

    try:
        if operation == "DELETE":
            target.unlink()
        else:
            if proposed_raw is None:
                raise CodingFileEditError(
                    "ERROR_CODING_FILE_EDIT_INTERNAL_CONTENT",
                    "validated content bytes are unavailable for a content mutation",
                )
            _atomic_write(target, proposed_raw, mode=original_mode if operation == "REPLACE" else 0o644)

        postimage = _snapshot(context.repo_root, canonical_path)
        after = inspect_repository(context.repo_root, cfg=cfg)
        before_dirty = set(before["dirty_paths"])
        after_dirty = set(after["dirty_paths"])
        newly_dirty = sorted(after_dirty - before_dirty)
        unexpected_new_dirty = sorted(set(newly_dirty) - {canonical_path})
        receipt.update(
            {
                "postimage": postimage,
                "newly_dirty_paths": newly_dirty,
                "unexpected_new_dirty_paths": unexpected_new_dirty,
            }
        )

        expected_post_state = "MISSING" if operation == "DELETE" else "EXISTS"
        if after["head_sha"] != before["head_sha"]:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_POSTCONDITION", "mutation changed repository HEAD")
        if unexpected_new_dirty:
            raise CodingFileEditError(
                "ERROR_CODING_FILE_EDIT_POSTCONDITION",
                "mutation produced unexpected dirty paths",
            )
        if postimage["state"] != expected_post_state:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_POSTCONDITION", "postimage state mismatch")
        if operation in {"CREATE", "REPLACE"} and postimage["sha256"] != observed_content_sha256:
            raise CodingFileEditError("ERROR_CODING_FILE_EDIT_POSTCONDITION", "postimage SHA-256 mismatch")

    except Exception as exc:
        rollback_error: str | None = None
        try:
            if target_existed and original_raw is not None:
                _atomic_write(target, original_raw, mode=original_mode)
            elif target.exists():
                target.unlink()
                _prune_empty_created_parents(context.repo_root, target)
            restored = _snapshot(context.repo_root, canonical_path)
            rollback_succeeded = (
                restored["state"] == observed_preimage["state"]
                and restored.get("sha256") == observed_preimage.get("sha256")
            )
        except Exception as rollback_exc:  # pragma: no cover - materially reported residue
            rollback_succeeded = False
            rollback_error = str(rollback_exc)

        receipt.update(
            {
                "rollback_attempted": True,
                "rollback_succeeded": rollback_succeeded,
                "rollback_error": rollback_error,
                "status": "CODING_FILE_EDIT_ROLLED_BACK" if rollback_succeeded else "CODING_FILE_EDIT_ROLLBACK_FAILED",
            }
        )
        if isinstance(exc, CodingFileEditError):
            raise CodingFileEditError(receipt["status"], exc.detail, receipt=receipt) from exc
        raise CodingFileEditError(receipt["status"], str(exc), receipt=receipt) from exc

    return {
        **receipt,
        "status": "CODING_FILE_EDIT_APPLIED",
        "mutation_performed": True,
        "rollback_attempted": False,
        "rollback_succeeded": None,
        "invariants": {
            "head_revalidated": True,
            "head_conserved_after_mutation": True,
            "path_boundary_revalidated": True,
            "symlink_components_rejected": True,
            "preimage_revalidated": True,
            "target_was_not_dirty": True,
            "content_hash_revalidated": operation == "DELETE" or observed_content_sha256 == expected_content_sha256,
            "unexpected_new_dirty_absent": True,
            "postimage_verified": True,
        },
        "limitations": [
            "one repository-relative regular file is mutated per governed call",
            "only UTF-8 text CREATE/REPLACE and regular-file DELETE are supported",
            "the transaction does not commit and intentionally preserves repository HEAD",
            "arbitrary shell writes, symlink traversal, .git mutation, and dirty-target overwrite are not exposed",
        ],
        "falsification_criteria": [
            "reject HEAD drift, preimage drift, content-byte drift, dirty target state, or path-boundary ambiguity",
            "reject symlink components and Git metadata targets",
            "rollback if postimage, repository HEAD, or dirty-path containment postconditions fail",
        ],
    }
