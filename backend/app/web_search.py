from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json

WEB_SEARCH_SCHEMA_VERSION = "mucaro-web-search/1.0"
BRAVE_WEB_SEARCH_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000
MAX_QUERY_CHARS = 600
MAX_QUERY_WORDS = 75
MAX_COUNT = 20
MAX_OFFSET = 9


class WebSearchError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bounded_response(response: Any, max_bytes: int) -> bytes:
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise WebSearchError("ERROR_WEB_SEARCH_RESPONSE_TOO_LARGE", f"response exceeded {max_bytes} bytes")
    return raw


def _validate_query(query: str) -> str:
    normalized = " ".join(str(query or "").split())
    if not normalized:
        raise WebSearchError("ERROR_WEB_SEARCH_QUERY_REQUIRED", "search query is required")
    if len(normalized) > MAX_QUERY_CHARS or len(normalized.split()) > MAX_QUERY_WORDS:
        raise WebSearchError(
            "ERROR_WEB_SEARCH_QUERY_LIMIT",
            f"query must be <= {MAX_QUERY_CHARS} characters and <= {MAX_QUERY_WORDS} words",
        )
    return normalized


def _validate_count_offset(count: int, offset: int) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > MAX_COUNT:
        raise WebSearchError("ERROR_WEB_SEARCH_COUNT", f"count must be 1..{MAX_COUNT}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset > MAX_OFFSET:
        raise WebSearchError("ERROR_WEB_SEARCH_OFFSET", f"offset must be 0..{MAX_OFFSET}")


def _result_rows(payload: dict[str, Any], *, count: int) -> list[dict[str, Any]]:
    web = payload.get("web")
    raw_results = web.get("results") if isinstance(web, dict) else []
    if not isinstance(raw_results, list):
        raw_results = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(raw_results[:count]):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        canonical = urllib.parse.urldefrag(url)[0]
        if canonical in seen:
            continue
        seen.add(canonical)
        rows.append(
            {
                "rank": len(rows) + 1,
                "provider_index": index,
                "title": str(row.get("title") or "").strip(),
                "url": canonical,
                "description": str(row.get("description") or "").strip(),
                "age": row.get("age"),
                "language": row.get("language"),
            }
        )
    return rows


def search_web_brave(
    query: str,
    *,
    cfg: Settings = settings,
    count: int = 10,
    offset: int = 0,
    country: str = "US",
    search_lang: str = "en",
    safesearch: str = "moderate",
    freshness: str | None = None,
    allow_live_search: bool = False,
    api_key: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    normalized_query = _validate_query(query)
    _validate_count_offset(count, offset)
    if not allow_live_search:
        raise WebSearchError(
            "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED",
            "live web search requires explicit allow_live_search=true",
        )
    token = api_key or os.getenv("MUCARO_BRAVE_SEARCH_API_KEY")
    if not token:
        raise WebSearchError(
            "ERROR_WEB_SEARCH_CREDENTIAL_MISSING",
            "MUCARO_BRAVE_SEARCH_API_KEY is not configured",
        )
    if safesearch not in {"off", "moderate", "strict"}:
        raise WebSearchError("ERROR_WEB_SEARCH_SAFESEARCH", "safesearch must be off, moderate, or strict")
    country = str(country or "US").strip().upper()
    search_lang = str(search_lang or "en").strip().lower()
    if len(country) != 2 or not country.isalpha():
        raise WebSearchError("ERROR_WEB_SEARCH_COUNTRY", "country must be a two-letter code")
    if not search_lang or len(search_lang) > 8:
        raise WebSearchError("ERROR_WEB_SEARCH_LANGUAGE", "invalid search language")

    params = {
        "q": normalized_query,
        "count": str(count),
        "offset": str(offset),
        "country": country,
        "search_lang": search_lang,
        "safesearch": safesearch,
    }
    if freshness:
        params["freshness"] = str(freshness)
    request_url = BRAVE_WEB_SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Subscription-Token": token,
            "User-Agent": "MucaroAI/0.1 governed-search",
        },
        method="GET",
    )
    started_at = utc_now()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = _bounded_response(response, max_response_bytes)
            http_status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")
        raise WebSearchError("ERROR_WEB_SEARCH_HTTP", f"HTTP {exc.code}: {detail[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise WebSearchError("ERROR_WEB_SEARCH_FAILED", str(exc.reason)) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSearchError("ERROR_WEB_SEARCH_RESPONSE_JSON", str(exc)) from exc
    if not isinstance(payload, dict):
        raise WebSearchError("ERROR_WEB_SEARCH_RESPONSE_JSON", "provider response must be a JSON object")

    results = _result_rows(payload, count=count)
    query_payload = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    completed_at = utc_now()
    search_id = stable_id("websearch", normalized_query, str(offset), started_at)
    receipt: dict[str, Any] = {
        "schema_version": WEB_SEARCH_SCHEMA_VERSION,
        "search_id": search_id,
        "status": "WEB_SEARCH_OK",
        "provider": "brave_web_search",
        "provider_endpoint": BRAVE_WEB_SEARCH_ENDPOINT,
        "query": normalized_query,
        "started_at": started_at,
        "completed_at": completed_at,
        "http_status": http_status,
        "parameters": {
            "count": count,
            "offset": offset,
            "country": country,
            "search_lang": search_lang,
            "safesearch": safesearch,
            "freshness": freshness,
            "allow_live_search": True,
        },
        "credential": {
            "source": "explicit_argument" if api_key is not None else "environment:MUCARO_BRAVE_SEARCH_API_KEY",
            "value_persisted": False,
        },
        "provider_response_sha256": hashlib.sha256(raw).hexdigest(),
        "result_count": len(results),
        "results": results,
        "more_results_available": bool(query_payload.get("more_results_available")),
        "invariants": {
            "credential_not_persisted": True,
            "result_count_within_requested_count": len(results) <= count,
            "result_urls_unique": len({row["url"] for row in results}) == len(results),
            "result_urls_http_only": all(urllib.parse.urlparse(row["url"]).scheme in {"http", "https"} for row in results),
        },
        "limitations": [
            "search-provider ranking is external evidence and is not treated as a factual claim by itself",
            "result discovery does not fetch, cite, or validate target-page contents",
            "result availability, robots policy, redirects and byte identity are established only by governed fetch snapshots",
            "provider credentials are intentionally not included in receipts",
        ],
        "falsification_criteria": [
            "never execute live search without explicit allow_live_search=true",
            "never persist the provider API key",
            "never emit more normalized results than the requested count",
            "never treat search-result snippets as equivalent to frozen target-page evidence",
        ],
    }
    out_dir = cfg.export_dir / "web_searches"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{search_id}.json"
    write_json(path, receipt)
    return {
        "status": "WEB_SEARCH_OK",
        "search_id": search_id,
        "receipt_path": str(path),
        "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "receipt": receipt,
    }
