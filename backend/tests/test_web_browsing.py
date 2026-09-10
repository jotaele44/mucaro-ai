from __future__ import annotations

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.app.config import Settings
from backend.app.web_browsing import WebBrowsingError, WebFetchPolicy, fetch_web_snapshot, validate_web_fetch_url


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


class StaticHandler(BaseHTTPRequestHandler):
    body = b"<html><body>Mucaro governed browsing fixture.</body></html>"
    content_type = "text/html; charset=utf-8"
    redirect_to: str | None = None

    def do_GET(self) -> None:
        if self.path == "/redirect" and self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _serve_fixture(path: str = "/fixture.html") -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}{path}"


def test_validate_web_fetch_url_blocks_unsafe_inputs():
    blocked = [
        "file:///etc/passwd",
        "http://user:pass@example.com/",
        "http://localhost:8000/",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data",
    ]

    for url in blocked:
        try:
            validate_web_fetch_url(url)
        except WebBrowsingError as exc:
            assert exc.status.startswith("ERROR_WEB_")
        else:
            raise AssertionError(f"expected blocked URL: {url}")


def test_validate_web_fetch_url_blocks_private_dns_resolution(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    try:
        validate_web_fetch_url("https://public-name.example")
    except WebBrowsingError as exc:
        assert exc.status == "ERROR_WEB_PRIVATE_HOST_BLOCKED"
    else:
        raise AssertionError("expected DNS private-address block")


def test_fetch_web_snapshot_records_policy_and_bytes(tmp_path: Path):
    cfg = make_settings(tmp_path)
    server, url = _serve_fixture()
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
        )
    finally:
        server.shutdown()

    assert result["status"] == "WEB_FETCH_SNAPSHOT_OK"
    snapshot_path = Path(result["snapshot_path"])
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "mucaro_web_snapshot_v1"
    assert saved["requested_url"] == url
    assert saved["final_url"] == url
    assert saved["redirect_chain"] == []
    assert saved["http_status"] == 200
    assert saved["content_type"] == "text/html; charset=utf-8"
    assert saved["content_size_bytes"] == len(StaticHandler.body)
    assert base64.b64decode(saved["content_base64"]) == StaticHandler.body
    assert saved["content_sha256"]
    assert saved["retrieval_utc"].endswith("Z")
    assert saved["policy"]["allow_private_hosts"] is True
    assert "Mucaro governed browsing fixture" in saved["text_preview"]
    ledger = json.loads(Path(result["ledger_path"]).read_text(encoding="utf-8"))
    assert ledger["schema_version"] == "mucaro_web_snapshot_ledger_v1"
    assert ledger["entry_count"] == 1
    assert ledger["entries"][0]["snapshot_id"] == saved["snapshot_id"]
    assert ledger["entries"][0]["snapshot_sha256"] == result["snapshot_sha256"]


def test_fetch_web_snapshot_records_safe_redirect_chain(tmp_path: Path):
    cfg = make_settings(tmp_path)
    second_server, second_url = _serve_fixture()
    first_server, first_url = _serve_fixture("/redirect")
    original_redirect_to = StaticHandler.redirect_to
    StaticHandler.redirect_to = second_url
    try:
        result = fetch_web_snapshot(
            first_url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
        )
    finally:
        first_server.shutdown()
        second_server.shutdown()
        StaticHandler.redirect_to = original_redirect_to

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert saved["final_url"] == second_url
    assert saved["redirect_chain"] == [
        {
            "from_url": first_url,
            "to_url": second_url,
            "http_status": 302,
        }
    ]


def test_fetch_web_snapshot_preserves_binary_bytes_without_text_preview(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_body = StaticHandler.body
    original_content_type = StaticHandler.content_type
    StaticHandler.body = b"\x89PNG\r\n\x1a\n\x00\x00"
    StaticHandler.content_type = "image/png"
    server, url = _serve_fixture()
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
        )
    finally:
        server.shutdown()
        StaticHandler.body = original_body
        StaticHandler.content_type = original_content_type

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert base64.b64decode(saved["content_base64"]) == b"\x89PNG\r\n\x1a\n\x00\x00"
    assert saved["text_preview"] == ""
    assert saved["charset"] == ""
    assert saved["parser_warnings"] == ["WEB_CONTENT_NOT_TEXT_PREVIEW_SKIPPED"]
