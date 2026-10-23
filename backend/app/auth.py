from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Mapping
from typing import Any

from fastapi import Header, HTTPException

from .config import Settings, settings
from .error_categories import classify_error

REMOTE_ACCESS_STATUS = "ERROR_REMOTE_ACCESS_REQUIRES_TOKEN"


def auth_status(cfg: Settings = settings) -> dict[str, Any]:
    return {
        "status": "AUTH_REQUIRED" if cfg.operator_token else "AUTH_DISABLED",
        "auth_required": bool(cfg.operator_token),
        "accepted_headers": ["Authorization: Bearer <token>", "X-Mucaro-Token: <token>"],
    }


def require_operator_auth(
    cfg: Settings = settings,
    *,
    authorization: str | None = Header(default=None),
    x_mucaro_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if not cfg.operator_token:
        return {"status": "AUTH_DISABLED"}
    bearer_prefix = "Bearer "
    supplied = x_mucaro_token or ""
    if authorization and authorization.startswith(bearer_prefix):
        supplied = authorization[len(bearer_prefix):].strip()
    if supplied and hmac.compare_digest(supplied.encode("utf-8"), cfg.operator_token.encode("utf-8")):
        return {"status": "AUTH_OK"}
    raise HTTPException(
        status_code=401,
        detail={
            "status": "ERROR_OPERATOR_AUTH_REQUIRED",
            "category": classify_error("ERROR_OPERATOR_AUTH_REQUIRED"),
            "detail": "Operator token required for this local action.",
        },
    )


def client_is_remote(scope: Mapping[str, Any]) -> bool:
    """True when the peer is a non-loopback IP address: a phone or another computer on the network.

    A real server always reports an address. Anything that is not one (the ASGI test client reports "testclient")
    and a missing peer (a unix socket) count as local, because there is no remote address to protect against.
    IPv4-mapped IPv6 peers are unwrapped explicitly, so the answer does not depend on the interpreter's patch level.
    """
    client = scope.get("client")
    if not client:
        return False
    host = str(client[0]).split("%", 1)[0].strip()  # drop an IPv6 zone id such as fe80::1%en0
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return not address.is_loopback


def require_remote_token(cfg: Settings, scope: Mapping[str, Any]) -> None:
    """Refuse a remote peer when no token is configured: reaching this service from another device needs one.

    Loopback is unchanged. The startup profile enforces the same rule for a public bind, but a service started
    directly with `uvicorn --host 0.0.0.0` never passes through it, so the request path checks the peer itself.
    """
    if cfg.operator_token or not client_is_remote(scope):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "status": REMOTE_ACCESS_STATUS,
            "category": classify_error(REMOTE_ACCESS_STATUS),
            "detail": (
                "Reaching Mucaro from another device needs an access token. "
                "Start it with phone access on the computer (see docs/PHONE_ACCESS.md)."
            ),
        },
    )


# Status-only routes that must stay reachable so a client can discover that a
# token is required. Every other API route, including reads of local data
# (memory, chunks, runs, tool calls, exports), requires the token when one is set.
PUBLIC_API_PATHS = frozenset({"/health", "/config", "/security", "/tools"})


def is_public_api_path(path: str) -> bool:
    return path in PUBLIC_API_PATHS
