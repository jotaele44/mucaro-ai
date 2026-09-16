from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json
from .web_browsing import WebBrowsingError, WebFetchPolicy, fetch_web_snapshot, validate_web_fetch_url
from .web_discovery import WebDiscoveryError, discover_snapshot_links

WEB_TRAVERSAL_SCHEMA_VERSION = "mucaro-web-traversal/1.0"
DEFAULT_MAX_PAGES = 20
MAX_MAX_PAGES = 250
DEFAULT_MAX_DEPTH = 2
MAX_MAX_DEPTH = 8
DEFAULT_MAX_LINKS_PER_PAGE = 25
MAX_MAX_LINKS_PER_PAGE = 250


class WebTraversalError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise WebTraversalError("ERROR_WEB_TRAVERSAL_LIMIT", f"{label} must be an integer from {minimum} through {maximum}")
    return value


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _canonical_url(url: str) -> str:
    defragged, _ = urldefrag(url)
    return defragged


def traverse_web_bounded(
    seed_url: str,
    *,
    cfg: Settings = settings,
    fetch_policy: WebFetchPolicy | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_links_per_page: int = DEFAULT_MAX_LINKS_PER_PAGE,
    require_same_host: bool = True,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    max_pages = _bounded_int(max_pages, minimum=1, maximum=MAX_MAX_PAGES, label="max_pages")
    max_depth = _bounded_int(max_depth, minimum=0, maximum=MAX_MAX_DEPTH, label="max_depth")
    max_links_per_page = _bounded_int(
        max_links_per_page,
        minimum=1,
        maximum=MAX_MAX_LINKS_PER_PAGE,
        label="max_links_per_page",
    )
    fetch_policy = fetch_policy or WebFetchPolicy()
    if fetch_policy.allow_private_hosts:
        raise WebTraversalError(
            "ERROR_WEB_TRAVERSAL_PRIVATE_HOST_POLICY",
            "autonomous traversal may not enable private/local host fetching",
        )

    try:
        checked_seed = validate_web_fetch_url(seed_url, allow_private_hosts=False)
    except WebBrowsingError as exc:
        raise WebTraversalError(exc.status, exc.detail) from exc
    seed = str(checked_seed["fetch_url"])
    seed_host = str(checked_seed["hostname"])
    started_at = utc_now()
    queue: deque[dict[str, Any]] = deque([{"url": seed, "depth": 0, "parent_url": None}])
    queued: set[str] = {seed}
    attempted: set[str] = set()
    rows: list[dict[str, Any]] = []
    discovery_reports: list[dict[str, Any]] = []
    exclusion_rows: list[dict[str, Any]] = []
    duplicate_count = 0
    depth_deferred_count = 0
    page_limit_deferred_count = 0
    discovery_truncated_count = 0
    unresolved_count = 0

    while queue and len(attempted) < max_pages:
        node = queue.popleft()
        url = str(node["url"])
        if url in attempted:
            duplicate_count += 1
            continue
        attempted.add(url)
        row: dict[str, Any] = {
            "url": url,
            "depth": int(node["depth"]),
            "parent_url": node["parent_url"],
            "fetch_state": None,
            "snapshot_id": None,
            "snapshot_path": None,
            "snapshot_sha256": None,
            "http_status": None,
            "content_type": None,
            "discovery_state": "NOT_APPLICABLE",
            "selected_link_count": 0,
        }
        try:
            fetched = fetch_web_snapshot(url, cfg=cfg, policy=fetch_policy)
        except WebBrowsingError as exc:
            row["fetch_state"] = "FETCH_FAILED"
            row["error_status"] = exc.status
            row["error_detail"] = exc.detail
            unresolved_count += 1
            rows.append(row)
            continue

        snapshot = fetched.get("snapshot") or {}
        row.update(
            {
                "fetch_state": "FETCHED",
                "snapshot_id": snapshot.get("snapshot_id"),
                "snapshot_path": fetched.get("snapshot_path"),
                "snapshot_sha256": fetched.get("snapshot_sha256"),
                "final_url": snapshot.get("final_url"),
                "http_status": snapshot.get("http_status"),
                "content_type": snapshot.get("content_type"),
                "content_sha256": snapshot.get("content_sha256"),
            }
        )
        final_url = _canonical_url(str(snapshot.get("final_url") or url))
        if require_same_host and (urlparse(final_url).hostname or "").lower() != seed_host:
            row["discovery_state"] = "FINAL_URL_CROSS_HOST"
            exclusion_rows.append({"url": final_url, "reason": "FINAL_URL_CROSS_HOST", "parent_url": url})
            rows.append(row)
            continue
        if int(node["depth"]) >= max_depth:
            row["discovery_state"] = "MAX_DEPTH_REACHED"
            rows.append(row)
            continue
        if "html" not in str(snapshot.get("content_type") or "").lower():
            row["discovery_state"] = "NON_HTML"
            rows.append(row)
            continue

        snapshot_path_raw = fetched.get("snapshot_path")
        if not isinstance(snapshot_path_raw, str):
            row["discovery_state"] = "DISCOVERY_UNRESOLVED"
            row["discovery_error"] = "SNAPSHOT_PATH_MISSING"
            unresolved_count += 1
            rows.append(row)
            continue
        try:
            discovery = discover_snapshot_links(
                Path(snapshot_path_raw),
                require_same_host=require_same_host,
                max_selected=max_links_per_page,
            )
        except WebDiscoveryError as exc:
            row["discovery_state"] = "DISCOVERY_UNRESOLVED"
            row["discovery_error"] = exc.status
            unresolved_count += 1
            rows.append(row)
            continue

        selected_urls = [str(candidate) for candidate in discovery.get("selected_urls") or []]
        row["discovery_state"] = str(discovery.get("scope_state") or discovery.get("status") or "DISCOVERED")
        row["selected_link_count"] = len(selected_urls)
        row["discovery_report_id"] = discovery.get("report_id")
        discovery_reports.append(
            {
                "parent_url": url,
                "report_id": discovery.get("report_id"),
                "status": discovery.get("status"),
                "scope_state": discovery.get("scope_state"),
                "selected_count": len(selected_urls),
                "state_counts": discovery.get("state_counts"),
            }
        )
        if discovery.get("scope_state") == "BOUNDED_TRUNCATED":
            discovery_truncated_count += 1

        for child in selected_urls:
            canonical = _canonical_url(child)
            host = (urlparse(canonical).hostname or "").lower()
            if require_same_host and host != seed_host:
                exclusion_rows.append({"url": canonical, "reason": "CROSS_HOST", "parent_url": url})
                continue
            if canonical in attempted or canonical in queued:
                duplicate_count += 1
                exclusion_rows.append({"url": canonical, "reason": "DUPLICATE", "parent_url": url})
                continue
            child_depth = int(node["depth"]) + 1
            if child_depth > max_depth:
                depth_deferred_count += 1
                exclusion_rows.append({"url": canonical, "reason": "MAX_DEPTH", "parent_url": url})
                continue
            queue.append({"url": canonical, "depth": child_depth, "parent_url": url})
            queued.add(canonical)
        rows.append(row)

    if queue:
        remaining = list(queue)
        page_limit_deferred_count = len(remaining)
        exclusion_rows.extend(
            {"url": str(node["url"]), "reason": "MAX_PAGES", "parent_url": node["parent_url"]}
            for node in remaining
        )

    fetched_count = sum(row["fetch_state"] == "FETCHED" for row in rows)
    failed_count = sum(row["fetch_state"] == "FETCH_FAILED" for row in rows)
    truncated = bool(page_limit_deferred_count or discovery_truncated_count)
    scope_state = "BOUNDED_TRUNCATED" if truncated else "BOUNDED_EXHAUSTED"
    status = "WEB_TRAVERSAL_WARN" if unresolved_count or truncated else "WEB_TRAVERSAL_OK"
    completed_at = utc_now()
    traversal_id = stable_id(
        "webtraversal",
        seed,
        str(max_pages),
        str(max_depth),
        str(max_links_per_page),
        started_at,
    )
    receipt: dict[str, Any] = {
        "schema_version": WEB_TRAVERSAL_SCHEMA_VERSION,
        "traversal_id": traversal_id,
        "status": status,
        "scope_state": scope_state,
        "seed_url": seed,
        "seed_host": seed_host,
        "started_at": started_at,
        "completed_at": completed_at,
        "parameters": {
            "max_pages": max_pages,
            "max_depth": max_depth,
            "max_links_per_page": max_links_per_page,
            "require_same_host": require_same_host,
            "discovery_source": "frozen_static_html_anchor_tags",
            "rendered_javascript_considered": False,
            "search_engine_used": False,
        },
        "fetch_policy": {
            "timeout_seconds": fetch_policy.timeout_seconds,
            "max_bytes": fetch_policy.max_bytes,
            "allow_private_hosts": fetch_policy.allow_private_hosts,
            "respect_robots": fetch_policy.respect_robots,
            "snapshot_http_errors": fetch_policy.snapshot_http_errors,
            "cite_http_error_pages": fetch_policy.cite_http_error_pages,
            "cache_mode": fetch_policy.cache_mode,
        },
        "counts": {
            "attempted_pages": len(rows),
            "fetched_pages": fetched_count,
            "failed_pages": failed_count,
            "unique_attempted_urls": len(attempted),
            "duplicate_candidates": duplicate_count,
            "depth_deferred": depth_deferred_count,
            "page_limit_deferred": page_limit_deferred_count,
            "discovery_truncated_pages": discovery_truncated_count,
            "unresolved": unresolved_count,
            "exclusions": len(exclusion_rows),
        },
        "pages": rows,
        "discoveries": discovery_reports,
        "exclusions": exclusion_rows,
        "invariants": {
            "attempt_accounting_closed": len(rows) == fetched_count + failed_count,
            "attempted_urls_unique": len(attempted) == len(rows),
            "page_ceiling_honored": len(rows) <= max_pages,
            "depth_ceiling_honored": all(int(row["depth"]) <= max_depth for row in rows),
            "private_hosts_disabled": fetch_policy.allow_private_hosts is False,
            "exhaustion_not_claimed_when_truncated": scope_state != "BOUNDED_EXHAUSTED" or not truncated,
            "static_html_scope_explicit": True,
        },
        "limitations": [
            "Traversal follows only links present in exact frozen static HTML anchor tags.",
            "JavaScript-rendered, lazy-loaded, form, sitemap, API, and search-engine discoveries remain outside this scope.",
            "BOUNDED_EXHAUSTED means the declared queue was drained without page/discovery truncation; it is not a site-wide or internet-wide exhaustion claim.",
            "Fetch failures remain explicit unresolved residue and do not disappear from arithmetic.",
        ],
        "falsification_criteria": [
            "never exceed max_pages, max_depth, or per-page selected-link ceilings",
            "never enable private/local-host traversal",
            "never label a truncated traversal BOUNDED_EXHAUSTED",
            "retain duplicate, depth, page-limit, cross-host and unresolved states explicitly",
            "do not infer rendered-browser or automated-search coverage from static traversal",
        ],
    }
    receipt["receipt_sha256"] = _stable_hash(receipt)
    out_dir = cfg.export_dir / "web_traversals"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{traversal_id}.json"
    write_json(path, receipt)
    return {
        "status": status,
        "traversal_id": traversal_id,
        "receipt_path": str(path),
        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "receipt": receipt,
    }
