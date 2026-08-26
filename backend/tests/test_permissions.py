from pathlib import Path

from backend.app.config import Settings
from backend.app.permissions import (
    DirectoryPermissionError,
    check_directory_permission,
    grant_directory,
    load_directory_grants,
    revoke_directory,
)


def make_settings(root: Path) -> Settings:
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
    )


def test_project_internal_path_does_not_require_grant(tmp_path: Path):
    project = tmp_path / "project"
    cfg = make_settings(project)
    sample = project / "data" / "input" / "sample.md"
    sample.parent.mkdir(parents=True)
    sample.write_text("Project-local input is already inside the app boundary.", encoding="utf-8")

    result = check_directory_permission(sample, cfg)

    assert result["status"] == "PERMISSION_OK"
    assert result["scope"] == "project"


def test_external_path_requires_then_accepts_directory_grant(tmp_path: Path):
    project = tmp_path / "project"
    cfg = make_settings(project)
    external = tmp_path / "external"
    external.mkdir()
    sample = external / "source.md"
    sample.write_text("External source requires a local permission grant.", encoding="utf-8")

    denied = check_directory_permission(sample, cfg)
    assert denied["status"] == "ERROR_DIRECTORY_PERMISSION_REQUIRED"
    assert denied["canonical_root"] == str(external.resolve())

    grant = grant_directory(external, cfg, granted_by="test", reason="regression")
    assert grant["status"] == "DIRECTORY_PERMISSION_GRANTED"
    assert grant["canonical_root"] == str(external.resolve())
    assert load_directory_grants(cfg) == [grant]

    allowed = check_directory_permission(sample, cfg)
    assert allowed["status"] == "PERMISSION_OK"
    assert allowed["scope"] == "granted_directory"

    revoked = revoke_directory(external, cfg)
    assert revoked["status"] == "DIRECTORY_PERMISSION_REVOKED"
    assert check_directory_permission(sample, cfg)["status"] == "ERROR_DIRECTORY_PERMISSION_REQUIRED"


def test_directory_grant_rejects_filesystem_root():
    try:
        grant_directory("/", make_settings(Path("/tmp/mucaro-test-root")), granted_by="test")
    except DirectoryPermissionError as exc:
        assert exc.status == "ERROR_DIRECTORY_PERMISSION_TOO_BROAD"
