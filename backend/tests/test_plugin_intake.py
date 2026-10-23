"""Plugin v1 catalog: positive identity and negative fail-closed intake regressions."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.plugin_intake import PluginIntakeError, list_plugins, stage_plugin


def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path, data_dir=tmp_path / "data", input_dir=tmp_path / "data/input",
        processed_dir=tmp_path / "data/processed", export_dir=tmp_path / "data/exports",
        index_dir=tmp_path / "data/indexes", sqlite_path=tmp_path / "data/processed/db.sqlite",
        default_model_provider="mock", default_model_name="mock",
    )


def manifest(**changes):
    base = {
        "host_api": "mucaro-plugin/1", "plugin_id": "example.offline-notes", "version": "1.0.0",
        "name": "Offline Notes", "description": "Local reference bundle", "capabilities": [],
        "contributions": [{"kind": "reference", "path": "notes.md"}],
    }
    return {**base, **changes}


def package(meta=None, *, extra=None, raw_manifest=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mucaro-plugin.json", raw_manifest if raw_manifest is not None else json.dumps(meta or manifest()))
        archive.writestr("notes.md", b"No external network required.")
        for key, value in (extra or {}).items():
            archive.writestr(key, value)
    return out.getvalue()


def failed(fn, expected):
    with pytest.raises(PluginIntakeError) as exc:
        fn()
    assert exc.value.status == expected


def test_stage_exact_hash_and_inert_catalog(tmp_path):
    cfg = settings(tmp_path)
    data = package()
    row = stage_plugin(data, cfg=cfg, expected_sha256=hashlib.sha256(data).hexdigest())["plugin"]
    assert row["state"] == "STAGED_DISABLED" and not row["execution_supported"]
    assert row["archive_sha256"] == hashlib.sha256(data).hexdigest()
    assert row["member_count"] == len(row["member_manifest"]) == 2
    assert list_plugins(cfg)["count"] == 1 and list_plugins(cfg)["active_count"] == 0
    assert list_plugins(cfg)["plugins"][0]["integrity"] == "PASS"
    assert stage_plugin(data, cfg=cfg)["status"] == "PLUGIN_ALREADY_STAGED"
    assert list_plugins(cfg)["count"] == 1


def test_distinct_version_coexists_without_identity_merging(tmp_path):
    cfg = settings(tmp_path)
    stage_plugin(package(), cfg=cfg)
    stage_plugin(package(manifest(version="1.0.1")), cfg=cfg)
    assert [(x["plugin_id"], x["version"]) for x in list_plugins(cfg)["plugins"]] == [
        ("example.offline-notes", "1.0.0"), ("example.offline-notes", "1.0.1")
    ]


def test_same_identity_different_bytes_refuses_collision(tmp_path):
    cfg = settings(tmp_path)
    stage_plugin(package(), cfg=cfg)
    failed(lambda: stage_plugin(package(manifest(description="Unrelated altered plugin")), cfg=cfg), "ERROR_PLUGIN_VERSION_COLLISION")
    assert list_plugins(cfg)["count"] == 1


@pytest.mark.parametrize("edited,status", [
    ({"host_api": "unsupported/5"}, "ERROR_PLUGIN_API_UNSUPPORTED"),
    ({"plugin_id": "invalid"}, "ERROR_PLUGIN_ID_INVALID"),
    ({"version": "v1"}, "ERROR_PLUGIN_VERSION_INVALID"),
    ({"capabilities": ["network.public_http"]}, "ERROR_PLUGIN_CAPABILITY_EXECUTION_NOT_SUPPORTED"),
    ({"contributions": [{"kind": "schema", "path": "notes.md"}]}, "ERROR_PLUGIN_CONTRIBUTION_KIND_MISMATCH"),
    ({"contributions": [{"kind": "reference", "path": "absent.md"}]}, "ERROR_PLUGIN_CONTRIBUTION_PATH_INVALID"),
])
def test_invalid_manifest_never_persisted(edited, status, tmp_path):
    cfg = settings(tmp_path)
    failed(lambda: stage_plugin(package(manifest(**edited)), cfg=cfg), status)
    assert list_plugins(cfg)["count"] == 0


def test_reject_undeclared_member_and_code(tmp_path):
    cfg = settings(tmp_path)
    failed(lambda: stage_plugin(package(extra={"unlisted.md": "hi"}), cfg=cfg), "ERROR_PLUGIN_UNDECLARED_MEMBER")
    failed(lambda: stage_plugin(package(extra={"code.py": "print('unsafe')"}), cfg=cfg), "ERROR_PLUGIN_UNSUPPORTED_PAYLOAD")


def test_reject_malformed_duplicate_key_and_hash(tmp_path):
    cfg = settings(tmp_path)
    raw = '{"host_api":"mucaro-plugin/1","host_api":"mucaro-plugin/2"}'
    failed(lambda: stage_plugin(package(raw_manifest=raw), cfg=cfg), "ERROR_PLUGIN_DUPLICATE_JSON_KEY")
    failed(lambda: stage_plugin(package(), cfg=cfg, expected_sha256="0" * 64), "ERROR_PLUGIN_SOURCE_HASH_MISMATCH")
    assert list_plugins(cfg)["count"] == 0


def test_reject_casefold_collision_and_traversal(tmp_path):
    cfg = settings(tmp_path)
    failed(lambda: stage_plugin(package(extra={"NOTES.md": "shadow"}), cfg=cfg), "ERROR_PLUGIN_CASE_COLLISION")
    failed(lambda: stage_plugin(package(extra={"../escape.md": "attack"}), cfg=cfg), "ERROR_PLUGIN_ARCHIVE_UNSAFE")


def test_tampered_payload_reported_not_silently_used(tmp_path):
    cfg = settings(tmp_path)
    row = stage_plugin(package(), cfg=cfg)["plugin"]
    archive = cfg.data_dir / "plugins/archives" / f"{row['archive_sha256']}.zip"
    archive.write_bytes(b"tampered")
    assert list_plugins(cfg)["plugins"][0]["integrity"] == "FAIL"
    failed(lambda: stage_plugin(package(), cfg=cfg), "ERROR_PLUGIN_ARCHIVE_CORRUPT")


def test_registry_corruption_fails_closed(tmp_path):
    cfg = settings(tmp_path)
    cfg.ensure_dirs()
    (cfg.processed_dir / "plugin_registry_v1.json").write_text('{"malformed": 1}', encoding="utf-8")
    failed(lambda: list_plugins(cfg), "ERROR_PLUGIN_REGISTRY_INVALID")


def test_oversized_package_and_invalid_zip(tmp_path):
    cfg = settings(tmp_path)
    failed(lambda: stage_plugin(b"x" * (10 * 1024 * 1024 + 1), cfg=cfg), "ERROR_PLUGIN_PACKAGE_SIZE")
    failed(lambda: stage_plugin(b"not a ZIP", cfg=cfg), "ERROR_PLUGIN_ARCHIVE_UNSAFE")


def test_registry_missing_fields_fails_closed(tmp_path):
    cfg = settings(tmp_path)
    cfg.ensure_dirs()
    (cfg.processed_dir / "plugin_registry_v1.json").write_text('[{"plugin_id": "example.bad"}]', encoding="utf-8")
    failed(lambda: list_plugins(cfg), "ERROR_PLUGIN_REGISTRY_INVALID")


def test_symlink_archive_not_accepted(tmp_path):
    cfg = settings(tmp_path)
    original = package()
    row = stage_plugin(original, cfg=cfg)["plugin"]
    target = cfg.data_dir / "plugins/archives" / f"{row['archive_sha256']}.zip"
    external = tmp_path / "external.zip"
    external.write_bytes(original)
    target.unlink()
    target.symlink_to(external)
    assert list_plugins(cfg)["plugins"][0]["integrity"] == "FAIL"
    failed(lambda: stage_plugin(original, cfg=cfg), "ERROR_PLUGIN_ARCHIVE_CORRUPT")


def test_invalid_contribution_type_is_clean_denial(tmp_path):
    cfg = settings(tmp_path)
    failed(lambda: stage_plugin(package(manifest(contributions=[{"kind": ["reference"], "path": "notes.md"}])), cfg=cfg),
           "ERROR_PLUGIN_CONTRIBUTION_INVALID")


def test_malformed_registry_json_is_clean_denial(tmp_path):
    cfg = settings(tmp_path)
    cfg.ensure_dirs()
    (cfg.processed_dir / "plugin_registry_v1.json").write_text("{broken", encoding="utf-8")
    failed(lambda: list_plugins(cfg), "ERROR_PLUGIN_REGISTRY_INVALID")
