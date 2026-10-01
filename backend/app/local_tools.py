from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

BUNDLED_TOOLS_ENV = "MUCARO_AI_BUNDLED_TOOLS_DIR"
BUNDLED_ONLY_ENV = "MUCARO_AI_BUNDLED_TOOLS_ONLY"

# Stable logical names keep callers independent of platform suffixes.
KNOWN_LOCAL_TOOLS = frozenset({"git", "tesseract", "pdftoppm", "docker", "podman"})


def frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_tools_dir() -> Path:
    override = os.getenv(BUNDLED_TOOLS_ENV)
    if override:
        return Path(override).expanduser().resolve()
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root).resolve() / "runtime" / "bin"
    # Source/developer layout. A checked-in or prepared runtime/bin directory can
    # exercise the exact same resolver without installing anything globally.
    return Path(__file__).resolve().parents[2] / "runtime" / "bin"


def bundled_only() -> bool:
    value = (os.getenv(BUNDLED_ONLY_ENV) or "").strip().lower()
    return frozen_runtime() or value in {"1", "true", "yes", "on"}


def _candidate_names(name: str) -> tuple[str, ...]:
    if os.name == "nt":
        return (name if name.lower().endswith(".exe") else f"{name}.exe",)
    return (name,)


def _usable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        return True
    return os.access(path, os.X_OK)


def resolve_local_tool(name: str, *, allow_host: bool | None = None) -> str | None:
    """Resolve a local executable without making packaged releases depend on PATH.

    Resolution order:
    1. Mucaro's bundled runtime/bin directory.
    2. Host PATH only in source/developer mode unless explicitly disabled.

    Frozen releases never fall back to PATH. This is deliberate: a packaged app
    must report a missing optional capability instead of asking the operator to
    install developer tools or silently using an uncontrolled host executable.
    """

    if name not in KNOWN_LOCAL_TOOLS:
        raise ValueError(f"unknown local tool: {name}")

    root = bundled_tools_dir()
    for candidate_name in _candidate_names(name):
        candidate = root / candidate_name
        if _usable_file(candidate):
            return str(candidate)

    host_allowed = not bundled_only() if allow_host is None else bool(allow_host)
    if not host_allowed:
        return None
    return shutil.which(name)


def local_tool_status(name: str, *, allow_host: bool | None = None) -> dict[str, Any]:
    resolved = resolve_local_tool(name, allow_host=allow_host)
    root = bundled_tools_dir()
    is_bundled = False
    if resolved:
        try:
            Path(resolved).resolve().relative_to(root)
            is_bundled = True
        except ValueError:
            is_bundled = False
    return {
        "tool": name,
        "available": resolved is not None,
        "path": resolved,
        "bundled": is_bundled,
        "bundled_only": bundled_only() if allow_host is None else not bool(allow_host),
        "bundle_root": str(root),
        "host_path_fallback_allowed": (not bundled_only()) if allow_host is None else bool(allow_host),
    }
