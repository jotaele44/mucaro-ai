"""Local plugin catalog operator CLI (no network or plugin execution)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .plugin_intake import MAX_PACKAGE_BYTES, PluginIntakeError, list_plugins, stage_plugin
from .permissions import DirectoryPermissionError, ensure_directory_permission

if TYPE_CHECKING:
    from .config import Settings


def main(argv: list[str] | None = None, *, cfg: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description="Múcaro local plugin package inspection; execution remains disabled")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List staged, disabled plugins and recheck archive integrity")
    stage = commands.add_parser("stage", help="Validate and hash-freeze an inert local plugin ZIP")
    stage.add_argument("path", type=Path)
    stage.add_argument("--expected-sha256", default=None)
    args = parser.parse_args(argv)
    if cfg is None:
        from .config import Settings
        cfg = Settings.load()
    try:
        if args.command == "list":
            result = list_plugins(cfg)
        else:
            # Check the directory grant before opening external paths; do not bypass
            # the canonical permission policy for a local CLI entrypoint.
            resolved = args.path.expanduser().resolve(strict=True)
            ensure_directory_permission(resolved, cfg)
            if not resolved.is_file():
                raise PluginIntakeError("ERROR_PLUGIN_SOURCE_NOT_FILE")
            with resolved.open("rb") as source:
                package = source.read(MAX_PACKAGE_BYTES + 1)
            result = stage_plugin(package, cfg=cfg, expected_sha256=args.expected_sha256, staged_by="local_cli")
    except (PluginIntakeError, DirectoryPermissionError, OSError) as exc:
        result = {"status": getattr(exc, "status", "ERROR_PLUGIN_INPUT_IO"), "detail": getattr(exc, "detail", type(exc).__name__)}
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
