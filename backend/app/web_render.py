from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json
from .web_browsing import WebBrowsingError, validate_web_fetch_url

WEB_RENDER_SCHEMA_VERSION = "mucaro-web-render/1.1"
DEFAULT_TIMEOUT_MS = 20_000
MAX_TIMEOUT_MS = 60_000
DEFAULT_MAX_DOM_BYTES = 2_000_000
MAX_MAX_DOM_BYTES = 8_000_000
DEFAULT_MAX_DOWNLOAD_BYTES = 20_000_000
MAX_MAX_DOWNLOAD_BYTES = 100_000_000
MAX_ACTIONS = 20
MAX_SELECTOR_CHARS = 1_024
MAX_FORM_VALUE_CHARS = 10_000
MAX_SCRIPT_CHARS = 20_000
MAX_UPLOAD_BYTES = 10_000_000
MAX_WAIT_AFTER_MS = 5_000
_ALLOWED_ACTIONS = {
    "click",
    "fill",
    "press",
    "select",
    "check",
    "uncheck",
    "evaluate",
    "upload",
    "download_click",
}


class WebRenderError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise WebRenderError("ERROR_WEB_RENDER_LIMIT", f"{label} must be {minimum}..{maximum}")
    return value


def _bounded_text(value: Any, *, label: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise WebRenderError("ERROR_WEB_RENDER_ACTION", f"{label} must be a string")
    if not allow_empty and not value.strip():
        raise WebRenderError("ERROR_WEB_RENDER_ACTION", f"{label} must not be empty")
    if len(value) > maximum:
        raise WebRenderError("ERROR_WEB_RENDER_ACTION", f"{label} exceeded {maximum} characters")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_filename(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (name[:120] or "download.bin")


def _browser_request_allowed(url: str, blocked_requests: list[dict[str, Any]]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return True
    try:
        validate_web_fetch_url(url, allow_private_hosts=False)
        return True
    except WebBrowsingError as exc:
        blocked_requests.append(
            {
                "url_sha256": _sha256_text(url),
                "scheme": parsed.scheme,
                "host": parsed.hostname or "",
                "status": exc.status,
            }
        )
        return False


def _validate_page_url(url: str, *, status: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise WebRenderError(status, f"browser navigation left HTTP(S): {parsed.scheme or 'missing-scheme'}")
    try:
        validate_web_fetch_url(url, allow_private_hosts=False)
    except WebBrowsingError as exc:
        raise WebRenderError(status, exc.detail) from exc


def _normalize_actions(
    actions: Any,
    *,
    cfg: Settings,
    allow_interactions: bool,
    allow_javascript_actions: bool,
    allow_uploads: bool,
    allow_downloads: bool,
) -> list[dict[str, Any]]:
    if actions is None or actions == ():
        return []
    if not isinstance(actions, list):
        raise WebRenderError("ERROR_WEB_RENDER_ACTIONS_INVALID", "actions must be an array")
    if not actions:
        return []
    if not allow_interactions:
        raise WebRenderError(
            "ERROR_WEB_RENDER_INTERACTIONS_NOT_ALLOWED",
            "browser interactions require explicit allow_interactions=true",
        )
    if len(actions) > MAX_ACTIONS:
        raise WebRenderError("ERROR_WEB_RENDER_ACTIONS_INVALID", f"actions exceeded maximum {MAX_ACTIONS}")

    normalized: list[dict[str, Any]] = []
    input_root = cfg.input_dir.resolve()
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise WebRenderError("ERROR_WEB_RENDER_ACTION", f"action {index} must be an object")
        action_type = str(raw.get("type") or "")
        if action_type not in _ALLOWED_ACTIONS:
            raise WebRenderError("ERROR_WEB_RENDER_ACTION", f"action {index} has unsupported type: {action_type}")
        action: dict[str, Any] = {"index": index, "type": action_type}
        wait_after_ms = raw.get("wait_after_ms", 0)
        action["wait_after_ms"] = _bounded_int(
            wait_after_ms,
            minimum=0,
            maximum=MAX_WAIT_AFTER_MS,
            label=f"actions[{index}].wait_after_ms",
        )

        if action_type != "evaluate":
            action["selector"] = _bounded_text(
                raw.get("selector"),
                label=f"actions[{index}].selector",
                maximum=MAX_SELECTOR_CHARS,
            )

        if action_type == "fill":
            action["value"] = _bounded_text(
                raw.get("value"),
                label=f"actions[{index}].value",
                maximum=MAX_FORM_VALUE_CHARS,
                allow_empty=True,
            )
        elif action_type == "press":
            action["key"] = _bounded_text(raw.get("key"), label=f"actions[{index}].key", maximum=64)
        elif action_type == "select":
            action["value"] = _bounded_text(
                raw.get("value"),
                label=f"actions[{index}].value",
                maximum=MAX_FORM_VALUE_CHARS,
                allow_empty=True,
            )
        elif action_type == "evaluate":
            if not allow_javascript_actions:
                raise WebRenderError(
                    "ERROR_WEB_RENDER_JAVASCRIPT_NOT_ALLOWED",
                    "evaluate actions require explicit allow_javascript_actions=true",
                )
            action["script"] = _bounded_text(
                raw.get("script"),
                label=f"actions[{index}].script",
                maximum=MAX_SCRIPT_CHARS,
            )
        elif action_type == "upload":
            if not allow_uploads:
                raise WebRenderError(
                    "ERROR_WEB_RENDER_UPLOAD_NOT_ALLOWED",
                    "upload actions require explicit allow_uploads=true",
                )
            raw_path = _bounded_text(
                raw.get("path"),
                label=f"actions[{index}].path",
                maximum=4_096,
            )
            candidate = Path(raw_path)
            resolved = (candidate if candidate.is_absolute() else cfg.input_dir / candidate).resolve()
            if not resolved.is_relative_to(input_root) or not resolved.is_file():
                raise WebRenderError(
                    "ERROR_WEB_RENDER_UPLOAD_PATH",
                    "upload path must resolve to an existing file inside the Mucaro input directory",
                )
            size = resolved.stat().st_size
            if size > MAX_UPLOAD_BYTES:
                raise WebRenderError(
                    "ERROR_WEB_RENDER_UPLOAD_TOO_LARGE",
                    f"upload file exceeded {MAX_UPLOAD_BYTES} bytes",
                )
            action["upload_path"] = str(resolved)
            action["upload_relative_path"] = str(resolved.relative_to(input_root))
            action["upload_size_bytes"] = size
            action["upload_sha256"] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        elif action_type == "download_click" and not allow_downloads:
            raise WebRenderError(
                "ERROR_WEB_RENDER_DOWNLOAD_NOT_ALLOWED",
                "download actions require explicit allow_downloads=true",
            )
        normalized.append(action)
    return normalized


def _action_plan_receipt(action: dict[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "index": int(action["index"]),
        "type": str(action["type"]),
        "wait_after_ms": int(action.get("wait_after_ms", 0)),
    }
    if "selector" in action:
        selector = str(action["selector"])
        receipt["selector_length"] = len(selector)
        receipt["selector_sha256"] = _sha256_text(selector)
    if action["type"] in {"fill", "select"}:
        value = str(action.get("value", ""))
        receipt["value_length"] = len(value)
        receipt["value_sha256"] = _sha256_text(value)
    elif action["type"] == "press":
        receipt["key"] = str(action["key"])
    elif action["type"] == "evaluate":
        script = str(action["script"])
        receipt["script_length"] = len(script)
        receipt["script_sha256"] = _sha256_text(script)
    elif action["type"] == "upload":
        receipt["upload_relative_path"] = str(action["upload_relative_path"])
        receipt["upload_size_bytes"] = int(action["upload_size_bytes"])
        receipt["upload_sha256"] = str(action["upload_sha256"])
    return receipt


def _execute_actions(
    page: Any,
    actions: list[dict[str, Any]],
    *,
    timeout_ms: int,
    max_download_bytes: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for action in actions:
        action_type = str(action["type"])
        selector = action.get("selector")
        result = _action_plan_receipt(action)
        result["status"] = "ACTION_OK"

        if action_type == "click":
            page.locator(str(selector)).click(timeout=timeout_ms)
        elif action_type == "fill":
            page.locator(str(selector)).fill(str(action["value"]), timeout=timeout_ms)
        elif action_type == "press":
            page.locator(str(selector)).press(str(action["key"]), timeout=timeout_ms)
        elif action_type == "select":
            page.locator(str(selector)).select_option(value=str(action["value"]), timeout=timeout_ms)
        elif action_type == "check":
            page.locator(str(selector)).check(timeout=timeout_ms)
        elif action_type == "uncheck":
            page.locator(str(selector)).uncheck(timeout=timeout_ms)
        elif action_type == "evaluate":
            value = page.evaluate(str(action["script"]))
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
            result["result_type"] = type(value).__name__
            result["result_sha256"] = _sha256_text(encoded)
        elif action_type == "upload":
            page.locator(str(selector)).set_input_files(str(action["upload_path"]), timeout=timeout_ms)
        elif action_type == "download_click":
            with page.expect_download(timeout=timeout_ms) as download_info:
                page.locator(str(selector)).click(timeout=timeout_ms)
            download = download_info.value
            download_url = str(download.url or "")
            parsed_download = urlparse(download_url)
            if parsed_download.scheme in {"http", "https"}:
                _validate_page_url(download_url, status="ERROR_WEB_RENDER_DOWNLOAD_URL_POLICY")
            download_path = download.path()
            if download_path is None:
                raise WebRenderError("ERROR_WEB_RENDER_DOWNLOAD_MISSING", "browser download has no local temporary file")
            raw_download = Path(download_path).read_bytes()
            if len(raw_download) > max_download_bytes:
                raise WebRenderError(
                    "ERROR_WEB_RENDER_DOWNLOAD_TOO_LARGE",
                    f"download exceeded {max_download_bytes} bytes",
                )
            result["download"] = {
                "suggested_filename": _safe_filename(str(download.suggested_filename or "download.bin")),
                "size_bytes": len(raw_download),
                "sha256": hashlib.sha256(raw_download).hexdigest(),
                "source_scheme": parsed_download.scheme or "unknown",
            }
            result["_download_bytes"] = raw_download

        if int(action.get("wait_after_ms", 0)):
            page.wait_for_timeout(int(action["wait_after_ms"]))
        _validate_page_url(str(page.url), status="ERROR_WEB_RENDER_ACTION_URL_POLICY")
        result["page_url_sha256_after"] = _sha256_text(str(page.url))
        results.append(result)
    return results


def _render_with_playwright(
    url: str,
    *,
    timeout_ms: int,
    actions: list[dict[str, Any]] | None = None,
    allow_downloads: bool = False,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        raise WebRenderError(
            "ERROR_WEB_RENDER_ENGINE_MISSING",
            "Playwright is not installed; install the optional browser runtime before rendered browsing.",
        ) from exc

    blocked_requests: list[dict[str, Any]] = []
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                context = browser.new_context(accept_downloads=allow_downloads, service_workers="block")

                def guard(route: Any) -> None:
                    if _browser_request_allowed(str(route.request.url), blocked_requests):
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", guard)
                page = context.new_page()
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                action_results = _execute_actions(
                    page,
                    actions or [],
                    timeout_ms=timeout_ms,
                    max_download_bytes=max_download_bytes,
                )
                final_url = page.url
                html = page.content().encode("utf-8")
                screenshot = page.screenshot(full_page=True)
                title = page.title()
                version = browser.version
                http_status = response.status if response is not None else None
                context.close()
            finally:
                browser.close()
    except WebRenderError:
        raise
    except Exception as exc:
        raise WebRenderError("ERROR_WEB_RENDER_EXECUTION", str(exc)) from exc

    return {
        "engine": "playwright_chromium",
        "engine_version": version,
        "final_url": final_url,
        "http_status": http_status,
        "title": title,
        "dom_bytes": html,
        "screenshot_bytes": screenshot,
        "action_results": action_results,
        "blocked_requests": blocked_requests,
    }


def render_web_page(
    url: str,
    *,
    cfg: Settings = settings,
    allow_rendered_browser: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_dom_bytes: int = DEFAULT_MAX_DOM_BYTES,
    actions: list[dict[str, Any]] | None = None,
    allow_interactions: bool = False,
    allow_javascript_actions: bool = False,
    allow_uploads: bool = False,
    allow_downloads: bool = False,
    max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    if not allow_rendered_browser:
        raise WebRenderError(
            "ERROR_WEB_RENDER_NOT_ALLOWED",
            "rendered browsing requires explicit allow_rendered_browser=true",
        )
    timeout_ms = _bounded_int(timeout_ms, minimum=1_000, maximum=MAX_TIMEOUT_MS, label="timeout_ms")
    max_dom_bytes = _bounded_int(max_dom_bytes, minimum=1_024, maximum=MAX_MAX_DOM_BYTES, label="max_dom_bytes")
    max_download_bytes = _bounded_int(
        max_download_bytes,
        minimum=1_024,
        maximum=MAX_MAX_DOWNLOAD_BYTES,
        label="max_download_bytes",
    )
    normalized_actions = _normalize_actions(
        actions,
        cfg=cfg,
        allow_interactions=allow_interactions,
        allow_javascript_actions=allow_javascript_actions,
        allow_uploads=allow_uploads,
        allow_downloads=allow_downloads,
    )
    action_plan = [_action_plan_receipt(action) for action in normalized_actions]

    try:
        checked = validate_web_fetch_url(url, allow_private_hosts=False)
    except WebBrowsingError as exc:
        raise WebRenderError(exc.status, exc.detail) from exc

    requested_url = str(checked["requested_url"])
    fetch_url = str(checked["fetch_url"])
    started_at = utc_now()
    if normalized_actions:
        rendered = _render_with_playwright(
            fetch_url,
            timeout_ms=timeout_ms,
            actions=normalized_actions,
            allow_downloads=allow_downloads,
            max_download_bytes=max_download_bytes,
        )
    else:
        rendered = _render_with_playwright(fetch_url, timeout_ms=timeout_ms)

    try:
        final_checked = validate_web_fetch_url(str(rendered["final_url"]), allow_private_hosts=False)
    except WebBrowsingError as exc:
        raise WebRenderError("ERROR_WEB_RENDER_FINAL_URL_POLICY", exc.detail) from exc

    dom = rendered.get("dom_bytes")
    screenshot = rendered.get("screenshot_bytes")
    if not isinstance(dom, (bytes, bytearray)):
        raise WebRenderError("ERROR_WEB_RENDER_DOM_MISSING", "render engine did not return DOM bytes")
    if not isinstance(screenshot, (bytes, bytearray)):
        raise WebRenderError("ERROR_WEB_RENDER_SCREENSHOT_MISSING", "render engine did not return screenshot bytes")
    dom = bytes(dom)
    screenshot = bytes(screenshot)
    if len(dom) > max_dom_bytes:
        raise WebRenderError("ERROR_WEB_RENDER_DOM_TOO_LARGE", f"rendered DOM exceeded {max_dom_bytes} bytes")

    completed_at = utc_now()
    render_id = stable_id(
        "webrender",
        requested_url,
        str(final_checked["fetch_url"]),
        hashlib.sha256(dom).hexdigest(),
        hashlib.sha256(json.dumps(action_plan, sort_keys=True).encode("utf-8")).hexdigest(),
        started_at,
    )
    out_dir = cfg.export_dir / "web_renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / f"{render_id}.png"
    screenshot_path.write_bytes(screenshot)

    action_results: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    for raw_result in rendered.get("action_results", []):
        if not isinstance(raw_result, dict):
            continue
        result = dict(raw_result)
        download_bytes = result.pop("_download_bytes", None)
        download = result.get("download")
        if isinstance(download_bytes, (bytes, bytearray)) and isinstance(download, dict):
            filename = _safe_filename(str(download.get("suggested_filename") or "download.bin"))
            download_dir = out_dir / "downloads"
            download_dir.mkdir(parents=True, exist_ok=True)
            path = download_dir / f"{render_id}-{int(result.get('index', 0)):02d}-{filename}"
            path.write_bytes(bytes(download_bytes))
            download["path"] = str(path)
            download["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            downloads.append(dict(download))
        action_results.append(result)

    blocked_requests = [
        dict(row)
        for row in rendered.get("blocked_requests", [])
        if isinstance(row, dict)
    ]
    receipt: dict[str, Any] = {
        "schema_version": WEB_RENDER_SCHEMA_VERSION,
        "render_id": render_id,
        "status": "WEB_RENDER_OK",
        "requested_url": requested_url,
        "fetch_url": fetch_url,
        "final_url": str(final_checked["fetch_url"]),
        "started_at": started_at,
        "completed_at": completed_at,
        "http_status": rendered.get("http_status"),
        "title": str(rendered.get("title") or ""),
        "engine": {
            "name": str(rendered.get("engine") or "unknown"),
            "version": str(rendered.get("engine_version") or "unknown"),
            "headless": True,
        },
        "parameters": {
            "allow_rendered_browser": True,
            "allow_interactions": bool(normalized_actions),
            "allow_javascript_actions": allow_javascript_actions,
            "allow_uploads": allow_uploads,
            "allow_downloads": allow_downloads,
            "timeout_ms": timeout_ms,
            "max_dom_bytes": max_dom_bytes,
            "max_download_bytes": max_download_bytes,
            "private_hosts_allowed": False,
            "wait_until": "networkidle",
        },
        "interaction": {
            "enabled": bool(normalized_actions),
            "action_count": len(normalized_actions),
            "action_plan": action_plan,
            "action_results": action_results,
            "blocked_request_count": len(blocked_requests),
            "blocked_requests": blocked_requests,
            "downloads": downloads,
        },
        "dom": {
            "content_size_bytes": len(dom),
            "content_sha256": hashlib.sha256(dom).hexdigest(),
            "content_base64": base64.b64encode(dom).decode("ascii"),
        },
        "screenshot": {
            "path": str(screenshot_path),
            "size_bytes": len(screenshot),
            "sha256": hashlib.sha256(screenshot).hexdigest(),
            "format": "png",
        },
        "citation_policy": {
            "citation_ready": False,
            "reason": "rendered DOM/screenshot are observation evidence; citation requires a separately governed frozen page-evidence contract",
        },
        "invariants": {
            "private_hosts_disabled": True,
            "network_request_firewall_enabled": True,
            "dom_within_ceiling": len(dom) <= max_dom_bytes,
            "dom_hash_present": True,
            "screenshot_hash_present": True,
            "raw_action_values_omitted_from_action_receipt": True,
            "upload_paths_bounded_to_input_dir": True,
            "downloads_hashed": all(len(str(row.get("sha256", ""))) == 64 for row in downloads),
            "credential_store_loaded": False,
        },
        "limitations": [
            "Rendered output may differ across browser versions, network timing, geolocation, cookies, and server-side personalization.",
            "Authenticated browsing, cookie import, storage-state import, and secret-bearing form automation remain unsupported.",
            "Fill/select action values and JavaScript source are hash-bound rather than copied into the action receipt; page-visible values can still appear in the DOM or screenshot and must not contain secrets.",
            "Uploads are limited to files already inside the Mucaro input directory and require explicit allow_uploads=true.",
            "Downloads require explicit allow_downloads=true, are byte-capped, saved under web_renders/downloads, and SHA-256 bound.",
            "Every HTTP(S) browser request is checked against the public-host policy; blocked subrequests are recorded by URL hash rather than raw URL.",
            "This source-level adapter does not prove Playwright/Chromium is installed on the release target.",
            "Rendered DOM and screenshots are not promoted to citation evidence by this adapter alone.",
        ],
        "falsification_criteria": [
            "never render or navigate to private/local HTTP(S) hosts",
            "never permit private/local HTTP(S) subrequests from page JavaScript or page assets",
            "never execute interactions without explicit allow_interactions=true",
            "never execute JavaScript, upload, or download actions without their dedicated explicit permission",
            "never persist raw fill/select values or JavaScript source in the action receipt",
            "never upload a file outside the Mucaro input directory",
            "never silently truncate DOM or downloaded bytes",
            "never claim a rendered page is citation-ready without a separate evidence gate",
            "bind every successful render to exact engine version, final URL, DOM hash, screenshot hash, and sanitized action plan",
        ],
    }
    receipt_path = out_dir / f"{render_id}.json"
    write_json(receipt_path, receipt)
    return {
        "status": "WEB_RENDER_OK",
        "render_id": render_id,
        "receipt_path": str(receipt_path),
        "receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "receipt": receipt,
    }
