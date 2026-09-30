"""Local-only plugin package intake. No extension code is imported or executed.

v1 is a deliberately staged, disabled catalog rather than a sandbox claim. A
separate reviewed executor and per-call policy gate are required for activation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from .archive_safety import ArchiveLimits, inspect_zip, member_manifest, read_member_limited
from .utils import canonical_sha256, read_json, update_json, utc_now

if TYPE_CHECKING:
    from .config import Settings

PLUGIN_API = "mucaro-plugin/1"
REGISTRY_FILE = "plugin_registry_v1.json"
MAX_PACKAGE_BYTES = 10 * 1024 * 1024
LIMITS = ArchiveLimits(max_members=128, max_member_bytes=256 * 1024, max_total_bytes=2 * 1024 * 1024, max_ratio=50.0)
ID_PATTERN = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+")
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
ALLOWED_EXTENSIONS = frozenset({".json", ".md", ".txt"})


class PluginIntakeError(ValueError):
    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(detail or status)
        self.status = status
        self.detail = detail or status


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise PluginIntakeError("ERROR_PLUGIN_DUPLICATE_JSON_KEY", key)
        obj[key] = value
    return obj


def _safe_paths(report: dict[str, Any]) -> set[str]:
    if report.get("status") != "ARCHIVE_SAFE":
        raise PluginIntakeError("ERROR_PLUGIN_ARCHIVE_UNSAFE", str(report.get("findings", report.get("status"))))
    seen: set[str] = set()
    paths: set[str] = set()
    for item in report["members"]:
        raw = item["path"]
        if item["is_dir"]:
            continue
        path = PurePosixPath(raw)
        normalized = unicodedata.normalize("NFC", raw)
        if raw != normalized or not raw.isascii() or raw.startswith("./") or "//" in raw or any(p in {".", ".."} for p in raw.split("/")):
            raise PluginIntakeError("ERROR_PLUGIN_PATH_NOT_CANONICAL", raw)
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise PluginIntakeError("ERROR_PLUGIN_UNSUPPORTED_PAYLOAD", raw)
        folded = normalized.casefold()
        if folded in seen:
            raise PluginIntakeError("ERROR_PLUGIN_CASE_COLLISION", raw)
        seen.add(folded)
        paths.add(raw)
    if "mucaro-plugin.json" not in paths:
        raise PluginIntakeError("ERROR_PLUGIN_MANIFEST_MISSING", "mucaro-plugin.json")
    return paths


def _validate_manifest(manifest: Any, members: set[str]) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise PluginIntakeError("ERROR_PLUGIN_MANIFEST_NOT_OBJECT")
    required = {"host_api", "plugin_id", "version", "name", "description", "capabilities", "contributions"}
    if set(manifest) != required:
        raise PluginIntakeError("ERROR_PLUGIN_MANIFEST_FIELDS", str(sorted(set(manifest) ^ required)))
    if manifest["host_api"] != PLUGIN_API:
        raise PluginIntakeError("ERROR_PLUGIN_API_UNSUPPORTED")
    if not isinstance(manifest["plugin_id"], str) or not ID_PATTERN.fullmatch(manifest["plugin_id"]) or len(manifest["plugin_id"]) > 120:
        raise PluginIntakeError("ERROR_PLUGIN_ID_INVALID")
    if not isinstance(manifest["version"], str) or not VERSION_PATTERN.fullmatch(manifest["version"]):
        raise PluginIntakeError("ERROR_PLUGIN_VERSION_INVALID")
    if not all(isinstance(manifest[k], str) and 0 < len(manifest[k]) <= 200 for k in ("name", "description")):
        raise PluginIntakeError("ERROR_PLUGIN_DESCRIPTION_INVALID")
    # Even a declared empty capability set conveys NO authority; future executable
    # support must use the established capability/distribution/approval engines.
    if manifest["capabilities"] != []:
        raise PluginIntakeError("ERROR_PLUGIN_CAPABILITY_EXECUTION_NOT_SUPPORTED")
    contributions = manifest["contributions"]
    if not isinstance(contributions, list) or len(contributions) > 32:
        raise PluginIntakeError("ERROR_PLUGIN_CONTRIBUTIONS_INVALID")
    seen: set[str] = set()
    for entry in contributions:
        if not isinstance(entry, dict) or set(entry) != {"kind", "path"} or entry["kind"] not in {"reference", "prompt_template", "schema"}:
            raise PluginIntakeError("ERROR_PLUGIN_CONTRIBUTION_INVALID")
        path = entry["path"]
        if not isinstance(path, str) or path not in members or path == "mucaro-plugin.json" or path in seen:
            raise PluginIntakeError("ERROR_PLUGIN_CONTRIBUTION_PATH_INVALID")
        expected = {"reference": {".md", ".txt"}, "prompt_template": {".md"}, "schema": {".json"}}[entry["kind"]]
        if PurePosixPath(path).suffix.lower() not in expected:
            raise PluginIntakeError("ERROR_PLUGIN_CONTRIBUTION_KIND_MISMATCH")
        seen.add(path)
    # Unreferenced content is rejected: every byte in the archive is accounted for.
    if members != seen | {"mucaro-plugin.json"}:
        raise PluginIntakeError("ERROR_PLUGIN_UNDECLARED_MEMBER", str(sorted(members - seen - {"mucaro-plugin.json"})))
    return manifest


def _read_registry(cfg: Settings) -> list[dict[str, Any]]:
    payload = read_json(cfg.processed_dir / REGISTRY_FILE, [])
    if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
        raise PluginIntakeError("ERROR_PLUGIN_REGISTRY_INVALID")
    required = {"plugin_id", "version", "archive_sha256", "state", "member_manifest_sha256", "member_manifest"}
    if any(not required <= set(x) or not isinstance(x["plugin_id"], str) or
           not isinstance(x["version"], str) or not isinstance(x["archive_sha256"], str) or
           not re.fullmatch(r"[a-f0-9]{64}", x["archive_sha256"]) or
           x["state"] != "STAGED_DISABLED" or not isinstance(x["member_manifest"], list)
           for x in payload):
        raise PluginIntakeError("ERROR_PLUGIN_REGISTRY_INVALID")
    identities = [(x.get("plugin_id"), x.get("version")) for x in payload]
    if len(identities) != len(set(identities)):
        raise PluginIntakeError("ERROR_PLUGIN_REGISTRY_DUPLICATES")
    return payload


def _check_frozen(cfg: Settings, row: dict[str, Any]) -> dict[str, Any]:
    expected = row["archive_sha256"]
    path = cfg.data_dir / "plugins" / "archives" / f"{expected}.zip"
    try:
        actual = None if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        actual = None
    return {**row, "integrity": "PASS" if actual == expected else "FAIL"}


def list_plugins(cfg: Settings) -> dict[str, Any]:
    plugins = [_check_frozen(cfg, row) for row in _read_registry(cfg)]
    return {"status": "PLUGIN_CATALOG_OK", "plugins": plugins, "count": len(plugins), "active_count": 0}


def stage_plugin(package: bytes, *, cfg: Settings, staged_by: str = "operator", expected_sha256: str | None = None) -> dict[str, Any]:
    if not isinstance(package, bytes) or not package or len(package) > MAX_PACKAGE_BYTES:
        raise PluginIntakeError("ERROR_PLUGIN_PACKAGE_SIZE")
    digest = hashlib.sha256(package).hexdigest()
    if expected_sha256 is not None and expected_sha256 != digest:
        raise PluginIntakeError("ERROR_PLUGIN_SOURCE_HASH_MISMATCH")
    cfg.ensure_dirs()
    archive_root = cfg.data_dir / "plugins" / "archives"
    archive_root.mkdir(parents=True, exist_ok=True)
    # Snapshot the exact supplied bytes before examining them. No archive extraction.
    fd, temporary = tempfile.mkstemp(prefix=".plugin-", suffix=".zip", dir=archive_root)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(package)
            handle.flush()
            os.fsync(handle.fileno())
        report = inspect_zip(temp, LIMITS)
        members = _safe_paths(report)
        with zipfile.ZipFile(temp) as archive:
            raw = read_member_limited(archive, "mucaro-plugin.json", 64 * 1024)
        try:
            manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PluginIntakeError("ERROR_PLUGIN_MANIFEST_JSON_INVALID", str(exc)) from exc
        manifest = _validate_manifest(manifest, members)
        member_rows = member_manifest(temp, LIMITS)
        if sum(row["bytes"] for row in member_rows) > LIMITS.max_total_bytes or {row["path"] for row in member_rows} != members:
            raise PluginIntakeError("ERROR_PLUGIN_MEMBER_ACCOUNTING")
        manifest_digest = canonical_sha256(member_rows)
        candidate = {
            "plugin_id": manifest["plugin_id"],
            "version": manifest["version"],
            "name": manifest["name"],
            "description": manifest["description"],
            "host_api": PLUGIN_API,
            "capabilities": [],
            "contributions": manifest["contributions"],
            "archive_sha256": digest,
            "archive_bytes": len(package),
            "member_count": len(member_rows),
            "member_manifest_sha256": manifest_digest,
            "member_manifest": member_rows,
            "state": "STAGED_DISABLED",
            "execution_supported": False,
            "staged_at": utc_now(),
            "staged_by": staged_by,
        }
        target = archive_root / f"{digest}.zip"
        registry = cfg.processed_dir / REGISTRY_FILE
        # Entire identity conflict check and write is under one existing utility lock.
        def add(rows: list[dict[str, Any]]) -> dict[str, Any]:
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise PluginIntakeError("ERROR_PLUGIN_REGISTRY_INVALID")
            keys = [(r.get("plugin_id"), r.get("version")) for r in rows]
            if len(keys) != len(set(keys)):
                raise PluginIntakeError("ERROR_PLUGIN_REGISTRY_DUPLICATES")
            for row in rows:
                if (row["plugin_id"], row["version"]) == (candidate["plugin_id"], candidate["version"]):
                    if row["archive_sha256"] == digest:
                        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                            raise PluginIntakeError("ERROR_PLUGIN_ARCHIVE_CORRUPT")
                        return {"status": "PLUGIN_ALREADY_STAGED", "plugin": row}
                    raise PluginIntakeError("ERROR_PLUGIN_VERSION_COLLISION")
            if target.is_symlink() or (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest):
                raise PluginIntakeError("ERROR_PLUGIN_ARCHIVE_CORRUPT")
            if not target.exists():
                # Link refuses to clobber an existing immutable identity; hard-link
                # and temp live on same filesystem. Registry commit occurs after it.
                try:
                    os.link(temp, target)
                except FileExistsError:
                    if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                        raise PluginIntakeError("ERROR_PLUGIN_ARCHIVE_CORRUPT")
            rows.append(candidate)
            rows.sort(key=lambda row: (row["plugin_id"], row["version"]))
            return {"status": "PLUGIN_STAGED_DISABLED", "plugin": candidate}
        return update_json(registry, [], add)
    finally:
        temp.unlink(missing_ok=True)
