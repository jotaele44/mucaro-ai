"""Read-only certification boundary for independent local plugin manifests.

This module never imports or executes a plugin. A positive result authorizes a
specific byte snapshot for *subsequent* governed approval, not execution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any

from .capability_policy import CapabilityPolicy, KNOWN_CAPABILITIES, evaluate_capabilities

MANIFEST_NAME = "mucaro-plugin.json"
SCHEMA_VERSION = "mucaro-independent-plugin/1"
MAX_MANIFEST_BYTES = 65536
MAX_PLUGIN_FILES = 128
MAX_PLUGIN_DIRS = 128
MAX_MEMBER_PATH_LENGTH = 240
MAX_PLUGIN_BYTES = 10 * 1024 * 1024
MAX_STORE_CHILDREN = 256
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+\Z", re.ASCII)
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z", re.ASCII)
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z", re.ASCII)
_FIELDS = frozenset({"schema_version", "id", "version", "entrypoint", "capabilities", "network"})


class PluginBoundaryError(ValueError):
    def __init__(self, status: str, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


def _duplicate_key_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PluginBoundaryError("INVALID_DUPLICATE_JSON_KEY", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def _reject_symlink_ancestors(path: Path) -> None:
    if not path.is_absolute():
        raise PluginBoundaryError("INVALID_PLUGIN_ROOT", "Expected absolute path")
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise PluginBoundaryError("INVALID_PLUGIN_ROOT", "Symlinked ancestor or root is not allowed")


def _root_entry(path: Path, store: Path) -> None:
    # A plugin must be one immediate, real directory child of the operator's store.
    _reject_symlink_ancestors(store)
    _reject_symlink_ancestors(path)
    if not store.is_dir() or store.is_symlink():
        raise PluginBoundaryError("INVALID_PLUGIN_STORE", "Store must be an existing, non-symlink directory")
    if not path.is_absolute() or not store.is_absolute() or path.parent != store:
        raise PluginBoundaryError("INVALID_PLUGIN_ROOT", "Plugin must be an immediate child of the absolute store")
    if path.is_symlink() or not path.is_dir():
        raise PluginBoundaryError("INVALID_PLUGIN_ROOT", "Plugin root must be a real directory, not a symlink")


def _files(plugin: Path) -> tuple[list[dict[str, Any]], list[str]]:
    members: list[dict[str, Any]] = []
    directory_paths: list[str] = []
    total = 0
    directories = 0
    seen_portable_paths: dict[str, str] = {}
    for current, dirs, names in os.walk(plugin, topdown=True, followlinks=False):
        dirs.sort()
        names.sort()
        directories += len(dirs)
        directory_paths.extend((Path(current) / name).relative_to(plugin).as_posix() for name in dirs)
        if directories > MAX_PLUGIN_DIRS:
            raise PluginBoundaryError("PLUGIN_RESOURCE_LIMIT", "Directory count limit exceeded")
        for name in dirs + names:
            path = Path(current) / name
            relative = path.relative_to(plugin).as_posix()
            portable_key = unicodedata.normalize("NFC", relative).casefold()
            previous = seen_portable_paths.setdefault(portable_key, relative)
            if previous != relative:
                status = ("AMBIGUOUS_MEMBER_CASE" if previous.casefold() == relative.casefold()
                          else "AMBIGUOUS_MEMBER_UNICODE")
                raise PluginBoundaryError(status, "Member paths collide after portable path normalization")
            if (len(relative) > MAX_MEMBER_PATH_LENGTH or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                                                        for c in relative)):
                raise PluginBoundaryError("INVALID_PLUGIN_MEMBER", "Unsafe or excessive member path")
            kind = path.lstat().st_mode
            if stat.S_ISLNK(kind) or not (stat.S_ISDIR(kind) or stat.S_ISREG(kind)):
                raise PluginBoundaryError(
                    "INVALID_PLUGIN_MEMBER", f"Symlink/special member: {path.relative_to(plugin)}"
                )
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(plugin).as_posix()
            size = path.stat().st_size
            total += size
            if size > MAX_PLUGIN_BYTES or total > MAX_PLUGIN_BYTES or len(members) >= MAX_PLUGIN_FILES:
                raise PluginBoundaryError("PLUGIN_RESOURCE_LIMIT", "File count or total byte limit exceeded")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if path.stat().st_size != size:
                raise PluginBoundaryError("PLUGIN_MUTATED_DURING_SCAN", relative)
            members.append({"path": relative, "bytes": size, "sha256": digest.hexdigest()})
    return sorted(members, key=lambda row: row["path"]), sorted(directory_paths)


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise PluginBoundaryError("INVALID_MANIFEST_SIZE", "Manifest exceeds 64 KiB")
    try:
        obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_key_pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON constant")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, PluginBoundaryError):
            raise
        raise PluginBoundaryError("INVALID_MANIFEST_JSON", "Manifest is not strict UTF-8 JSON") from exc
    if not isinstance(obj, dict) or set(obj) != _FIELDS:
        raise PluginBoundaryError("INVALID_MANIFEST_FIELDS", "Manifest must contain exactly the v1 fields")
    if obj["schema_version"] != SCHEMA_VERSION:
        raise PluginBoundaryError("INVALID_SCHEMA_VERSION", "Unsupported manifest version")
    if not isinstance(obj["id"], str) or not _PLUGIN_ID.fullmatch(obj["id"]):
        raise PluginBoundaryError("INVALID_PLUGIN_ID", "ID must be exact lower-case ASCII dotted namespace")
    if not isinstance(obj["version"], str) or not _VERSION.fullmatch(obj["version"]):
        raise PluginBoundaryError("INVALID_PLUGIN_VERSION", "Version must be exact numeric major.minor.patch")
    entry = obj["entrypoint"]
    if not isinstance(entry, str) or not _FILENAME.fullmatch(entry) or not entry.endswith(".py"):
        raise PluginBoundaryError("INVALID_ENTRYPOINT", "Entrypoint must be a root-local Python filename")
    caps = obj["capabilities"]
    if not isinstance(caps, list) or any(not isinstance(c, str) for c in caps) or len(caps) != len(set(caps)):
        raise PluginBoundaryError("INVALID_CAPABILITIES", "Capabilities must be unique strings")
    if any(c not in KNOWN_CAPABILITIES for c in caps):
        raise PluginBoundaryError("UNKNOWN_CAPABILITY", "Manifest declares an unknown capability")
    if obj["network"] not in ("none", "remote"):
        raise PluginBoundaryError("INVALID_NETWORK_CLASS", "Network class must be none or remote")
    if ("network.public_http" in caps) != (obj["network"] == "remote"):
        raise PluginBoundaryError("NETWORK_CLASS_CONTRADICTION", "Network declaration conflicts with capabilities")
    return obj


def inspect_plugin(path: Path, store: Path, *, policy: CapabilityPolicy, offline: bool = True) -> dict[str, Any]:
    """Create a byte-bound inspection receipt. No code is imported or run."""
    if not isinstance(offline, bool):
        raise PluginBoundaryError("INVALID_OFFLINE_FLAG", "Offline mode must be boolean")
    _root_entry(path, store)
    members, directory_paths = _files(path)
    manifest_member = next((m for m in members if m["path"] == MANIFEST_NAME), None)
    if manifest_member is None:
        raise PluginBoundaryError("MANIFEST_MISSING", MANIFEST_NAME)
    raw = (path / MANIFEST_NAME).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_member["sha256"]:
        raise PluginBoundaryError("PLUGIN_MUTATED_DURING_SCAN", MANIFEST_NAME)
    manifest = _parse_manifest(raw)
    if not any(m["path"] == manifest["entrypoint"] for m in members):
        raise PluginBoundaryError("ENTRYPOINT_MISSING", manifest["entrypoint"])
    decision = evaluate_capabilities(policy=policy, declared_capabilities=manifest["capabilities"],
                                     requested_capabilities=manifest["capabilities"])
    denied_offline = offline and manifest["network"] != "none"
    status = "INSPECTED_FOR_APPROVAL" if decision.authorized and not denied_offline else "BLOCKED"
    policy_snapshot = {"policy_id": policy.policy_id, "allowed": sorted(policy.allowed),
                       "denied": sorted(policy.denied)}
    body = {"schema_version": SCHEMA_VERSION, "plugin_id": manifest["id"], "version": manifest["version"],
            "root_name": path.name, "manifest_sha256": manifest_member["sha256"], "members": members,
            "directories": directory_paths, "policy_id": policy.policy_id,
            "policy_sha256": _canonical_sha256(policy_snapshot),
            "requested_capabilities": sorted(manifest["capabilities"]),
            "network": manifest["network"], "offline": offline}
    return {"status": status, "snapshot_sha256": _canonical_sha256(body), "snapshot": body,
            "capability_decision": decision.as_dict(), "offline_denied": denied_offline,
            "execution_certified": False}


def authorize_snapshot(path: Path, store: Path, *, policy: CapabilityPolicy, expected_sha256: str,
                       explicit_operator_approval: bool, offline: bool = True) -> dict[str, Any]:
    """Re-inspect immediately before handing off to a governed runtime."""
    if not explicit_operator_approval:
        raise PluginBoundaryError("APPROVAL_REQUIRED", "Explicit operator approval is required")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise PluginBoundaryError("INVALID_EXPECTED_HASH", "Expected snapshot must be a lower-case SHA-256")
    result = inspect_plugin(path, store, policy=policy, offline=offline)
    if result["snapshot_sha256"] != expected_sha256:
        raise PluginBoundaryError("STALE_PLUGIN_SNAPSHOT", "Plugin bytes, policy, or offline setting changed")
    if result["status"] != "INSPECTED_FOR_APPROVAL":
        raise PluginBoundaryError("PLUGIN_NOT_AUTHORIZED", "Policy or offline gate denied plugin")
    audited = inspect_store(store, policy=policy, offline=offline)
    if not audited["arithmetic_closes"]:
        raise PluginBoundaryError("PLUGIN_STORE_CONSERVATION_FAILURE", "Store audit did not close")
    if result["snapshot"]["plugin_id"] in audited["duplicate_ids"]:
        raise PluginBoundaryError("DUPLICATE_PLUGIN_ID", "Store has multiple candidates for the declared plugin ID")
    current = [row for row in audited["candidates"] if row.get("snapshot", {}).get("root_name") == path.name]
    if len(current) != 1 or current[0].get("snapshot_sha256") != expected_sha256:
        raise PluginBoundaryError("STALE_PLUGIN_SNAPSHOT", "Store re-inspection changed the target")
    if current[0]["status"] != "INSPECTED_FOR_APPROVAL":
        raise PluginBoundaryError("PLUGIN_NOT_AUTHORIZED", "Store re-inspection denied the target")
    return {**result, "status": "APPROVED_SNAPSHOT_ONLY", "execution_certified": False}


def inspect_store(store: Path, *, policy: CapabilityPolicy, offline: bool = True) -> dict[str, Any]:
    if not isinstance(offline, bool):
        raise PluginBoundaryError("INVALID_OFFLINE_FLAG", "Offline mode must be boolean")
    _reject_symlink_ancestors(store)
    if not store.is_absolute() or not store.is_dir() or store.is_symlink():
        raise PluginBoundaryError("INVALID_PLUGIN_STORE", "Store must be a real absolute directory")
    receipts: list[dict[str, Any]] = []
    excluded_regular_files = 0
    children = sorted(store.iterdir(), key=lambda p: p.name)
    if len(children) > MAX_STORE_CHILDREN:
        raise PluginBoundaryError("PLUGIN_STORE_RESOURCE_LIMIT", "Plugin store child count limit exceeded")
    for child in children:
        if child.is_file() and not child.is_symlink():
            excluded_regular_files += 1
            continue
        try:
            receipt = inspect_plugin(child, store, policy=policy, offline=offline)
        except PluginBoundaryError as exc:
            receipt = {"root_name": child.name, "status": "INVALID", "error_status": exc.status}
        receipts.append(receipt)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for receipt in receipts:
        plugin_id = receipt.get("snapshot", {}).get("plugin_id")
        if plugin_id is not None:
            by_id.setdefault(plugin_id, []).append(receipt)
    duplicates = sorted(k for k, v in by_id.items() if len(v) > 1)
    for plugin_id in duplicates:
        for receipt in by_id[plugin_id]:
            receipt["pre_collision_status"] = receipt["status"]
            receipt["status"] = "UNRESOLVED_DUPLICATE_PLUGIN_ID"
    counts: dict[str, int] = {}
    for receipt in receipts:
        counts[receipt["status"]] = counts.get(receipt["status"], 0) + 1
    return {"status": "PLUGIN_STORE_AUDITED", "source_count": len(children),
            "retained_count": len(receipts), "excluded_count": excluded_regular_files,
            "counts": counts, "arithmetic_closes": (len(children) == len(receipts) + excluded_regular_files
                and sum(counts.values()) == len(receipts)), "duplicate_ids": duplicates,
            "candidates": receipts}
