from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from backend.app.cli import main
from backend.tests.test_coding_workspace import make_settings


def run(capsys: pytest.CaptureFixture[str], cfg, *argv: str) -> tuple[int, dict]:
    code = main(list(argv), cfg)
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


@pytest.fixture
def page_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b"<html><head><title>Hello</title></head><body><p>governed page</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/page"
    server.shutdown()


def test_web_commands_require_approval_and_block_private_hosts(tmp_path: Path, capsys: pytest.CaptureFixture[str], page_server: str) -> None:
    cfg = make_settings(tmp_path)

    code, err = run(capsys, cfg, "web", "fetch", page_server)
    assert code == 2 and err["status"] == "ERROR_WEB_CONFIRMATION_REQUIRED"

    code, err = run(capsys, cfg, "web", "fetch", page_server, "--yes")
    assert code == 2 and err["status"].startswith("ERROR_")

    code, err = run(capsys, cfg, "web", "search", "mucaro")
    assert code == 2 and err["status"] == "ERROR_WEB_CONFIRMATION_REQUIRED"

    code, err = run(capsys, cfg, "web", "search", "mucaro", "--yes")
    assert code == 2 and err["status"] == "ERROR_WEB_SEARCH_LIVE_NOT_ALLOWED"


def test_web_fetch_snapshots_loopback_when_explicitly_allowed(tmp_path: Path, capsys: pytest.CaptureFixture[str], page_server: str) -> None:
    cfg = make_settings(tmp_path)
    code, out = run(capsys, cfg, "web", "fetch", page_server, "--yes", "--allow-private-hosts", "--ignore-robots")
    assert code == 0, out
    assert "governed page" in json.dumps(out)
