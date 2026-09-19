from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .portable_runtime import PORTABLE_APP_NAME, PortableRuntimeError, verify_portable_macos_app

INSTALL_RECEIPT_SCHEMA_VERSION = "mucaro-portable-install/1.0"
DEFAULT_INSTALL_NAME = "Mucaro AI.app"
BACKUP_DIR_NAME = ".mucaro-rollback"


class PortableInstallError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_manifest(app_path: Path) -> tuple[dict[str, Any], Path]:
    path = app_path / "Contents" / "Resources" / "portable-manifest.json"
    if not path.is_file():
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_MANIFEST", str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_MANIFEST", str(exc)) from exc
    if not isinstance(payload, dict):
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_MANIFEST", "portable manifest must be an object")
    return payload, path


def _verify(app_path: Path, expected_head: str | None = None) -> dict[str, Any]:
    try:
        report = verify_portable_macos_app(app_path, expected_head=expected_head)
    except (PortableRuntimeError, OSError, json.JSONDecodeError) as exc:
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_VERIFY", str(exc)) from exc
    if report.get("status") != "PORTABLE_APP_VERIFIED":
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_VERIFY", ",".join(report.get("failed_checks") or []))
    return report


def _remove_tree(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise PortableInstallError("ERROR_PORTABLE_INSTALL_TARGET_TYPE", str(path))
        shutil.rmtree(path)


def install_portable_macos_app(
    source_app: Path,
    *,
    install_root: Path,
    install_name: str = DEFAULT_INSTALL_NAME,
) -> dict[str, Any]:
    source_app = source_app.resolve()
    install_root = install_root.expanduser().resolve()
    if not source_app.is_dir():
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_SOURCE", str(source_app))
    source_manifest, source_manifest_path = _portable_manifest(source_app)
    source_head = str(source_manifest.get("head_sha") or "")
    if not source_head:
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_HEAD", "source portable manifest lacks head_sha")
    source_verify = _verify(source_app, source_head)

    install_root.mkdir(parents=True, exist_ok=True)
    destination = install_root / install_name
    backup_root = install_root / BACKUP_DIR_NAME
    backup = backup_root / install_name
    staging = install_root / f".{install_name}.staging"

    if source_app == destination.resolve(strict=False):
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_SAME_PATH", str(source_app))
    _remove_tree(staging)

    previous: dict[str, Any] | None = None
    if destination.exists():
        previous_manifest, previous_manifest_path = _portable_manifest(destination)
        previous = {
            "head_sha": previous_manifest.get("head_sha"),
            "manifest_sha256": _sha256(previous_manifest_path),
        }
        _verify(destination, str(previous_manifest.get("head_sha") or "") or None)

    shutil.copytree(source_app, staging, symlinks=False)
    staged_verify = _verify(staging, source_head)

    backup_created = False
    try:
        if destination.exists():
            backup_root.mkdir(parents=True, exist_ok=True)
            _remove_tree(backup)
            destination.rename(backup)
            backup_created = True
        staging.rename(destination)
        installed_verify = _verify(destination, source_head)
    except Exception as exc:
        if destination.exists():
            _remove_tree(destination)
        if backup_created and backup.exists():
            backup.rename(destination)
        _remove_tree(staging)
        if isinstance(exc, PortableInstallError):
            raise
        raise PortableInstallError("ERROR_PORTABLE_INSTALL_TRANSACTION", str(exc)) from exc

    installed_manifest, installed_manifest_path = _portable_manifest(destination)
    receipt = {
        "schema_version": INSTALL_RECEIPT_SCHEMA_VERSION,
        "status": "PORTABLE_INSTALL_OK",
        "created_utc": _utc_now(),
        "source": {
            "app_path": str(source_app),
            "head_sha": source_head,
            "manifest_sha256": _sha256(source_manifest_path),
            "verification_status": source_verify["status"],
        },
        "installation": {
            "install_root": str(install_root),
            "app_path": str(destination),
            "head_sha": installed_manifest.get("head_sha"),
            "manifest_sha256": _sha256(installed_manifest_path),
            "verification_status": installed_verify["status"],
        },
        "previous_installation": previous,
        "rollback": {
            "available": backup_created and backup.exists(),
            "backup_path": str(backup) if backup_created else None,
        },
        "invariants": {
            "staging_verified_before_swap": staged_verify["status"] == "PORTABLE_APP_VERIFIED",
            "installed_head_matches_source": installed_manifest.get("head_sha") == source_head,
            "installed_manifest_matches_source": _sha256(installed_manifest_path) == _sha256(source_manifest_path),
        },
        "limitations": [
            "successful file transaction is not proof of launch behavior on a clean machine",
            "code signing and notarization remain separate gates",
            "rollback evidence is complete only when an actual previous installation was restored and reverified",
        ],
    }
    receipt_path = install_root / "mucaro-install-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = _sha256(receipt_path)
    return receipt


def rollback_portable_macos_app(
    *,
    install_root: Path,
    install_name: str = DEFAULT_INSTALL_NAME,
) -> dict[str, Any]:
    install_root = install_root.expanduser().resolve()
    destination = install_root / install_name
    backup = install_root / BACKUP_DIR_NAME / install_name
    if not backup.is_dir():
        raise PortableInstallError("ERROR_PORTABLE_ROLLBACK_UNAVAILABLE", str(backup))

    backup_manifest, backup_manifest_path = _portable_manifest(backup)
    backup_head = str(backup_manifest.get("head_sha") or "")
    _verify(backup, backup_head or None)

    displaced = install_root / f".{install_name}.rollback-displaced"
    _remove_tree(displaced)
    try:
        if destination.exists():
            destination.rename(displaced)
        backup.rename(destination)
        verified = _verify(destination, backup_head or None)
    except Exception as exc:
        if destination.exists():
            _remove_tree(destination)
        if displaced.exists():
            displaced.rename(destination)
        if isinstance(exc, PortableInstallError):
            raise
        raise PortableInstallError("ERROR_PORTABLE_ROLLBACK_TRANSACTION", str(exc)) from exc
    _remove_tree(displaced)

    installed_manifest, installed_manifest_path = _portable_manifest(destination)
    receipt = {
        "schema_version": INSTALL_RECEIPT_SCHEMA_VERSION,
        "status": "PORTABLE_ROLLBACK_OK",
        "created_utc": _utc_now(),
        "installation": {
            "app_path": str(destination),
            "head_sha": installed_manifest.get("head_sha"),
            "manifest_sha256": _sha256(installed_manifest_path),
            "verification_status": verified["status"],
        },
        "restored_from": {
            "head_sha": backup_head,
            "manifest_sha256": _sha256(backup_manifest_path) if backup_manifest_path.exists() else None,
        },
        "invariants": {
            "restored_head_matches_backup": installed_manifest.get("head_sha") == backup_head,
            "post_rollback_verified": verified["status"] == "PORTABLE_APP_VERIFIED",
        },
        "limitations": [
            "rollback transaction proves file-state restoration, not clean-machine launch behavior",
            "signed/notarized installer rollback remains outside this source-level transaction",
        ],
    }
    receipt_path = install_root / "mucaro-rollback-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = _sha256(receipt_path)
    return receipt
