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
