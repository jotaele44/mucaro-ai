from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.app.config import Settings
from backend.app.web_render import WebRenderError, render_web_page

STATUS_OK = "WEB_RENDER_LIVE_CERT_OK"
STATUS_FAILED = "WEB_RENDER_LIVE_CERT_FAILED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute live public rendered-browser evidence.")
    parser.add_argument("--url", default="https://example.com/")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cfg = Settings.load()
    try:
        result = render_web_page(
            args.url,
            cfg=cfg,
            allow_rendered_browser=True,
            timeout_ms=30_000,
            max_dom_bytes=2_000_000,
        )
    except WebRenderError as exc:
        payload = {
            "schema_version": "mucaro-web-render-live-cert/1.0",
            "status": STATUS_FAILED,
            "error_status": exc.status,
            "error_detail": exc.detail,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(STATUS_FAILED)
        print(json.dumps(payload, sort_keys=True))
        return 2

    receipt = result["receipt"]
    screenshot = Path(receipt["screenshot"]["path"])
    screenshot_bytes = screenshot.read_bytes() if screenshot.is_file() else b""
    invariants = {
        "render_status_ok": result.get("status") == "WEB_RENDER_OK",
        "public_https_url": str(receipt.get("final_url", "")).startswith("https://"),
        "chromium_engine": receipt.get("engine", {}).get("name") == "playwright_chromium",
        "engine_version_present": receipt.get("engine", {}).get("version") not in {None, "", "unknown"},
        "dom_hash_present": len(str(receipt.get("dom", {}).get("content_sha256", ""))) == 64,
        "screenshot_hash_matches": bool(screenshot_bytes)
        and hashlib.sha256(screenshot_bytes).hexdigest() == receipt.get("screenshot", {}).get("sha256"),
        "screenshot_is_png": screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
        "private_hosts_disabled": receipt.get("invariants", {}).get("private_hosts_disabled") is True,
        "citation_not_silently_promoted": receipt.get("citation_policy", {}).get("citation_ready") is False,
    }
    payload = {
        "schema_version": "mucaro-web-render-live-cert/1.0",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "scope": "WEB-09_RENDER_RUNTIME_ONLY",
        "render_id": result.get("render_id"),
        "receipt_path": result.get("receipt_path"),
        "receipt_sha256": result.get("receipt_sha256"),
        "final_url": receipt.get("final_url"),
        "http_status": receipt.get("http_status"),
        "engine": receipt.get("engine"),
        "dom_sha256": receipt.get("dom", {}).get("content_sha256"),
        "screenshot_path": str(screenshot),
        "screenshot_sha256": _sha256(screenshot) if screenshot.is_file() else None,
        "invariants": invariants,
        "limitations": [
            "This proves live Playwright/Chromium page rendering and screenshot/DOM provenance only.",
            "It does not prove authenticated browsing, click/form automation, downloads/uploads, or arbitrary JavaScript interaction.",
            "WEB-09 remains broader than this executed scope and therefore cannot be promoted to PASS from this receipt alone.",
            "Rendered observations remain non-citation-ready unless separately frozen under the governed page-evidence contract.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(payload["status"])
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
