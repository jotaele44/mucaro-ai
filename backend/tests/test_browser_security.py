import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app.browser_security import BrowserSecurityMiddleware
from backend.app.main import create_app
from backend.tests.test_security import make_settings


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/config", None),
    ("POST", "/plan", {"user_request": "Write a file"}),
    ("OPTIONS", "/health", None),
])
def test_untrusted_origin_rejected_before_routing(tmp_path, method, path, payload):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.request(method, path, json=payload, headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "ERROR_UNTRUSTED_BROWSER_REQUEST"


@pytest.mark.parametrize("host", ["attacker.example", "localhost.attacker.example", "127.0.0.1.attacker.example"])
def test_rebound_hostname_rejected_even_without_origin(tmp_path, host):
    client = TestClient(create_app(make_settings(tmp_path)), client=("127.0.0.1", 12345))
    assert client.get("/config", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_loopback_same_origin_browser_access(tmp_path, host):
    client = TestClient(create_app(make_settings(tmp_path)), client=("127.0.0.1", 12345))
    response = client.get("/config", headers={"Host": host, "Origin": f"http://{host}"})
    assert response.status_code == 200


def test_phone_same_origin_still_requires_operator_token(tmp_path):
    token = "t" * 40
    client = TestClient(create_app(make_settings(tmp_path, operator_token=token)), client=("192.168.1.5", 12345))
    headers = {"Host": "192.168.1.2:8000", "Origin": "http://192.168.1.2:8000"}
    assert client.get("/conversations", headers=headers).status_code == 401
    headers["X-Mucaro-Token"] = token
    assert client.get("/conversations", headers=headers).status_code == 200


def test_duplicate_origin_headers_rejected(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    response = client.get("/health", headers=[("Origin", "http://127.0.0.1:5173")] * 2)
    assert response.status_code == 403


def test_unauthorized_upload_rejected_without_reading_body(tmp_path):
    messages = []

    async def never_read():
        pytest.fail("Unauthenticated upload body was consumed")

    async def never_route(scope, receive, send):
        pytest.fail("Unauthenticated upload reached multipart parsing")

    async def send(message):
        messages.append(message)

    guard = BrowserSecurityMiddleware(never_route, make_settings(tmp_path, operator_token="t" * 40))
    asyncio.run(guard({
        "type": "http", "method": "POST", "path": "/ingest", "scheme": "http",
        "client": ("127.0.0.1", 12345), "headers": [(b"host", b"127.0.0.1:8000")],
    }, never_read, send))
    assert messages[0]["status"] == 401
