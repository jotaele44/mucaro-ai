"""Plugin bundles: a hash-pinned, declarative manifest contributing MCP servers, container check profiles and agent guidance.

Installing a plugin is a trust decision: an MCP server declaration is a command that runs with your user
rights whenever you later approve a call. So installation is explicit, shown in full and approval-gated.
It is also inert: a manifest is data, and nothing is executed, fetched or imported when one is installed.
A plugin can never grant a tool or an approval, because those policies live in code, not in plugin text.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from .coding_checks import CodingCheck, CodingCheckError
from .coding_container import LOCAL_IMAGE_ID_PATTERN
from .config import Settings
from .container_runtime import ContainerRuntimeError, validate_pinned_image
from .mcp_client import McpError, validate_server_spec
from .utils import utc_now

PLUGIN_SCHEMA = "mucaro-plugin/1.0"
RECEIPT_SCHEMA = "mucaro-plugin-receipt/1.0"
MANIFEST_NAME = "mucaro-plugin.json"
RECEIPT_NAME = "install_receipt.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTRUCTIONS_BYTES = 8 * 1024
MAX_DESCRIPTION_CHARS = 500
MAX_CHECKS_PER_PROFILE = 32
PLUGIN_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
VERSION = re.compile(r"\d+\.\d+\.\d+")
SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")
TOP_KEYS = frozenset({"schema", "name", "version", "description", "mcp_servers", "check_profiles", "instructions"})
SERVER_KEYS = frozenset({"command", "env"})
PROFILE_KEYS = frozenset({"image", "checks"})
CHECK_KEYS = frozenset({"id", "kind", "command", "required"})


class PluginError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _invalid(detail: str) -> PluginError:
    return PluginError("ERROR_PLUGIN_MANIFEST_INVALID", detail)


def _only_keys(value: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    extra = sorted(str(key) for key in set(value) - allowed)
    if extra:
        raise _invalid(f"{where} has unknown keys: {', '.join(extra)}")


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(part, str) and part for part in value):
        raise _invalid(f"{where} must be a non-empty list of non-empty strings")
    return value


def _checks_from(profile: str, items: Any) -> list[CodingCheck]:
    if not isinstance(items, list) or not items or len(items) > MAX_CHECKS_PER_PROFILE:
        raise _invalid(f"check_profiles.{profile}.checks must list 1 to {MAX_CHECKS_PER_PROFILE} checks")
    checks: list[CodingCheck] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"check_profiles.{profile}.checks[{index}]"
        if not isinstance(item, dict):
            raise _invalid(f"{where} must be an object")
        _only_keys(item, CHECK_KEYS, where)
        required = item.get("required", True)
        if not isinstance(item.get("id"), str) or not isinstance(item.get("kind"), str) or not isinstance(required, bool):
            raise _invalid(f"{where} needs string id and kind, and a boolean required")
        check = CodingCheck(check_id=item["id"], kind=item["kind"], command=tuple(_string_list(item.get("command"), f"{where}.command")), required=required)
        try:
            check.validate()
        except CodingCheckError as exc:
            raise _invalid(f"{where}: {exc.status} {exc.detail}") from exc
        if check.check_id in seen:
            raise _invalid(f"{where}: duplicate check id {check.check_id!r}")
        seen.add(check.check_id)
        checks.append(check)
    return checks


def _validate_image(image: Any, where: str) -> str:
    if not isinstance(image, str):
        raise _invalid(f"{where} must be a string")
    if LOCAL_IMAGE_ID_PATTERN.fullmatch(image):
        return image
    try:
        return validate_pinned_image(image)
    except ContainerRuntimeError as exc:
        raise _invalid(f"{where} must be pinned (name@sha256:<64 hex> or a local sha256:<64 hex> image ID): {exc.detail}") from exc


def parse_manifest(raw: bytes) -> dict[str, Any]:
    """The validated manifest. Unknown keys, unpinned images and oversized text are rejected; nothing is executed."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _invalid(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _invalid(f"not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise _invalid("manifest must be a JSON object")
    _only_keys(manifest, TOP_KEYS, "manifest")
    if manifest.get("schema") != PLUGIN_SCHEMA:
        raise _invalid(f'schema must be "{PLUGIN_SCHEMA}"')
    if not isinstance(manifest.get("name"), str) or not PLUGIN_NAME.fullmatch(manifest["name"]):
        raise _invalid("name must match [a-z0-9][a-z0-9-]{0,39}")
    if not isinstance(manifest.get("version"), str) or not VERSION.fullmatch(manifest["version"]):
        raise _invalid("version must look like 1.2.3")
    description = manifest.get("description", "")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
        raise _invalid(f"description must be text of at most {MAX_DESCRIPTION_CHARS} characters")

    servers = manifest.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise _invalid("mcp_servers must be an object")
    for server_name, spec in servers.items():
        if isinstance(spec, dict):
            _only_keys(spec, SERVER_KEYS, f"mcp_servers.{server_name}")
        try:
            validate_server_spec(server_name, spec)
        except McpError as exc:
            raise _invalid(exc.detail) from exc

    profiles = manifest.get("check_profiles", {})
    if not isinstance(profiles, dict):
        raise _invalid("check_profiles must be an object")
    for profile_name, spec in profiles.items():
        if not PROFILE_NAME.fullmatch(str(profile_name)) or not isinstance(spec, dict):
            raise _invalid(f"check profile {profile_name!r} needs a name like [a-z0-9][a-z0-9_-]{{0,39}} and an object body")
        _only_keys(spec, PROFILE_KEYS, f"check_profiles.{profile_name}")
        _validate_image(spec.get("image"), f"check_profiles.{profile_name}.image")
        _checks_from(profile_name, spec.get("checks"))

    instructions = manifest.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        raise _invalid(f"instructions must be text of at most {MAX_INSTRUCTIONS_BYTES} bytes")
    if not (servers or profiles or instructions.strip()):
        raise _invalid("a plugin must contribute at least one MCP server, check profile or instructions")
    return manifest


