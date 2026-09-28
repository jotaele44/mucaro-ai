import json
from pathlib import Path

import backend.app.portable_runtime as portable
import scripts.certify_portable_release_lane as cert


def test_portable_lane_requires_all_release_checks(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    for app, head in ((current, "a"*40), (previous, "b"*40)):
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/portable-manifest.json").write_text(
            json.dumps({"head_sha": head}) + "\n", encoding="utf-8"
        )
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text(
        json.dumps({"status": "PORTABLE_RUNTIME_VERIFIED"}) + "\n", encoding="utf-8"
    )
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({
        "schema_version": "mucaro-external-release-evidence/1.0",
        "status": "CLEAN_MACHINE_MACOS_VERIFIED",
        "head_sha": "a"*40,
        "evidence": {
            "install": True,
            "launch": True,
            "representative_workflow": True,
            "post_launch_bundle_immutable": True,
            "reinstall": True,
            "rollback": True,
        },
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda app, expected_head=None: {
        "status": "PORTABLE_APP_VERIFIED",
        "manifest_sha256": "c"*64,
    })

    receipt = cert.certify_portable_lane(
        current_app=current, previous_app=previous, runtime_dir=runtime,
        clean_machine_receipt=clean, expected_head="a"*40,
    )
    assert receipt["status"] == cert.STATUS_OK
    assert all(receipt["checks"].values())


def test_portable_lane_blocks_same_version_rollback(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    for app in (current, previous):
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/portable-manifest.json").write_text(
            json.dumps({"head_sha": "a"*40}) + "\n", encoding="utf-8"
        )
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text(
        json.dumps({"status": "PORTABLE_RUNTIME_VERIFIED"}) + "\n", encoding="utf-8"
    )
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({
        "schema_version": "mucaro-external-release-evidence/1.0",
        "status": "CLEAN_MACHINE_MACOS_VERIFIED",
        "head_sha": "a"*40,
        "evidence": {
            "install": True, "launch": True, "representative_workflow": True,
            "post_launch_bundle_immutable": True, "reinstall": True, "rollback": True,
        },
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda app, expected_head=None: {
        "status": "PORTABLE_APP_VERIFIED", "manifest_sha256": "c"*64,
    })
    receipt = cert.certify_portable_lane(
        current_app=current, previous_app=previous, runtime_dir=runtime,
        clean_machine_receipt=clean, expected_head="a"*40,
    )
    assert receipt["status"] == cert.STATUS_FAILED
    assert receipt["checks"]["distinct_version_upgrade_rollback"] is False
