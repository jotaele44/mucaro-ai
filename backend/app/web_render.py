from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from .config import Settings, settings
from .utils import stable_id, utc_now, write_json
from .web_browsing import WebBrowsingError, validate_web_fetch_url

WEB_RENDER_SCHEMA_VERSION = "mucaro-web-render/1.0"
DEFAULT_TIMEOUT_MS = 20_000
MAX_TIMEOUT_MS = 60_000
DEFAULT_MAX_DOM_BYTES = 2_000_000
MAX_MAX_DOM_BYTES = 8_000_000


class WebRenderError(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise WebRenderError("ERROR_WEB_RENDER_LIMIT", f"{label} must be {minimum}..{maximum}")
    return value


def _render_with_playwright(url: str, *, timeout_ms: int) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as exc:
        raise WebRenderError(
            "ERROR_WEB_RENDER_ENGINE_MISSING",
            "Playwright is not installed; install the optional browser runtime before rendered browsing.",
        ) from exc

    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                final_url = page.url
                html = page.content().encode("utf-8")
                screenshot = page.screenshot(full_page=True)
                title = page.title()
                version = browser.version
                http_status = response.status if response is not None else None
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
    }


def render_web_page(
    url: str,
    *,
    cfg: Settings = settings,
    allow_rendered_browser: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    max_dom_bytes: int = DEFAULT_MAX_DOM_BYTES,
) -> dict[str, Any]:
    cfg.ensure_dirs()
    if not allow_rendered_browser:
        raise WebRenderError(
            "ERROR_WEB_RENDER_NOT_ALLOWED",
            "rendered browsing requires explicit allow_rendered_browser=true",
        )
    timeout_ms = _bounded_int(timeout_ms, minimum=1_000, maximum=MAX_TIMEOUT_MS, label="timeout_ms")
    max_dom_bytes = _bounded_int(max_dom_bytes, minimum=1_024, maximum=MAX_MAX_DOM_BYTES, label="max_dom_bytes")

    try:
        checked = validate_web_fetch_url(url, allow_private_hosts=False)
    except WebBrowsingError as exc:
        raise WebRenderError(exc.status, exc.detail) from exc

    requested_url = str(checked["requested_url"])
    fetch_url = str(checked["fetch_url"])
    started_at = utc_now()
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
        started_at,
    )
    out_dir = cfg.export_dir / "web_renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = out_dir / f"{render_id}.png"
    screenshot_path.write_bytes(screenshot)

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
            "timeout_ms": timeout_ms,
            "max_dom_bytes": max_dom_bytes,
            "private_hosts_allowed": False,
            "wait_until": "networkidle",
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
            "dom_within_ceiling": len(dom) <= max_dom_bytes,
            "dom_hash_present": True,
            "screenshot_hash_present": True,
            "credential_material_persisted": False,
        },
        "limitations": [
            "Rendered output may differ across browser versions, network timing, geolocation, cookies, and server-side personalization.",
            "No credentials, cookies, local storage, downloads, uploads, form submission, or arbitrary click automation are enabled by this adapter.",
            "This source-level adapter does not prove Playwright/Chromium is installed on the release target.",
            "Rendered DOM and screenshots are not promoted to citation evidence by this adapter alone.",
        ],
        "falsification_criteria": [
            "never render private/local hosts",
            "never execute without explicit allow_rendered_browser=true",
            "never silently truncate DOM bytes",
            "never claim a rendered page is citation-ready without a separate evidence gate",
            "bind every successful render to exact engine version, final URL, DOM hash, and screenshot hash",
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
