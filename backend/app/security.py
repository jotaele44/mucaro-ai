from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .distribution_policy import (
    MAC_APP_STORE_DISTRIBUTION_CHANNEL,
    SUPPORTED_DISTRIBUTION_CHANNELS,
)

SUPPORTED_DEPLOYMENT_PROFILES = {"local", "production"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MIN_PRODUCTION_TOKEN_CHARS = 32


class SecurityProfileError(RuntimeError):
    def __init__(self, status: str, detail: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.report = report or {}


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _is_public_host(host: str | None) -> bool:
    if not host:
        return False
    return host.strip().lower() not in LOOPBACK_HOSTS


def build_security_report(cfg: Settings = settings, *, bind_host: str | None = None) -> dict[str, Any]:
    profile = cfg.deployment_profile
    checks = []
    checks.append(
        _check(
            "deployment_profile",
            "PASS" if profile in SUPPORTED_DEPLOYMENT_PROFILES else "FAIL",
            profile,
        )
    )
    distribution_channel = cfg.distribution_channel
    checks.append(
        _check(
            "distribution_channel",
            "PASS" if distribution_channel in SUPPORTED_DISTRIBUTION_CHANNELS else "FAIL",
            distribution_channel,
        )
    )
    checks.append(
        _check(
            "app_store_production_profile",
            (
                "PASS"
                if distribution_channel != MAC_APP_STORE_DISTRIBUTION_CHANNEL or profile == "production"
                else "FAIL"
            ),
            "production required" if distribution_channel == MAC_APP_STORE_DISTRIBUTION_CHANNEL else "not applicable",
        )
    )

    token_length = len(cfg.operator_token or "")
    if profile == "production":
        token_status = "PASS" if token_length >= MIN_PRODUCTION_TOKEN_CHARS else "FAIL"
    else:
        token_status = "PASS" if cfg.operator_token else "WARN"
    checks.append(
        _check(
            "operator_token",
            token_status,
            "configured" if cfg.operator_token else "missing",
        )
    )

    cors_origins = list(cfg.cors_origins)
    unsafe_cors = "*" in cors_origins
    checks.append(
        _check(
            "cors_origins",
            "FAIL" if profile == "production" and unsafe_cors else "PASS",
            ",".join(cors_origins) or "none",
        )
    )

    public_bind = _is_public_host(bind_host)
    if public_bind and distribution_channel == MAC_APP_STORE_DISTRIBUTION_CHANNEL:
        bind_status = "FAIL"
        bind_detail = f"{bind_host} is forbidden for Mac App Store distribution"
    elif public_bind and profile == "local" and not cfg.allow_public_bind:
        bind_status = "FAIL"
        bind_detail = f"{bind_host} requires MUCARO_AI_ALLOW_PUBLIC_BIND=1 or production profile"
    else:
        bind_status = "PASS"
        bind_detail = bind_host or "not supplied"
    checks.append(_check("bind_host", bind_status, bind_detail))

    failed = [check for check in checks if check["status"] == "FAIL"]
    warned = [check for check in checks if check["status"] == "WARN"]
    status = "SECURITY_FAIL" if failed else "SECURITY_WARN" if warned else "SECURITY_OK"
    return {
        "status": status,
        "deployment_profile": profile,
        "distribution_channel": distribution_channel,
        "production_ready": profile == "production" and status == "SECURITY_OK",
        "public_bind_allowed": bool(cfg.allow_public_bind),
        "checks": checks,
        "recommendations": _recommendations(profile, failed, warned),
    }


def _recommendations(profile: str, failed: list[dict[str, str]], warned: list[dict[str, str]]) -> list[str]:
    names = {item["name"] for item in failed + warned}
    recommendations = []
    if profile not in SUPPORTED_DEPLOYMENT_PROFILES:
        recommendations.append("Set MUCARO_AI_DEPLOYMENT_PROFILE to local or production.")
    if "distribution_channel" in names:
        recommendations.append("Set MUCARO_AI_DISTRIBUTION_CHANNEL to local or mac_app_store.")
    if "app_store_production_profile" in names:
        recommendations.append("Use MUCARO_AI_DEPLOYMENT_PROFILE=production for the Mac App Store channel.")
    if "operator_token" in names:
        recommendations.append("Set MUCARO_AI_OPERATOR_TOKEN to a high-entropy value before production exposure.")
    if "cors_origins" in names:
        recommendations.append("Set MUCARO_AI_CORS_ORIGINS to explicit trusted browser origins.")
    if "bind_host" in names:
        recommendations.append("Keep local mode on loopback, or explicitly opt into public binding.")
    return sorted(set(recommendations))


def enforce_startup_security(cfg: Settings = settings, *, bind_host: str | None = None) -> dict[str, Any]:
    report = build_security_report(cfg, bind_host=bind_host)
    if report["status"] == "SECURITY_FAIL":
        failed = [check for check in report["checks"] if check["status"] == "FAIL"]
        detail = "; ".join(f"{check['name']}={check['detail']}" for check in failed)
        raise SecurityProfileError("ERROR_SECURITY_PROFILE_NOT_READY", detail, report)
    return report
