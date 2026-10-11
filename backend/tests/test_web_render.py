import socket
from pathlib import Path

import pytest

import backend.app.web_render as render
from backend.app.config import Settings
from backend.app.web_render import WebRenderError, render_web_page


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these tests hermetic: public names resolve to a documentation-range
    public address without touching the network (SD-005)."""
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, *args, **kwargs):
        if host in {"example.com", "www.example.com"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data/input",
        processed_dir=root / "data/processed",
        export_dir=root / "data/exports",
        index_dir=root / "data/indexes",
        sqlite_path=root / "data/processed/test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def test_render_requires_explicit_live_permission(tmp_path: Path) -> None:
    with pytest.raises(WebRenderError) as exc:
        render_web_page("https://example.com", cfg=make_settings(tmp_path))
    assert exc.value.status == "ERROR_WEB_RENDER_NOT_ALLOWED"


def test_render_receipt_binds_engine_dom_and_screenshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        render,
        "_render_with_playwright",
        lambda url, timeout_ms: {
            "engine": "playwright_chromium",
            "engine_version": "fixture-1",
            "final_url": "https://example.com/final",
            "http_status": 200,
            "title": "Fixture",
            "dom_bytes": b"<html><body>rendered</body></html>",
            "screenshot_bytes": b"PNG-FIXTURE",
        },
    )
    result = render_web_page(
        "https://example.com",
        cfg=make_settings(tmp_path),
        allow_rendered_browser=True,
        max_dom_bytes=10_000,
    )
    receipt = result["receipt"]
    assert receipt["status"] == "WEB_RENDER_OK"
    assert receipt["engine"]["name"] == "playwright_chromium"
    assert receipt["engine"]["version"] == "fixture-1"
    assert receipt["final_url"] == "https://example.com/final"
    assert receipt["citation_policy"]["citation_ready"] is False
    assert receipt["invariants"]["dom_within_ceiling"] is True
    assert len(receipt["dom"]["content_sha256"]) == 64
    assert len(receipt["screenshot"]["sha256"]) == 64
    assert Path(receipt["screenshot"]["path"]).read_bytes() == b"PNG-FIXTURE"


def test_render_rejects_private_hosts_before_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake(url, timeout_ms):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(render, "_render_with_playwright", fake)
    with pytest.raises(WebRenderError):
        render_web_page(
            "http://127.0.0.1/private",
            cfg=make_settings(tmp_path),
            allow_rendered_browser=True,
        )
    assert called is False


def test_render_rejects_oversized_dom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        render,
        "_render_with_playwright",
        lambda url, timeout_ms: {
            "engine": "playwright_chromium",
            "engine_version": "fixture-1",
            "final_url": url,
            "http_status": 200,
            "title": "Fixture",
            "dom_bytes": b"x" * 2048,
            "screenshot_bytes": b"png",
        },
    )
    with pytest.raises(WebRenderError) as exc:
        render_web_page(
            "https://example.com",
            cfg=make_settings(tmp_path),
            allow_rendered_browser=True,
            max_dom_bytes=1024,
        )
    assert exc.value.status == "ERROR_WEB_RENDER_DOM_TOO_LARGE"
