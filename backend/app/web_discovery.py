from __future__ import annotations

import base64
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from .utils import read_json, stable_id, utc_now, write_json

WEB_DISCOVERY_SCHEMA_VERSION = "mucaro_web_discovery_v1"
DEFAULT_MAX_CANDIDATES = 1000
MAX_MAX_CANDIDATES = 10000
DEFAULT_MAX_SELECTED = 25
MAX_MAX_SELECTED = 250


class WebDiscoveryError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class _AnchorParser(HTMLParser):
    def __init__(self, *, max_candidates: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_candidates = max_candidates
        self.hrefs: list[str] = []
        self.anchor_count = 0
        self.overflow_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.anchor_count += 1
        href = next((value for key, value in attrs if key.lower() == "href" and value is not None), None)
        if href is None:
            return
        if len(self.hrefs) >= self.max_candidates:
            self.overflow_count += 1
            return
        self.hrefs.append(href)


def _positive_bounded(value: int, *, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > maximum:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_LIMIT", f"{label} must be an integer from 1 through {maximum}")
    return value


def _load_snapshot(snapshot_path: Path) -> tuple[dict[str, Any], bytes, str]:
    if not snapshot_path.exists() or not snapshot_path.is_file():
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_SNAPSHOT_NOT_FOUND", str(snapshot_path))
    raw_snapshot = snapshot_path.read_bytes()
    snapshot_sha256 = hashlib.sha256(raw_snapshot).hexdigest()
    payload = read_json(snapshot_path, None)
    if not isinstance(payload, dict):
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_SNAPSHOT_INVALID", "snapshot JSON must be an object")
    content_base64 = payload.get("content_base64")
    if not isinstance(content_base64, str) or not content_base64:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_CONTENT_MISSING", "snapshot does not contain frozen response bytes")
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_CONTENT_BASE64", "snapshot content_base64 is invalid") from exc
    expected_content_sha = payload.get("content_sha256")
    observed_content_sha = hashlib.sha256(content).hexdigest()
    if not isinstance(expected_content_sha, str) or expected_content_sha != observed_content_sha:
        raise WebDiscoveryError(
            "ERROR_WEB_DISCOVERY_CONTENT_HASH",
            f"snapshot content hash mismatch: expected={expected_content_sha} observed={observed_content_sha}",
        )
    expected_size = payload.get("content_size_bytes")
    if not isinstance(expected_size, int) or expected_size != len(content):
        raise WebDiscoveryError(
            "ERROR_WEB_DISCOVERY_CONTENT_SIZE",
            f"snapshot content size mismatch: expected={expected_size} observed={len(content)}",
        )
    return payload, content, snapshot_sha256


def _decode_html(content: bytes, content_type: str) -> str:
    lowered = content_type.lower()
    if "html" not in lowered:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_NOT_HTML", f"content type is not HTML: {content_type}")
    charset = "utf-8"
    if "charset=" in lowered:
        charset = lowered.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        return content.decode(charset, errors="strict")
    except LookupError as exc:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_CHARSET", f"unknown charset: {charset}") from exc
    except UnicodeDecodeError as exc:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_DECODE", f"HTML bytes do not decode as declared charset {charset}") from exc


def _classify_href(raw_href: str, *, base_url: str, require_same_host: bool) -> dict[str, Any]:
    raw = raw_href.strip()
    row: dict[str, Any] = {
        "raw_href": raw_href,
        "normalized_href": raw,
        "resolved_url": None,
        "fragment_removed": False,
        "state": "EXCLUDED",
        "reason": None,
    }
    if not raw:
        row["reason"] = "EMPTY_HREF"
        return row
    lowered = raw.lower()
    for prefix, reason in (
        ("javascript:", "JAVASCRIPT_SCHEME"),
        ("mailto:", "MAILTO_SCHEME"),
        ("tel:", "TEL_SCHEME"),
        ("data:", "DATA_SCHEME"),
    ):
        if lowered.startswith(prefix):
            row["reason"] = reason
            return row
    resolved = urljoin(base_url, raw)
    defragged, fragment = urldefrag(resolved)
    parsed = urlparse(defragged)
    if parsed.scheme not in {"http", "https"}:
        row["reason"] = "UNSUPPORTED_SCHEME"
        return row
    if not parsed.hostname:
        row["reason"] = "HOST_MISSING"
        return row
    base_host = (urlparse(base_url).hostname or "").lower()
    target_host = parsed.hostname.lower()
    if require_same_host and target_host != base_host:
        row.update({"resolved_url": defragged, "fragment_removed": bool(fragment), "reason": "CROSS_HOST"})
        return row
    row.update(
        {
            "resolved_url": defragged,
            "fragment_removed": bool(fragment),
            "state": "CANDIDATE",
            "reason": None,
            "hostname": target_host,
        }
    )
    return row


def discover_snapshot_links(
    snapshot_path: Path,
    *,
    require_same_host: bool = False,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_selected: int = DEFAULT_MAX_SELECTED,
    output_path: Path | None = None,
) -> dict[str, Any]:
    max_candidates = _positive_bounded(max_candidates, maximum=MAX_MAX_CANDIDATES, label="max_candidates")
    max_selected = _positive_bounded(max_selected, maximum=MAX_MAX_SELECTED, label="max_selected")
    snapshot, content, snapshot_sha256 = _load_snapshot(snapshot_path)
    base_url = snapshot.get("final_url") or snapshot.get("requested_url")
    content_type = str(snapshot.get("content_type") or "")
    if not isinstance(base_url, str) or not base_url:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_BASE_URL", "snapshot lacks requested/final URL")
    html = _decode_html(content, content_type)

    parser = _AnchorParser(max_candidates=max_candidates)
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise WebDiscoveryError("ERROR_WEB_DISCOVERY_HTML_PARSE", str(exc)) from exc

    classified = [
        {"candidate_index": index, **_classify_href(href, base_url=base_url, require_same_host=require_same_host)}
        for index, href in enumerate(parser.hrefs)
    ]
    first_index_by_url: dict[str, int] = {}
    unique_candidates: list[dict[str, Any]] = []
    duplicate_count = 0
    for row in classified:
        url = row.get("resolved_url")
        if row["state"] != "CANDIDATE" or not isinstance(url, str):
            continue
        if url in first_index_by_url:
            row["state"] = "DUPLICATE"
            row["reason"] = f"DUPLICATE_OF:{first_index_by_url[url]}"
            duplicate_count += 1
            continue
        first_index_by_url[url] = row["candidate_index"]
        unique_candidates.append(row)

    selected = unique_candidates[:max_selected]
    selected_indices = {row["candidate_index"] for row in selected}
    for row in unique_candidates[max_selected:]:
        row["state"] = "DEFERRED"
        row["reason"] = "MAX_SELECTED_LIMIT"

    excluded_count = sum(row["state"] == "EXCLUDED" for row in classified)
    deferred_count = sum(row["state"] == "DEFERRED" for row in classified)
    unique_candidate_count = len(unique_candidates)
    selected_urls = [str(row["resolved_url"]) for row in selected]
    truncated = parser.overflow_count > 0
    scope_state = "BOUNDED_TRUNCATED" if truncated else "FROZEN_HTML_ANCHOR_EXHAUSTED"
    created_at = utc_now()
    report_id = stable_id("webdiscovery", snapshot_sha256, str(require_same_host), str(max_candidates), str(max_selected), created_at)
    report = {
        "schema_version": WEB_DISCOVERY_SCHEMA_VERSION,
        "report_id": report_id,
        "status": "WEB_DISCOVERY_WARN" if truncated else "WEB_DISCOVERY_OK",
        "created_at": created_at,
        "scope_state": scope_state,
        "source_snapshot": {
            "path": str(snapshot_path),
            "snapshot_sha256": snapshot_sha256,
            "snapshot_id": snapshot.get("snapshot_id"),
            "requested_url": snapshot.get("requested_url"),
            "final_url": snapshot.get("final_url"),
            "retrieval_utc": snapshot.get("retrieval_utc"),
            "content_sha256": snapshot.get("content_sha256"),
            "content_size_bytes": snapshot.get("content_size_bytes"),
            "content_type": content_type,
        },
        "parameters": {
            "require_same_host": require_same_host,
            "max_candidates": max_candidates,
            "max_selected": max_selected,
            "discovery_source": "frozen_static_html_anchor_tags",
            "network_fetch_performed": False,
            "rendered_javascript_considered": False,
        },
        "candidates": classified,
        "selected_urls": selected_urls,
        "state_counts": {
            "anchors_observed": parser.anchor_count,
            "hrefs_parsed": len(parser.hrefs),
            "overflow_href_count": parser.overflow_count,
            "unique_candidate_count": unique_candidate_count,
            "duplicate_count": duplicate_count,
            "excluded_count": excluded_count,
            "deferred_count": deferred_count,
            "selected_count": len(selected_urls),
        },
        "invariants": {
            "parsed_accounting_closed": len(classified) == unique_candidate_count + duplicate_count + excluded_count,
            "selection_accounting_closed": unique_candidate_count == len(selected_urls) + deferred_count,
            "selected_urls_unique": len(selected_urls) == len(set(selected_urls)),
            "selected_indices_preserved": selected_indices == {row["candidate_index"] for row in classified if row.get("resolved_url") in set(selected_urls) and row["state"] == "CANDIDATE"},
            "source_content_hash_verified": True,
            "source_content_size_verified": True,
        },
        "warnings": ["WEB_DISCOVERY_MAX_CANDIDATES_TRUNCATED"] if truncated else [],
        "limitations": [
            "exhaustion applies only to anchor tags present in the exact frozen static HTML bytes",
            "JavaScript-rendered, lazy-loaded, form-action, script-derived, sitemap, API, and search-engine-discovered URLs are outside this scope",
            "this operation performs no network fetch and does not prove target availability, robots permission, or source relevance",
            "selected URLs are a deterministic follow plan, not autonomous browsing execution",
        ],
        "falsification_criteria": [
            "reject when snapshot byte hash or byte count does not match the stored manifest",
            "classify rather than silently drop unsupported schemes, duplicates, cross-host exclusions, and selection overflow",
            "never label the scope exhaustive when max_candidates truncates href parsing",
            "do not infer site-wide or rendered-browser exhaustion from static frozen HTML anchors",
        ],
    }
    if output_path is not None:
        write_json(output_path, report)
        report["report_path"] = str(output_path)
        report["report_sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return report
