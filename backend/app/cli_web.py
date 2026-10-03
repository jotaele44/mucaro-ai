"""`mucaro web` command group: governed, approval-gated web fetch and search.

Network access always requires --yes (or an interactive confirmation). Fetches
use the existing robots/SSRF/redirect/snapshot policy; live search additionally
needs --allow-live-search and a Brave API key in the environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .config import Settings
from .web_browsing import (
    WebBrowsingError,
    build_web_browse_session_policy,
    build_web_fetch_policy,
    fetch_web_browse_session,
    fetch_web_snapshot,
)
from .web_search import WebSearchError, search_web_brave

WEB_ERRORS = (WebBrowsingError, WebSearchError)


def _confirm(args: argparse.Namespace, summary: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise WebBrowsingError("ERROR_WEB_CONFIRMATION_REQUIRED", f"{summary}. Re-run with --yes to approve network access.")
    print(f"{summary}. Proceed? [y/N] ", end="", file=sys.stderr)
    if sys.stdin.readline().strip().lower() not in {"y", "yes"}:
        raise WebBrowsingError("ERROR_WEB_APPROVAL_DENIED", "Operator declined network access.")


def _run(action: Any, args: argparse.Namespace) -> int:
    try:
        print(json.dumps(action(), indent=2, ensure_ascii=False))
        return 0
    except WEB_ERRORS as exc:
        print(json.dumps({"status": exc.status, "detail": exc.detail}, indent=2), file=sys.stderr)
        return 2


def _fetch_arguments(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "allow_private_hosts": args.allow_private_hosts,
        "ignore_robots": args.ignore_robots,
        "max_bytes": args.max_bytes,
        "timeout_seconds": args.timeout,
    }


def run_web_fetch(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        _confirm(args, f"Fetch {args.url}")
        return fetch_web_snapshot(args.url, cfg=cfg, policy=build_web_fetch_policy(_fetch_arguments(args)))

    return _run(action, args)


def run_web_session(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        _confirm(args, f"Fetch {len(args.urls)} operator-supplied URL(s)")
        return fetch_web_browse_session(
            args.urls,
            purpose=args.purpose,
            cfg=cfg,
            fetch_policy=build_web_fetch_policy(_fetch_arguments(args)),
            session_policy=build_web_browse_session_policy({"max_pages": args.max_pages, "require_same_host": args.same_host}),
        )

    return _run(action, args)


def run_web_search(args: argparse.Namespace, cfg: Settings) -> int:
    def action() -> dict[str, Any]:
        _confirm(args, f"Send the query to the live Brave Search API: {args.query!r}")
        return search_web_brave(args.query, cfg=cfg, count=args.count, allow_live_search=args.allow_live_search)

    return _run(action, args)


def register_web_commands(subcommands: Any) -> None:
    web = subcommands.add_parser("web", help="Governed, approval-gated web fetch, browse session, and search.")
    sub = web.add_subparsers(dest="web_command", required=True)

    def add(name: str, help_text: str, handler: Any) -> argparse.ArgumentParser:
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("--yes", action="store_true", help="Approve network access without a prompt.")
        parser.set_defaults(handler=handler)
        return parser

    def fetch_options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--allow-private-hosts", action="store_true", help="Permit loopback/private hosts (off by default).")
        parser.add_argument("--ignore-robots", action="store_true", help="Skip robots.txt enforcement (off by default).")
        parser.add_argument("--max-bytes", type=int, default=None, help="Response size ceiling.")
        parser.add_argument("--timeout", type=int, default=None, help="Timeout in seconds.")

    fetch = add("fetch", "Fetch one URL as an immutable, hashed snapshot.", run_web_fetch)
    fetch.add_argument("url")
    fetch_options(fetch)

    session = add("session", "Fetch operator-supplied URLs as one bounded browse session.", run_web_session)
    session.add_argument("urls", nargs="+")
    session.add_argument("--purpose", default="", help="Why these pages are being read.")
    session.add_argument("--max-pages", type=int, default=5)
    session.add_argument("--same-host", action="store_true", help="Require all URLs to share a host.")
    fetch_options(session)

    search = add("search", "Search via Brave (needs --allow-live-search and the API key env var).", run_web_search)
    search.add_argument("query")
    search.add_argument("--count", type=int, default=10)
    search.add_argument("--allow-live-search", action="store_true", help="Explicitly permit the live network call.")
