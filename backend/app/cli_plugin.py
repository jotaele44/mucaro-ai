"""`mucaro plugin` command group: install, list and remove hash-pinned plugin bundles.

Installing prints everything the plugin contributes, then asks. Nothing is executed at install.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .operator_audit import CLI_ACTOR, OperatorAuditError, approval_mode, audit_decision, run_audited
from .plugins import (
    SHA256_HEX,
    PluginError,
    describe,
    ensure_installable,
    install_plugin,
    list_plugins,
    plugins_root,
    read_source,
    remove_plugin,
)


def _run(action: Any) -> int:
    try:
        print(json.dumps(action(), indent=2, ensure_ascii=False))
        return 0
    except (PluginError, OperatorAuditError) as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2), file=sys.stderr)
        return 2


def _confirm(args: argparse.Namespace, summary: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise PluginError("ERROR_PLUGIN_CONFIRMATION_REQUIRED", f"{summary}. Re-run with --yes after reviewing it.")
    print(f"{summary}. Proceed? [y/N] ", end="", file=sys.stderr)
    if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
        raise PluginError("ERROR_PLUGIN_APPROVAL_DENIED", "Operator declined.")


def _approve(args: argparse.Namespace, cfg: Settings, tool_name: str, tool_args: dict[str, Any], summary: str) -> str:
    """Ask the operator, audit the decision (including a refusal) in the hash-chained tool log, and return the call id."""
    mode = approval_mode(args.yes)
    try:
        _confirm(args, summary)
    except PluginError as exc:
        refused_mode = "denied_non_tty" if exc.status == "ERROR_PLUGIN_CONFIRMATION_REQUIRED" else mode
        audit_decision(cfg, tool_name, tool_args, approved=False, requested_by=CLI_ACTOR, mode=refused_mode)
        raise
    return audit_decision(cfg, tool_name, tool_args, approved=True, requested_by=CLI_ACTOR, mode=mode)


def register_plugin_commands(subcommands: Any) -> None:
    plugin = subcommands.add_parser("plugin", help="Install, list and remove hash-pinned plugin bundles (MCP servers, check profiles, agent guidance).")
    sub = plugin.add_subparsers(dest="plugin_command", required=True)

    inspect = sub.add_parser("inspect", help="Validate a plugin manifest and show everything it would contribute. Read-only: nothing is installed or executed.")
    inspect.add_argument("source", help="A mucaro-plugin.json file, or a directory containing one.")

    def run_inspect(args: argparse.Namespace, cfg: Settings) -> int:
        def action() -> dict[str, Any]:
            _, manifest, sha256 = read_source(args.source)
            return {
                "status": "PLUGIN_MANIFEST_OK",
                "name": manifest["name"],
                "version": manifest["version"],
                "manifest_sha256": sha256,
                "already_installed": (plugins_root(cfg) / manifest["name"]).exists(),
                "consent_surface": describe(manifest, sha256),
            }

        return _run(action)

    inspect.set_defaults(handler=run_inspect)

    install = sub.add_parser("install", help="Show everything a plugin contributes, then install it after approval. Nothing is executed.")
    install.add_argument("source", help="A mucaro-plugin.json file, or a directory containing one.")
    install.add_argument("--sha256", help="The manifest SHA-256 the publisher gave you; installation is refused if it differs.")
    install.add_argument("--yes", action="store_true", help="Approve installing without a prompt. The full contents are in the result's consent_surface (and on stderr at a terminal).")

    def run_install(args: argparse.Namespace, cfg: Settings) -> int:
        def action() -> dict[str, Any]:
            raw, manifest, sha256 = read_source(args.source)
            if args.sha256:
                if not SHA256_HEX.fullmatch(args.sha256):
                    raise PluginError("ERROR_PLUGIN_PIN_INVALID", "--sha256 must be 64 hex characters")
                if args.sha256.lower() != sha256:
                    raise PluginError("ERROR_PLUGIN_PIN_MISMATCH", f"expected manifest sha256 {args.sha256.lower()}, this manifest is {sha256}")
            ensure_installable(cfg, manifest["name"])
            surface = describe(manifest, sha256)
            if sys.stdin.isatty():
                # A person at a terminal sees what they are consenting to before the prompt. Scripts do not get free text on
                # stderr: an unattended refusal or failure is only a JSON error, and a --yes success reports `consent_surface`.
                print(surface, file=sys.stderr)
            tool_args = {"name": manifest["name"], "version": manifest["version"], "manifest_sha256": sha256}
            mode = approval_mode(args.yes)
            summary = f"Install plugin {manifest['name']} {manifest['version']} (review it first with `mucaro plugin inspect {args.source}`)"
            call_id = _approve(args, cfg, "plugin.install", tool_args, summary)
            result = run_audited(cfg, "plugin.install", call_id, CLI_ACTOR, lambda: install_plugin(cfg, raw, manifest, sha256, approval_mode=mode))
            return {**result, "consent_surface": surface}

        return _run(action)

    install.set_defaults(handler=run_install)

    listing = sub.add_parser("list", help="List installed plugins and verify each against its install receipt.")
    listing.set_defaults(handler=lambda args, cfg: _run(lambda: {"status": "PLUGINS_OK", "root": str(plugins_root(cfg)), "plugins": list_plugins(cfg)}))

    remove = sub.add_parser("remove", help="Remove an installed plugin after approval.")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="Approve removal without a prompt.")

    def run_remove(args: argparse.Namespace, cfg: Settings) -> int:
        def action() -> dict[str, Any]:
            call_id = _approve(args, cfg, "plugin.remove", {"name": args.name}, f"Remove plugin {args.name}")
            return run_audited(cfg, "plugin.remove", call_id, CLI_ACTOR, lambda: remove_plugin(cfg, args.name))

        return _run(action)

    remove.set_defaults(handler=run_remove)
