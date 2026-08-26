from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException

from .config import Settings, settings


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
    if supplied == cfg.operator_token:
        return {"status": "AUTH_OK"}
    raise HTTPException(
        status_code=401,
        detail={
            "status": "ERROR_OPERATOR_AUTH_REQUIRED",
            "detail": "Operator token required for this local mutating action.",
        },
    )
