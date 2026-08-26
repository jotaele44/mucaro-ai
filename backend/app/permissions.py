from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings, settings
from .utils import read_json, utc_now, write_json

DIRECTORY_PERMISSIONS_NAME = "directory_permissions.json"


class DirectoryPermissionError(PermissionError):
    def __init__(self, status: str, detail: str, *, path: Path | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.path = path


def permission_ledger_path(cfg: Settings = settings) -> Path:
    return cfg.processed_dir / DIRECTORY_PERMISSIONS_NAME


def resolve_existing_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise DirectoryPermissionError("ERROR_INPUT_PATH_NOT_FOUND", str(resolved), path=resolved)
    return resolved


def directory_root_for(path: Path) -> Path:
    return path if path.is_dir() else path.parent


def is_within_path(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def is_project_internal(path: Path, cfg: Settings = settings) -> bool:
    return is_within_path(path, cfg.project_root.resolve())


def load_directory_grants(cfg: Settings = settings) -> list[dict[str, Any]]:
    payload = read_json(permission_ledger_path(cfg), [])
    if not isinstance(payload, list):
        raise DirectoryPermissionError(
            "ERROR_DIRECTORY_PERMISSION_LEDGER_INVALID",
            f"Permission ledger must contain a list: {permission_ledger_path(cfg)}",
        )
    grants: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict) and isinstance(item.get("canonical_root"), str):
            grants.append(item)
    return grants


def check_directory_permission(path: str | Path, cfg: Settings = settings) -> dict[str, Any]:
    resolved = resolve_existing_path(path)
    root = directory_root_for(resolved)
    if is_project_internal(resolved, cfg):
        return {
            "status": "PERMISSION_OK",
            "scope": "project",
            "path": str(resolved),
            "canonical_root": str(cfg.project_root.resolve()),
        }
    for grant in load_directory_grants(cfg):
        granted_root = Path(str(grant["canonical_root"])).expanduser().resolve()
        if is_within_path(resolved, granted_root):
            return {
                "status": "PERMISSION_OK",
                "scope": "granted_directory",
                "path": str(resolved),
                "canonical_root": str(granted_root),
                "grant": grant,
            }
    return {
        "status": "ERROR_DIRECTORY_PERMISSION_REQUIRED",
        "detail": f"Grant directory access before ingesting paths under: {root}",
        "path": str(resolved),
        "canonical_root": str(root),
    }


def ensure_directory_permission(path: str | Path, cfg: Settings = settings) -> dict[str, Any]:
    result = check_directory_permission(path, cfg)
    if result["status"] != "PERMISSION_OK":
        raise DirectoryPermissionError(result["status"], str(result["detail"]), path=Path(str(result["path"])))
    return result


def grant_directory(path: str | Path, cfg: Settings = settings, *, granted_by: str, reason: str | None = None) -> dict[str, Any]:
    resolved = resolve_existing_path(path)
    root = directory_root_for(resolved)
    if root.anchor == str(root):
        raise DirectoryPermissionError(
            "ERROR_DIRECTORY_PERMISSION_TOO_BROAD",
            "Refusing to grant filesystem root access.",
            path=root,
        )
    cfg.ensure_dirs()
    grants = load_directory_grants(cfg)
    canonical_root = str(root)
    grants = [grant for grant in grants if grant.get("canonical_root") != canonical_root]
    grant = {
        "status": "DIRECTORY_PERMISSION_GRANTED",
        "requested_path": str(resolved),
        "canonical_root": canonical_root,
        "granted_at": utc_now(),
        "granted_by": granted_by,
        "reason": reason or "",
    }
    grants.append(grant)
    grants.sort(key=lambda item: str(item.get("canonical_root", "")))
    write_json(permission_ledger_path(cfg), grants)
    return grant


def revoke_directory(path: str | Path, cfg: Settings = settings) -> dict[str, Any]:
    resolved = resolve_existing_path(path)
    root = directory_root_for(resolved)
    ledger = permission_ledger_path(cfg)
    grants = load_directory_grants(cfg)
    kept = [grant for grant in grants if grant.get("canonical_root") != str(root)]
    removed = len(grants) - len(kept)
    write_json(ledger, kept)
    return {
        "status": "DIRECTORY_PERMISSION_REVOKED" if removed else "DIRECTORY_PERMISSION_NOT_FOUND",
        "canonical_root": str(root),
        "removed": removed,
    }
