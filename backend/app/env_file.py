"""Minimal, dependency-free reader for the project ``.env`` file.

The launcher and dev scripts merge these values into the environment they pass to
the backend process. Values already present in the environment always win, and
blank values (``KEY=``) are skipped so the ``.env.example`` placeholders stay inert.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    # Unquoted values may carry a trailing " # comment".
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines, ignoring comments, blanks, and malformed lines."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        key, sep, raw_value = stripped.partition("=")
        key = key.strip()
        if not sep or not _KEY_PATTERN.match(key):
            continue
        value = _strip_value(raw_value)
        if value:
            values[key] = value
    return values


def merge_env_file(env: Mapping[str, str], path: Path) -> dict[str, str]:
    """Return a copy of ``env`` with ``.env`` values added for keys not already set."""
    merged = dict(env)
    for key, value in read_env_file(path).items():
        merged.setdefault(key, value)
    return merged
