from backend.app.config import DEFAULT_MAX_UPLOAD_BYTES, Settings, parse_positive_int


def test_parse_positive_int_uses_default_for_invalid_values():
    assert parse_positive_int("not-a-number", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("-1", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("1024", DEFAULT_MAX_UPLOAD_BYTES) == 1024


def test_settings_loads_explicit_distribution_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.setenv("MUCARO_AI_DISTRIBUTION_CHANNEL", "mac_app_store")

    cfg = Settings.load()

    assert cfg.distribution_channel == "mac_app_store"


def test_data_dir_override_keeps_user_data_outside_the_bundle(tmp_path, monkeypatch):
    from backend.app.config import Settings

    target = tmp_path / "Application Support" / "Mucaro AI"
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(target))
    cfg = Settings.load()
    assert cfg.data_dir == target.resolve()
    assert cfg.sqlite_path == target.resolve() / "processed" / "mucaro_ai.sqlite3"
    assert cfg.project_root not in cfg.data_dir.parents
    monkeypatch.delenv("MUCARO_AI_DATA_DIR")
    assert Settings.load().data_dir == Settings.load().project_root / "data"
