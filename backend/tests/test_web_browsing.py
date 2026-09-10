from __future__ import annotations

import base64
import json
import socket
import threading
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.app.config import Settings
from backend.app.ingestion import get_chunks, get_sources, ingest_web_snapshot
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
    body = (
        b"<html><head><title>Mucaro Web Fixture</title>"
        b"<meta name=\"author\" content=\"Mucaro\"></head>"
        b"<body><h1>Governed browsing</h1>"
        b"<a href=\"/source\">source</a>"
        b"<p>Mucaro governed browsing fixture.</p></body></html>"
    )
    content_type = "text/html; charset=utf-8"
    redirect_to: str | None = None
    robots_body = b"User-agent: *\nAllow: /\n"
    cache_control = "max-age=60"
    response_status = 200

    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(self.robots_body)))
            self.end_headers()
            self.wfile.write(self.robots_body)
            return
        if self.path == "/redirect" and self.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.end_headers()
            return
        self.send_response(self.response_status)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Cache-Control", self.cache_control)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _make_handler(
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    redirect_to: str | None = None,
    robots_body: bytes | None = None,
    cache_control: str | None = None,
    response_status: int | None = None,
) -> type[StaticHandler]:
    class CustomStaticHandler(StaticHandler):
        pass

    if body is not None:
        CustomStaticHandler.body = body
    if content_type is not None:
        CustomStaticHandler.content_type = content_type
    if redirect_to is not None:
        CustomStaticHandler.redirect_to = redirect_to
    if robots_body is not None:
        CustomStaticHandler.robots_body = robots_body
    if cache_control is not None:
        CustomStaticHandler.cache_control = cache_control
    if response_status is not None:
        CustomStaticHandler.response_status = response_status
    return CustomStaticHandler


def _serve_fixture(path: str = "/fixture.html", handler_cls: type[StaticHandler] = StaticHandler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
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
    assert saved["cache_policy"]["cache_control"] == "max-age=60"
    assert saved["cache_policy"]["cache_mode"] == "immutable_snapshot_only"
    assert saved["cache_policy"]["header_status"] == "CACHE_HEADERS_PRESENT"
    assert saved["cache_policy"]["reuse_allowed"] is False
    assert saved["cache_policy"]["revalidation_required_before_reuse"] is True
    assert saved["robots_policy"]["status"] == "ROBOTS_ALLOWED"
    assert saved["robots_policy"]["allowed"] is True
    assert saved["robots_policy"]["content_sha256"]
    assert saved["http_error_policy"]["is_http_error"] is False
    assert saved["parser_trace"]["content_kind"] == "html"
    assert saved["parser_trace"]["html_title"] == "Mucaro Web Fixture"
    assert saved["parser_trace"]["html_heading_count"] == 1
    assert saved["parser_trace"]["html_headings"] == ["Governed browsing"]
    assert saved["parser_trace"]["html_link_count"] == 1
    assert saved["parser_trace"]["html_link_samples"] == ["/source"]
    assert saved["parser_trace"]["html_meta_names"] == ["author"]
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


def test_ingest_web_snapshot_makes_snapshot_citeable(tmp_path: Path):
    cfg = make_settings(tmp_path)
    server, url = _serve_fixture()
    try:
        fetched = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
        )
    finally:
        server.shutdown()

    result = ingest_web_snapshot(Path(fetched["snapshot_path"]), cfg)

    assert result["status"] == "WEB_SNAPSHOT_INGEST_OK"
    assert result["citation_ready"] is True
    assert result["chunks_added"] == 1
    assert result["source"]["parser_metadata"]["source_format"] == "web_snapshot"
    assert result["source"]["parser_metadata"]["final_url"] == url
    assert result["source"]["parser_metadata"]["snapshot_sha256"] == fetched["snapshot_sha256"]
    assert result["source"]["parser_metadata"]["cache_policy"]["cache_control"] == "max-age=60"
    assert result["source"]["parser_metadata"]["robots_policy"]["status"] == "ROBOTS_ALLOWED"
    assert result["source"]["parser_metadata"]["parser_trace"]["html_title"] == "Mucaro Web Fixture"
    sources = get_sources(cfg)
    chunks = get_chunks(cfg)
    assert sources[0]["source_id"] == result["source"]["source_id"]
    assert chunks[0]["source_format"] == "web_snapshot"
    assert chunks[0]["source_url"] == url
    assert chunks[0]["snapshot_sha256"] == fetched["snapshot_sha256"]
    assert chunks[0]["content_sha256"] == result["source"]["parser_metadata"]["content_sha256"]


