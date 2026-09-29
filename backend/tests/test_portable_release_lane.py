import json
from pathlib import Path

import scripts.certify_portable_release_lane as cert


def test_json_loader_rejects_non_object_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text("[]\n", encoding="utf-8")

    try:
        cert._json(receipt)
    except TypeError as exc:
        assert "expected JSON object" in str(exc)
    else:
        raise AssertionError("non-object JSON must fail closed")


def test_portable_lane_requires_all_release_checks(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    for app, head, version in ((current, "a"*40, "0.1.24"), (previous, "b"*40, "0.1.23")):
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/portable-manifest.json").write_text(
            json.dumps({"head_sha": head, "release_version": version}) + "\n", encoding="utf-8"
        )
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text(
        json.dumps({"status": "PORTABLE_RUNTIME_SOURCE_READY"}) + "\n", encoding="utf-8"
    )
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({
        "schema_version": "mucaro-external-release-evidence/1.0",
        "status": "PACKAGED_LIFECYCLE_VERIFIED",
        "head_sha": "a"*40,
        "evidence": {
            "install": True,
            "launch": True,
            "representative_workflow": True,
            "post_launch_bundle_immutable": True,
            "reinstall": True,
            "rollback": True,
        },
        "clean_machine": {
            "current_version": "0.1.24",
            "previous_version": "0.1.23",
        },
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda app, expected_head=None: {
        "status": "PORTABLE_APP_VERIFIED",
        "manifest_sha256": "c"*64,
    })
    monkeypatch.setattr(cert, "verify_materialized_runtime", lambda root, runtime_dir, manifest_path: {
        "status": "PORTABLE_RUNTIME_VERIFIED",
        "failed_checks": [],
    })

    receipt = cert.certify_portable_lane(
        current_app=current, previous_app=previous, runtime_dir=runtime,
        clean_machine_receipt=clean, expected_head="a"*40,
    )
    assert receipt["status"] == cert.STATUS_OK
    assert all(receipt["checks"].values())
    assert receipt["release_versions"] == {"current": "0.1.24", "previous": "0.1.23"}
    assert receipt["execution_environment"]["github_actions"] is False
    assert receipt["execution_environment"]["platform"]
    assert receipt["execution_environment"]["machine"]
    assert receipt["checks"]["packaged_lifecycle_macos"] is True
    assert "clean_machine_macos" not in receipt["checks"]
    assert receipt["scope"] == {
        "runtime_manifest": "packaging/runtime/macos-x86_64.json",
        "runtime_architecture": "x86_64",
        "clean_host_certified": False,
        "physical_input_certified": False,
        "signing_notarization_certified": False,
    }


def test_portable_lane_blocks_same_version_rollback(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    for app, head in ((current, "a"*40), (previous, "b"*40)):
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/portable-manifest.json").write_text(
            json.dumps({"head_sha": head, "release_version": "0.1.24"}) + "\n", encoding="utf-8"
        )
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text(
        json.dumps({"status": "PORTABLE_RUNTIME_SOURCE_READY"}) + "\n", encoding="utf-8"
    )
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({
        "schema_version": "mucaro-external-release-evidence/1.0",
        "status": "PACKAGED_LIFECYCLE_VERIFIED",
        "head_sha": "a"*40,
        "evidence": {
            "install": True, "launch": True, "representative_workflow": True,
            "post_launch_bundle_immutable": True, "reinstall": True, "rollback": True,
        },
        "clean_machine": {
            "current_version": "0.1.24",
            "previous_version": "0.1.24",
        },
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda app, expected_head=None: {
        "status": "PORTABLE_APP_VERIFIED", "manifest_sha256": "c"*64,
    })
    monkeypatch.setattr(cert, "verify_materialized_runtime", lambda root, runtime_dir, manifest_path: {
        "status": "PORTABLE_RUNTIME_VERIFIED",
        "failed_checks": [],
    })
    receipt = cert.certify_portable_lane(
        current_app=current, previous_app=previous, runtime_dir=runtime,
        clean_machine_receipt=clean, expected_head="a"*40,
    )
    assert receipt["status"] == cert.STATUS_FAILED
    assert receipt["checks"]["distinct_version_upgrade_rollback"] is False
    assert receipt["previous_head_sha"] != receipt["head_sha"]
    assert receipt["release_versions"] == {"current": "0.1.24", "previous": "0.1.24"}
