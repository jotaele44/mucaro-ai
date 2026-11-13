"""Reject browser requests before routing, including public and static routes.

CORS controls response visibility, not whether a request can mutate local data.
Tokenless loopback access also needs Host validation against DNS rebinding.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.responses import JSONResponse

from .auth import client_is_remote, require_operator_auth, require_remote_token
from .config import Settings


def _loopback_host(host: str) -> bool:
    try:
        parsed = urlsplit("http://" + host)
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        hostname = parsed.hostname or ""
        _ = parsed.port  # validate the port as well as the hostname
        if hostname == "localhost":
            return True
        address = ipaddress.ip_address(hostname)
        return (getattr(address, "ipv4_mapped", None) or address).is_loopback
    except ValueError:
        return False


class BrowserSecurityMiddleware:
    def __init__(self, app: object, cfg: Settings) -> None:
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)  # type: ignore[operator]
            return
        headers = scope.get("headers", [])
        hosts = [value.decode("latin-1") for key, value in headers if key.lower() == b"host"]
        origins = [value.decode("latin-1") for key, value in headers if key.lower() == b"origin"]
        host = hosts[0] if len(hosts) == 1 else ""
        # Starlette's in-process test transport has no network peer; this is not
        # an allowlist exception for requests received from real IP addresses.
        client = scope.get("client")
        test_transport = bool(client and client[0] == "testclient" and host == "testserver")
        untrusted_host = (
            not self.cfg.operator_token
            and not client_is_remote(scope)
            and not test_transport
            and not _loopback_host(host)
        )
        same_origin = f"{scope.get('scheme', 'http')}://{host}"
        untrusted_origin = len(origins) > 1 or bool(
            origins and origins[0] not in (*self.cfg.cors_origins, same_origin)
        )
        if untrusted_host or untrusted_origin:
            response = JSONResponse(
                status_code=403,
                content={"detail": {
                    "status": "ERROR_UNTRUSTED_BROWSER_REQUEST",
                    "detail": "Use a trusted browser origin and a loopback hostname for tokenless local access.",
                }},
            )
            await response(scope, receive, send)
            return
        # FastAPI parses request bodies before dependency authentication. Reject
        # unauthorized writes here so multipart uploads cannot consume disk or
        # parser resources first. Read routes retain their route dependencies.
        if scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            values = {key.lower(): value.decode("latin-1") for key, value in headers}
            try:
                require_remote_token(self.cfg, scope)
                require_operator_auth(
                    self.cfg,
                    authorization=values.get(b"authorization"),
                    x_mucaro_token=values.get(b"x-mucaro-token"),
                )
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)  # type: ignore[operator]
