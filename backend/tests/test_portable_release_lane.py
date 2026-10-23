import json
from pathlib import Path

import scripts.certify_portable_release_lane as cert


def _app(path: Path, head: str, version: str) -> None:
    (path / "Contents/Resources").mkdir(parents=True)
    (path / "Contents/Resources/portable-manifest.json").write_text(
        json.dumps({"head_sha": head, "release_version": version}) + "\n",
        encoding="utf-8",
    )


def test_arm64_portable_lane_requires_all_release_checks(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    _app(current, "a" * 40, "0.1.24")
    _app(previous, "b" * 40, "0.1.23")
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text(
        json.dumps({"status": "PORTABLE_RUNTIME_SOURCE_READY"}) + "\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "macos-arm64.json"
    manifest.write_text(json.dumps({"target": {"machine": "arm64"}}) + "\n", encoding="utf-8")
    clean = tmp_path / "clean.json"
    clean.write_text(
        json.dumps(
            {
                "schema_version": "mucaro-external-release-evidence/1.0",
                "status": "PACKAGED_LIFECYCLE_VERIFIED",
                "head_sha": "a" * 40,
                "evidence": {
                    "install": True,
                    "launch": True,
                    "representative_workflow": True,
                    "post_launch_bundle_immutable": True,
                    "reinstall": True,
                    "rollback": True,
                },
                "clean_machine": {"current_version": "0.1.24", "previous_version": "0.1.23"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cert,
        "verify_portable_macos_app",
        lambda app, expected_head=None: {"status": "PORTABLE_APP_VERIFIED", "manifest_sha256": "c" * 64},
    )
    monkeypatch.setattr(
        cert,
        "verify_materialized_runtime",
        lambda root, runtime_dir, manifest_path: {
            "status": "PORTABLE_RUNTIME_VERIFIED",
            "failed_checks": [],
            "runtime_tree": {"tree_sha256": "d" * 64},
            "probe": {"machine": "arm64"},
        },
    )
    monkeypatch.setattr(cert, "ROOT", tmp_path)

    receipt = cert.certify_portable_lane(
        current_app=current,
        previous_app=previous,
        runtime_dir=runtime,
        runtime_manifest=manifest,
        clean_machine_receipt=clean,
        expected_head="a" * 40,
        expected_architecture="arm64",
    )

    assert receipt["status"] == cert.STATUS_OK
    assert all(receipt["checks"].values())
    assert receipt["release_versions"] == {"current": "0.1.24", "previous": "0.1.23"}
    assert receipt["scope"] == {
        "runtime_manifest": "macos-arm64.json",
        "runtime_architecture": "arm64",
        "clean_host_certified": False,
        "physical_input_certified": False,
        "signing_notarization_certified": False,
    }


def test_portable_lane_rejects_same_version(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    _app(current, "a" * 40, "0.1.24")
    _app(previous, "b" * 40, "0.1.24")
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text('{"status":"PORTABLE_RUNTIME_SOURCE_READY"}\n', encoding="utf-8")
    manifest = tmp_path / "macos-arm64.json"
    manifest.write_text('{"target":{"machine":"arm64"}}\n', encoding="utf-8")
    clean = tmp_path / "clean.json"
    clean.write_text(
        json.dumps(
            {
                "schema_version": "mucaro-external-release-evidence/1.0",
                "status": "PACKAGED_LIFECYCLE_VERIFIED",
                "head_sha": "a" * 40,
                "evidence": {
                    "install": True,
                    "launch": True,
                    "representative_workflow": True,
                    "post_launch_bundle_immutable": True,
                    "reinstall": True,
                    "rollback": True,
                },
                "clean_machine": {"current_version": "0.1.24", "previous_version": "0.1.24"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda *args, **kwargs: {"status": "PORTABLE_APP_VERIFIED", "manifest_sha256": "c" * 64})
    monkeypatch.setattr(cert, "verify_materialized_runtime", lambda *args, **kwargs: {"status": "PORTABLE_RUNTIME_VERIFIED", "failed_checks": []})
    monkeypatch.setattr(cert, "ROOT", tmp_path)

    receipt = cert.certify_portable_lane(
        current_app=current,
        previous_app=previous,
        runtime_dir=runtime,
        runtime_manifest=manifest,
        clean_machine_receipt=clean,
        expected_head="a" * 40,
        expected_architecture="arm64",
    )

    assert receipt["status"] == cert.STATUS_FAILED
    assert receipt["checks"]["distinct_version_upgrade_rollback"] is False


def test_portable_lane_rejects_wrong_runtime_architecture(tmp_path: Path, monkeypatch) -> None:
    current = tmp_path / "current.app"
    previous = tmp_path / "previous.app"
    runtime = tmp_path / "runtime"
    _app(current, "a" * 40, "0.1.24")
    _app(previous, "b" * 40, "0.1.23")
    runtime.mkdir()
    (runtime / "runtime-provenance.json").write_text('{"status":"PORTABLE_RUNTIME_SOURCE_READY"}\n', encoding="utf-8")
    manifest = tmp_path / "macos-x86_64.json"
    manifest.write_text('{"target":{"machine":"x86_64"}}\n', encoding="utf-8")
    clean = tmp_path / "clean.json"
    clean.write_text(
        json.dumps(
            {
                "schema_version": "mucaro-external-release-evidence/1.0",
                "status": "PACKAGED_LIFECYCLE_VERIFIED",
                "head_sha": "a" * 40,
                "evidence": {
                    "install": True,
                    "launch": True,
                    "representative_workflow": True,
                    "post_launch_bundle_immutable": True,
                    "reinstall": True,
                    "rollback": True,
                },
                "clean_machine": {"current_version": "0.1.24", "previous_version": "0.1.23"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cert, "verify_portable_macos_app", lambda *args, **kwargs: {"status": "PORTABLE_APP_VERIFIED", "manifest_sha256": "c" * 64})
    monkeypatch.setattr(cert, "verify_materialized_runtime", lambda *args, **kwargs: {"status": "PORTABLE_RUNTIME_VERIFIED", "failed_checks": []})
    monkeypatch.setattr(cert, "ROOT", tmp_path)

    receipt = cert.certify_portable_lane(
        current_app=current,
        previous_app=previous,
        runtime_dir=runtime,
        runtime_manifest=manifest,
        clean_machine_receipt=clean,
        expected_head="a" * 40,
        expected_architecture="arm64",
    )

    assert receipt["status"] == cert.STATUS_FAILED
    assert receipt["checks"]["runtime_architecture"] is False
