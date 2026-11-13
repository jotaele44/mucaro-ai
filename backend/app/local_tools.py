from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

BUNDLED_TOOLS_ENV = "MUCARO_AI_BUNDLED_TOOLS_DIR"
BUNDLED_ONLY_ENV = "MUCARO_AI_BUNDLED_TOOLS_ONLY"

# Stable logical names keep callers independent of platform suffixes.
KNOWN_LOCAL_TOOLS = frozenset({"git", "tesseract", "pdftoppm", "docker", "podman"})

# On macOS, /usr/bin/git is a stub. Without the Command Line Tools or Xcode, running it opens the
# "install developer tools" dialog instead of running Git, so it only counts when those tools exist.
MACOS_DEVELOPER_STUBS = frozenset({"/usr/bin/git"})
MACOS_COMMAND_LINE_TOOLS = Path("/Library/Developer/CommandLineTools")


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


def macos_developer_tools_present() -> bool:
    """True when the developer directory behind the macOS /usr/bin stubs holds a real git.

    Never runs a stub: it checks files, and asks /usr/bin/xcode-select (a real program that never
    opens the install dialog) for the selected developer directory.
    """

    candidates: list[Path] = []
    developer_dir = os.getenv("DEVELOPER_DIR")
    if developer_dir:
        candidates.append(Path(developer_dir))
    candidates.append(MACOS_COMMAND_LINE_TOOLS)
    if any((candidate / "usr" / "bin" / "git").is_file() for candidate in candidates):
        return True
    try:
        proc = subprocess.run(
            ["/usr/bin/xcode-select", "-p"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    selected = proc.stdout.strip() if proc.returncode == 0 else ""
    return bool(selected) and (Path(selected) / "usr" / "bin" / "git").is_file()


def _host_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found and sys.platform == "darwin" and os.path.realpath(found) in MACOS_DEVELOPER_STUBS:
        if not macos_developer_tools_present():
            return None
    return found


def resolve_local_tool(name: str, *, allow_host: bool | None = None) -> str | None:
    """Resolve a local executable without making packaged releases depend on PATH.

    Resolution order:
    1. Mucaro's bundled runtime/bin directory.
    2. Host PATH only in source/developer mode unless explicitly disabled. On macOS the
       /usr/bin/git stub counts only when the developer tools are installed (see
       macos_developer_tools_present), so a source checkout never opens the install dialog.

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
    return _host_tool(name)


def resolve_tool_request(name: str, requested: str | None = None) -> str | None:
    """Resolve an optional caller-supplied executable under the same release policy."""

    if requested is None:
        return resolve_local_tool(name)
    if name not in KNOWN_LOCAL_TOOLS:
        raise ValueError(f"unknown local tool: {name}")

    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        # Logical names still flow through the controlled resolver.
        return resolve_local_tool(name)

    if not bundled_only():
        # Preserve explicit developer/test overrides. The eventual subprocess
        # invocation remains responsible for reporting an unusable path.
        return str(candidate)

    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not _usable_file(resolved):
        return None
    try:
        resolved.relative_to(bundled_tools_dir())
    except ValueError:
        return None
    return str(resolved)


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
