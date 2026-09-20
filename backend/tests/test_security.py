from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.security import SecurityProfileError, build_security_report, enforce_startup_security


def make_settings(
    root: Path,
    *,
    operator_token: str | None = None,
    cors_origins: tuple[str, ...] = ("http://127.0.0.1:5173",),
    deployment_profile: str = "local",
    allow_public_bind: bool = False,
    distribution_channel: str = "local",
) -> Settings:
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
        operator_token=operator_token,
        cors_origins=cors_origins,
        deployment_profile=deployment_profile,
        allow_public_bind=allow_public_bind,
        distribution_channel=distribution_channel,
    )


def test_local_security_warns_without_operator_token_but_allows_startup(tmp_path: Path):
    cfg = make_settings(tmp_path)

    report = build_security_report(cfg)
    app = create_app(cfg)

    assert report["status"] == "SECURITY_WARN"
    assert report["production_ready"] is False
    assert app.title == "Mucaro AI"


def test_local_public_bind_fails_without_explicit_opt_in(tmp_path: Path):
    cfg = make_settings(tmp_path, operator_token="x" * 32)

    report = build_security_report(cfg, bind_host="0.0.0.0")

    assert report["status"] == "SECURITY_FAIL"
    assert any(check["name"] == "bind_host" and check["status"] == "FAIL" for check in report["checks"])


def test_local_public_bind_can_be_explicitly_allowed(tmp_path: Path):
    cfg = make_settings(tmp_path, operator_token="x" * 32, allow_public_bind=True)

    report = build_security_report(cfg, bind_host="0.0.0.0")

    assert report["status"] == "SECURITY_OK"


def test_production_security_requires_strong_operator_token_and_safe_cors(tmp_path: Path):
    cfg = make_settings(tmp_path, cors_origins=("*",), deployment_profile="production")

    report = build_security_report(cfg)

    assert report["status"] == "SECURITY_FAIL"
    assert {check["name"] for check in report["checks"] if check["status"] == "FAIL"} == {
        "operator_token",
        "cors_origins",
    }
    with pytest.raises(SecurityProfileError) as exc:
        enforce_startup_security(cfg)
    assert exc.value.status == "ERROR_SECURITY_PROFILE_NOT_READY"


def test_production_security_profile_passes_with_required_controls(tmp_path: Path):
    cfg = make_settings(
        tmp_path,
        operator_token="x" * 32,
        cors_origins=("https://mucaro.example",),
        deployment_profile="production",
    )

    report = build_security_report(cfg)

    assert report["status"] == "SECURITY_OK"
    assert report["production_ready"] is True


def test_app_store_channel_requires_production_and_loopback(tmp_path: Path):
    local_profile = make_settings(
        tmp_path,
        operator_token="x" * 32,
        distribution_channel="mac_app_store",
    )
    public_bind = make_settings(
        tmp_path,
        operator_token="x" * 32,
        deployment_profile="production",
        distribution_channel="mac_app_store",
        allow_public_bind=True,
    )

    local_report = build_security_report(local_profile, bind_host="127.0.0.1")
    public_report = build_security_report(public_bind, bind_host="0.0.0.0")

    assert local_report["status"] == "SECURITY_FAIL"
    assert any(
        check["name"] == "app_store_production_profile" and check["status"] == "FAIL"
        for check in local_report["checks"]
    )
    assert public_report["status"] == "SECURITY_FAIL"
    assert any(check["name"] == "bind_host" and check["status"] == "FAIL" for check in public_report["checks"])


def test_unknown_distribution_channel_fails_closed(tmp_path: Path):
    cfg = make_settings(tmp_path, distribution_channel="unknown")

    report = build_security_report(cfg)

    assert report["status"] == "SECURITY_FAIL"
    assert any(
        check["name"] == "distribution_channel" and check["status"] == "FAIL"
        for check in report["checks"]
    )