def test_fetch_web_snapshot_blocks_robots_disallowed_path(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_robots_body = StaticHandler.robots_body
    StaticHandler.robots_body = b"User-agent: *\nDisallow: /fixture.html\n"
    server, url = _serve_fixture()
    try:
        try:
            fetch_web_snapshot(
                url,
                cfg=cfg,
                policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
            )
        except WebBrowsingError as exc:
            assert exc.status == "ERROR_WEB_ROBOTS_DISALLOWED"
        else:
            raise AssertionError("expected robots disallow failure")
    finally:
        server.shutdown()
        StaticHandler.robots_body = original_robots_body


def test_fetch_web_snapshot_fails_http_error_without_explicit_snapshot_policy(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_status = StaticHandler.response_status
    StaticHandler.response_status = 404
    server, url = _serve_fixture()
    try:
        try:
            fetch_web_snapshot(
                url,
                cfg=cfg,
                policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
            )
        except WebBrowsingError as exc:
            assert exc.status == "ERROR_WEB_HTTP_STATUS"
        else:
            raise AssertionError("expected default HTTP error failure")
    finally:
        server.shutdown()
        StaticHandler.response_status = original_status


def test_fetch_web_snapshot_can_save_uncitable_http_error_page(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_status = StaticHandler.response_status
    StaticHandler.response_status = 404
    server, url = _serve_fixture()
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(
                timeout_seconds=5,
                max_bytes=1000,
                allow_private_hosts=True,
                snapshot_http_errors=True,
            ),
        )
    finally:
        server.shutdown()
        StaticHandler.response_status = original_status

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert saved["snapshot_kind"] == "http_error_page"
    assert saved["http_status"] == 404
    assert saved["http_error_policy"]["snapshot_allowed"] is True
    assert saved["http_error_policy"]["citation_allowed"] is False
    ingest_result = ingest_web_snapshot(Path(result["snapshot_path"]), cfg)
    assert ingest_result["status"] == "ERROR_WEB_SNAPSHOT_NOT_CITABLE"


def test_fetch_web_snapshot_can_save_citable_http_error_page_with_explicit_policy(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_status = StaticHandler.response_status
    StaticHandler.response_status = 404
    server, url = _serve_fixture()
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(
                timeout_seconds=5,
                max_bytes=1000,
                allow_private_hosts=True,
                snapshot_http_errors=True,
                cite_http_error_pages=True,
            ),
        )
    finally:
        server.shutdown()
        StaticHandler.response_status = original_status

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert saved["snapshot_kind"] == "http_error_page"
    assert saved["http_error_policy"]["citation_allowed"] is True
    ingest_result = ingest_web_snapshot(Path(result["snapshot_path"]), cfg)
    assert ingest_result["status"] == "WEB_SNAPSHOT_INGEST_OK"
    assert ingest_result["citation_ready"] is True


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
            "target_robots_policy": {
                "status": "ROBOTS_ALLOWED",
                "robots_url": second_url.rsplit("/", 1)[0] + "/robots.txt",
                "robots_final_url": second_url.rsplit("/", 1)[0] + "/robots.txt",
                "allowed": True,
                "checked_at": saved["redirect_chain"][0]["target_robots_policy"]["checked_at"],
                "http_status": 200,
                "content_sha256": saved["redirect_chain"][0]["target_robots_policy"]["content_sha256"],
                "content_size_bytes": len(StaticHandler.robots_body),
                "warnings": [],
            },
        }
    ]