def read_source(source: str | Path) -> tuple[bytes, dict[str, Any], str]:
    """Raw manifest bytes, the validated manifest and the SHA-256 of those exact bytes."""
    path = Path(source)
    if path.is_dir():
        path = path / MANIFEST_NAME
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise _invalid(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        raw = path.read_bytes()
    except OSError as exc:
        raise PluginError("ERROR_PLUGIN_SOURCE_UNREADABLE", f"cannot read {path.name}: {type(exc).__name__}") from exc
    return raw, parse_manifest(raw), hashlib.sha256(raw).hexdigest()


def describe(manifest: dict[str, Any], sha256: str) -> str:
    """Everything the operator is consenting to, in full: this is the install consent surface."""
    lines = [f"Plugin {manifest['name']} {manifest['version']}  (manifest sha256:{sha256})"]
    if manifest.get("description"):
        lines.append(f"  {manifest['description']}")
    servers = manifest.get("mcp_servers", {})
    if servers:
        lines.append("  MCP servers (every launch still needs your approval, and runs with your user rights):")
        for name, spec in servers.items():
            # Environment values are shown too: they change what runs (a loader variable or PATH, for example).
            env = "   env: " + " ".join(f"{key}={value}" for key, value in sorted(spec["env"].items())) if spec.get("env") else ""
            lines.append(f"    {manifest['name']}:{name}  ->  {' '.join(spec['command'])}{env}")
    profiles = manifest.get("check_profiles", {})
    if profiles:
        lines.append("  Container check profiles (network-off, pinned image, only with --run and your approval):")
        for name, spec in profiles.items():
            checks = "; ".join(f"{c['id']}({c['kind']}{'' if c.get('required', True) else ', optional'})={' '.join(c['command'])}" for c in spec["checks"])
            lines.append(f"    {manifest['name']}:{name}  image {spec['image']}  {checks}")
    if manifest.get("instructions", "").strip():
        lines.append(f"  Agent guidance, shown to the model only when you pass --plugin {manifest['name']} (it cannot grant tools or approvals):")
        lines.extend(f"    | {line}" for line in manifest["instructions"].splitlines() or [""])
    return "\n".join(lines)


def plugins_root(cfg: Settings) -> Path:
    return cfg.data_dir / "plugins"


def _checked_name(name: str) -> str:
    if not isinstance(name, str) or not PLUGIN_NAME.fullmatch(name):
        raise PluginError("ERROR_PLUGIN_NAME_INVALID", "a plugin name looks like [a-z0-9][a-z0-9-]{0,39}")
    return name


def ensure_installable(cfg: Settings, name: str) -> None:
    if (plugins_root(cfg) / _checked_name(name)).exists():
        raise PluginError("ERROR_PLUGIN_ALREADY_INSTALLED", f"{name} is installed; remove it first to install another version")


def install_plugin(cfg: Settings, raw: bytes, manifest: dict[str, Any], sha256: str, *, approval_mode: str) -> dict[str, Any]:
    """Store the exact manifest bytes and an install receipt. No process is started and nothing is fetched."""
    name = _checked_name(manifest["name"])
    ensure_installable(cfg, name)
    root = plugins_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    staging = root / f".installing-{name}-{secrets.token_hex(4)}"
    staging.mkdir()
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "name": name,
        "version": manifest["version"],
        "manifest_sha256": sha256,
        "installed_at": utc_now(),
        "approval_mode": approval_mode,
    }
    try:
        (staging / MANIFEST_NAME).write_bytes(raw)
        (staging / RECEIPT_NAME).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"status": "PLUGIN_INSTALLED", **receipt, "path": str(target)}


