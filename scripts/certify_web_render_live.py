from __future__ import annotations

import argparse
import base64
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
    parser = argparse.ArgumentParser(description="Execute live public rendered-browser interaction evidence.")
    parser.add_argument("--url", default="https://example.com/")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cfg = Settings.load()
    cfg.ensure_dirs()
    upload_path = cfg.input_dir / "web09-cert-upload.txt"
    upload_path.write_text("WEB09-UPLOAD-CERT\n", encoding="utf-8")
    fill_value = "mucaro-form-value"
    select_value = "web09-choice-beta-unique"
    setup_script = """
document.body.innerHTML = `
  <main id="fixture">
    <input id="name" type="text">
    <select id="choice"><option value="a">A</option><option value="web09-choice-beta-unique">B</option></select>
    <input id="agree" type="checkbox">
    <input id="upload" type="file">
    <button id="toggle" type="button">Toggle</button>
    <a id="download" download="web09-fixture.txt" href="data:text/plain;base64,V0VCMDktRE9XTkxPQUQtQ0VSVAo=">Download</a>
  </main>
`;
document.querySelector("#toggle").addEventListener("click", () => {
  document.body.dataset.clicked = "yes";
});
"fixture-ready";
""".strip()
    verify_script = """
const ok =
  document.body.dataset.clicked === "yes" &&
  document.querySelector("#name").value === "mucaro-form-value" &&
  document.querySelector("#choice").value === "web09-choice-beta-unique" &&
  document.querySelector("#agree").checked === true &&
  document.querySelector("#upload").files.length === 1;
document.body.dataset.cert = ok ? "interaction-complete" : "interaction-failed";
document.body.dataset.cert;
""".strip()
    actions = [
        {"type": "evaluate", "script": setup_script},
        {"type": "fill", "selector": "#name", "value": fill_value},
        {"type": "select", "selector": "#choice", "value": select_value},
        {"type": "check", "selector": "#agree"},
        {"type": "upload", "selector": "#upload", "path": str(upload_path)},
        {"type": "click", "selector": "#toggle"},
        {"type": "download_click", "selector": "#download"},
        {"type": "evaluate", "script": verify_script},
    ]

    try:
        result = render_web_page(
            args.url,
            cfg=cfg,
            allow_rendered_browser=True,
            timeout_ms=30_000,
            max_dom_bytes=2_000_000,
            actions=actions,
            allow_interactions=True,
            allow_javascript_actions=True,
            allow_uploads=True,
            allow_downloads=True,
            max_download_bytes=1_000_000,
        )
    except WebRenderError as exc:
        payload = {
            "schema_version": "mucaro-web-render-live-cert/1.1",
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
    interaction = receipt.get("interaction", {})
    action_plan = interaction.get("action_plan", [])
    action_results = interaction.get("action_results", [])
    downloads = interaction.get("downloads", [])
    dom_bytes = base64.b64decode(str(receipt.get("dom", {}).get("content_base64", "")) or "")
    action_plan_text = json.dumps(action_plan, sort_keys=True)
    upload_sha256 = _sha256(upload_path)
    download_ok = False
    if len(downloads) == 1 and isinstance(downloads[0], dict):
        download_path = Path(str(downloads[0].get("path") or ""))
        download_ok = (
            download_path.is_file()
            and len(str(downloads[0].get("sha256") or "")) == 64
            and _sha256(download_path) == downloads[0].get("sha256")
            and download_path.read_bytes() == b"WEB09-DOWNLOAD-CERT\n"
        )

    action_types = [row.get("type") for row in action_results if isinstance(row, dict)]
    action_statuses = [row.get("status") for row in action_results if isinstance(row, dict)]
    upload_plans = [row for row in action_plan if isinstance(row, dict) and row.get("type") == "upload"]
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
        "request_firewall_enabled": receipt.get("invariants", {}).get("network_request_firewall_enabled") is True,
        "citation_not_silently_promoted": receipt.get("citation_policy", {}).get("citation_ready") is False,
        "interaction_enabled": interaction.get("enabled") is True,
        "interaction_denominator_closed": interaction.get("action_count") == 8
        and len(action_plan) == 8
        and len(action_results) == 8,
        "interaction_types_exact": action_types
        == ["evaluate", "fill", "select", "check", "upload", "click", "download_click", "evaluate"],
        "all_actions_executed": action_statuses == ["ACTION_OK"] * 8,
        "form_and_click_state_verified": b"interaction-complete" in dom_bytes and b'interaction-failed' not in dom_bytes,
        "raw_fill_value_omitted_from_action_plan": fill_value not in action_plan_text,
        "raw_select_value_omitted_from_action_plan": select_value not in action_plan_text,
        "javascript_source_omitted_from_action_plan": setup_script not in action_plan_text
        and verify_script not in action_plan_text,
        "upload_hash_bound": len(upload_plans) == 1 and upload_plans[0].get("upload_sha256") == upload_sha256,
        "download_hash_bound": download_ok,
    }
    payload = {
        "schema_version": "mucaro-web-render-live-cert/1.1",
        "status": STATUS_OK if all(invariants.values()) else STATUS_FAILED,
        "scope": "WEB-09_RENDER_AND_INTERACTION_RUNTIME",
        "render_id": result.get("render_id"),
        "receipt_path": result.get("receipt_path"),
        "receipt_sha256": result.get("receipt_sha256"),
        "final_url": receipt.get("final_url"),
        "http_status": receipt.get("http_status"),
        "engine": receipt.get("engine"),
        "dom_sha256": receipt.get("dom", {}).get("content_sha256"),
        "screenshot_path": str(screenshot),
        "screenshot_sha256": _sha256(screenshot) if screenshot.is_file() else None,
        "interaction_action_count": interaction.get("action_count"),
        "interaction_action_types": action_types,
        "blocked_request_count": interaction.get("blocked_request_count"),
        "download_count": len(downloads),
        "invariants": invariants,
        "limitations": [
            "This executes rendering plus click/form/select/check/upload/download and arbitrary JavaScript interaction in a fresh unauthenticated Chromium context.",
            "The interaction fixture is injected after loading a public origin; it proves runtime mechanics and policy enforcement, not the behavior of every third-party site.",
            "Authenticated browsing, cookies/storage-state import, and secret-bearing form automation remain outside this receipt.",
            "Rendered observations remain non-citation-ready unless separately frozen under the governed page-evidence contract.",
            "WEB-09 promotion must be adjudicated against its exact frozen requirement and remaining authenticated-browsing residue; script success alone is not certification.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(payload["status"])
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == STATUS_OK else 2


if __name__ == "__main__":
    raise SystemExit(main())