def test_fetch_web_snapshot_blocks_redirect_target_disallowed_by_robots(tmp_path: Path):
    cfg = make_settings(tmp_path)
    target_handler = _make_handler(robots_body=b"User-agent: *\nDisallow: /fixture.html\n")
    second_server, second_url = _serve_fixture(handler_cls=target_handler)
    redirect_handler = _make_handler(redirect_to=second_url)
    first_server, first_url = _serve_fixture("/redirect", handler_cls=redirect_handler)
    try:
        try:
            fetch_web_snapshot(
                first_url,
                cfg=cfg,
                policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
            )
        except WebBrowsingError as exc:
            assert exc.status == "ERROR_WEB_ROBOTS_DISALLOWED"
        else:
            raise AssertionError("expected redirect-target robots disallow failure")
    finally:
        first_server.shutdown()
        second_server.shutdown()


def test_fetch_web_snapshot_records_absent_cache_headers_as_snapshot_only(tmp_path: Path):
    cfg = make_settings(tmp_path)
    handler = _make_handler(cache_control="")
    server, url = _serve_fixture(handler_cls=handler)
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=1000, allow_private_hosts=True),
        )
    finally:
        server.shutdown()

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert saved["cache_policy"]["cache_control"] == ""
    assert saved["cache_policy"]["header_status"] == "CACHE_HEADERS_ABSENT"
    assert saved["cache_policy"]["reuse_allowed"] is False
    assert saved["cache_policy"]["warnings"] == ["CACHE_HEADERS_ABSENT_IMMUTABLE_SNAPSHOT_ONLY"]


def test_fetch_web_snapshot_records_pdf_parser_trace(tmp_path: Path):
    from pypdf import PdfWriter  # type: ignore

    cfg = make_settings(tmp_path)
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    handler = _make_handler(body=buffer.getvalue(), content_type="application/pdf", cache_control="no-store")
    server, url = _serve_fixture(handler_cls=handler)
    try:
        result = fetch_web_snapshot(
            url,
            cfg=cfg,
            policy=WebFetchPolicy(timeout_seconds=5, max_bytes=2000, allow_private_hosts=True),
        )
    finally:
        server.shutdown()

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert saved["text_preview"] == ""
    assert saved["parser_trace"]["content_kind"] == "pdf"
    assert saved["parser_trace"]["pdf_page_count"] == 1
    assert saved["parser_trace"]["pdf_pages_with_empty_text"] == 1
    assert saved["parser_trace"]["pdf_failed_pages"] == 0
    assert saved["parser_trace"]["trace_warnings"] == ["PDF_TRACE_PAGE_EMPTY:1"]


def test_fetch_web_snapshot_preserves_binary_bytes_without_text_preview(tmp_path: Path):
    cfg = make_settings(tmp_path)
    original_body = StaticHandler.body
    original_content_type = StaticHandler.content_type
    original_robots_body = StaticHandler.robots_body
    original_response_status = StaticHandler.response_status
    StaticHandler.body = b"\x89PNG\r\n\x1a\n\x00\x00"
    StaticHandler.content_type = "image/png"
    StaticHandler.robots_body = b"User-agent: *\nAllow: /\n"
    StaticHandler.response_status = 200
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
        StaticHandler.robots_body = original_robots_body
        StaticHandler.response_status = original_response_status

    saved = json.loads(Path(result["snapshot_path"]).read_text(encoding="utf-8"))
    assert base64.b64decode(saved["content_base64"]) == b"\x89PNG\r\n\x1a\n\x00\x00"
    assert saved["text_preview"] == ""
    assert saved["charset"] == ""
    assert saved["parser_warnings"] == ["WEB_CONTENT_NOT_TEXT_PREVIEW_SKIPPED"]
    assert saved["parser_trace"]["content_kind"] == "non_text"