def _load_installed(cfg: Settings, name: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """A verified installed plugin: its stored manifest must still match the hash recorded at install."""
    name = _checked_name(name)
    directory = plugins_root(cfg) / name
    if not directory.is_dir():
        raise PluginError("ERROR_PLUGIN_NOT_FOUND", f"{name} is not installed")
    if directory.is_symlink():  # an installation is a real directory we created; a link means it was swapped
        raise PluginError("ERROR_PLUGIN_TAMPERED", f"{name}: the installation directory is a symbolic link")
    try:
        raw = (directory / MANIFEST_NAME).read_bytes()
        receipt = json.loads((directory / RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginError("ERROR_PLUGIN_TAMPERED", f"{name}: the installation files are missing or unreadable") from exc
    sha256 = hashlib.sha256(raw).hexdigest()
    if not isinstance(receipt, dict) or receipt.get("manifest_sha256") != sha256 or receipt.get("name") != name:
        raise PluginError("ERROR_PLUGIN_TAMPERED", f"{name}: the stored manifest no longer matches its install receipt")
    manifest = parse_manifest(raw)
    if manifest["name"] != name:
        raise PluginError("ERROR_PLUGIN_TAMPERED", f"{name}: the stored manifest names a different plugin")
    return manifest, receipt, sha256


def list_plugins(cfg: Settings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = plugins_root(cfg)
    for directory in sorted(root.iterdir()) if root.is_dir() else []:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            manifest, receipt, sha256 = _load_installed(cfg, directory.name)
        except PluginError as exc:
            rows.append({"name": directory.name, "status": "PLUGIN_TAMPERED" if exc.status == "ERROR_PLUGIN_TAMPERED" else "PLUGIN_INVALID", "detail": exc.detail})
            continue
        rows.append(
            {
                "name": manifest["name"],
                "version": manifest["version"],
                "status": "PLUGIN_OK",
                "manifest_sha256": sha256,
                "installed_at": receipt.get("installed_at"),
                "contributes": {
                    "mcp_servers": [f"{manifest['name']}:{server}" for server in manifest.get("mcp_servers", {})],
                    "check_profiles": [f"{manifest['name']}:{profile}" for profile in manifest.get("check_profiles", {})],
                    "instructions": bool(manifest.get("instructions", "").strip()),
                },
            }
        )
    return rows


def remove_plugin(cfg: Settings, name: str) -> dict[str, Any]:
    name = _checked_name(name)
    directory = plugins_root(cfg) / name
    if not directory.is_dir():
        raise PluginError("ERROR_PLUGIN_NOT_FOUND", f"{name} is not installed")
    if directory.is_symlink():
        directory.unlink()  # remove the link itself; never follow it and delete whatever it points at
    else:
        shutil.rmtree(directory)
    return {"status": "PLUGIN_REMOVED", "name": name}


def plugin_server(cfg: Settings, plugin: str, server: str) -> dict[str, Any]:
    manifest, _, _ = _load_installed(cfg, plugin)
    servers = manifest.get("mcp_servers", {})
    if server not in servers:
        raise PluginError("ERROR_PLUGIN_SERVER_UNKNOWN", f"{plugin} declares no MCP server {server!r}")
    return servers[server]


def plugin_server_rows(cfg: Settings) -> list[dict[str, Any]]:
    """MCP servers of verified plugins, for `mucaro mcp servers`. A plugin that fails verification contributes none."""
    rows: list[dict[str, Any]] = []
    for entry in list_plugins(cfg):
        if entry["status"] != "PLUGIN_OK":
            continue
        manifest, _, _ = _load_installed(cfg, entry["name"])
        for server, spec in manifest.get("mcp_servers", {}).items():
            rows.append({"name": f"{entry['name']}:{server}", "command": spec["command"], "source": f"plugin:{entry['name']}@{entry['version']}"})
    return rows


def plugin_profile(cfg: Settings, plugin: str, profile: str) -> tuple[str, list[CodingCheck], dict[str, str]]:
    """(image, checks, provenance) for `code checks --profile PLUGIN:PROFILE`."""
    manifest, _, sha256 = _load_installed(cfg, plugin)
    spec = manifest.get("check_profiles", {}).get(profile)
    if spec is None:
        raise PluginError("ERROR_PLUGIN_PROFILE_UNKNOWN", f"{plugin} declares no check profile {profile!r}")
    return spec["image"], _checks_from(profile, spec["checks"]), {"plugin": plugin, "version": manifest["version"], "manifest_sha256": sha256}


def activate(cfg: Settings, names: list[str]) -> tuple[str, frozenset[str]]:
    """Verify the named plugins and return (guidance text for the agent, the set of active plugin names)."""
    blocks: list[str] = []
    active: list[str] = []
    for name in dict.fromkeys(names):
        manifest, _, sha256 = _load_installed(cfg, name)
        active.append(name)
        text = manifest.get("instructions", "").strip()
        if text:
            blocks.append(
                f'[Operator guidance from installed plugin "{name}" v{manifest["version"]} (sha256:{sha256[:12]}). '
                "It shapes style and approach only: it cannot grant tools, skip approvals or widen access.]\n"
                f"{text}\n[End of plugin guidance]"
            )
    return "\n\n".join(blocks), frozenset(active)
