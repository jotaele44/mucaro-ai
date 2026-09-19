from pathlib import Path
import subprocess

import pytest

import backend.app.macos_distribution as distribution
from backend.app.macos_distribution import MacOSDistributionError, verify_signed_notarized_macos_app


def fake_success(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout="ok")


def test_macos_distribution_requires_macos(tmp_path: Path) -> None:
    app=tmp_path/"Mucaro.app"; app.mkdir()
    with pytest.raises(MacOSDistributionError) as exc:
        verify_signed_notarized_macos_app(app, expected_head="a"*40, platform_name="linux")
    assert exc.value.status == "ERROR_MACOS_DISTRIBUTION_PLATFORM"


def test_verifier_emits_external_release_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app=tmp_path/"Mucaro.app"; app.mkdir()
    monkeypatch.setattr(
        distribution,
        "verify_portable_macos_app",
        lambda path, expected_head=None: {
            "status":"PORTABLE_APP_VERIFIED",
            "manifest_sha256":"b"*64,
            "failed_checks":[],
        },
    )
    out=tmp_path/"signing.json"
    receipt=verify_signed_notarized_macos_app(
        app,
        expected_head="a"*40,
        output_path=out,
        platform_name="darwin",
        runner=fake_success,
    )
    assert receipt["status"] == "MACOS_DISTRIBUTION_VERIFIED"
    assert receipt["evidence"] == {"signed":True,"notarized":True,"installer_verified":True}
    assert receipt["head_sha"] == "a"*40
    assert out.is_file()


def test_any_failed_distribution_check_blocks_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app=tmp_path/"Mucaro.app"; app.mkdir()
    monkeypatch.setattr(
        distribution,
        "verify_portable_macos_app",
        lambda path, expected_head=None: {
            "status":"PORTABLE_APP_VERIFIED",
            "manifest_sha256":"b"*64,
            "failed_checks":[],
        },
    )
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        rc = 1 if command[:2] == ["xcrun","stapler"] else 0
        return subprocess.CompletedProcess(command, rc, stdout="fixture")
    receipt=verify_signed_notarized_macos_app(
        app,
        expected_head="a"*40,
        platform_name="darwin",
        runner=runner,
    )
    assert receipt["status"] == "MACOS_DISTRIBUTION_BLOCKED"
    assert receipt["evidence"]["signed"] is True
    assert receipt["evidence"]["notarized"] is False
