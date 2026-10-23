import pytest

from backend.app.config import DEFAULT_MAX_UPLOAD_BYTES, Settings, parse_positive_int


def test_parse_positive_int_uses_default_for_invalid_values():
    assert parse_positive_int("not-a-number", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("-1", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("1024", DEFAULT_MAX_UPLOAD_BYTES) == 1024


def test_settings_prefers_repository_shaped_current_working_directory(monkeypatch, tmp_path):
    root = tmp_path / "isolated-worktree"
    (root / "schemas").mkdir(parents=True)
    (root / "backend").mkdir()
    monkeypatch.delenv("MUCARO_AI_ROOT", raising=False)
    monkeypatch.chdir(root)

    cfg = Settings.load()

    assert cfg.project_root == root.resolve()


def test_settings_loads_explicit_distribution_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(tmp_path.parent / "application-support"))
    monkeypatch.setenv("MUCARO_AI_DISTRIBUTION_CHANNEL", "mac_app_store")

    cfg = Settings.load()

    assert cfg.distribution_channel == "mac_app_store"
    assert cfg.data_dir == (tmp_path.parent / "application-support").resolve()


def test_data_dir_override_keeps_user_data_outside_the_bundle(tmp_path, monkeypatch):
    target = tmp_path / "Application Support" / "Mucaro AI"
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(target))
    cfg = Settings.load()
    assert cfg.data_dir == target.resolve()
    assert cfg.sqlite_path == target.resolve() / "processed" / "mucaro_ai.sqlite3"
    assert cfg.project_root not in cfg.data_dir.parents
    monkeypatch.delenv("MUCARO_AI_DATA_DIR")
    assert Settings.load().data_dir == Settings.load().project_root / "data"


def test_settings_rejects_relative_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", "relative/data")

    with pytest.raises(ValueError, match="must be an absolute path"):
        Settings.load()


def test_app_store_settings_reject_data_inside_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.setenv("MUCARO_AI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MUCARO_AI_DISTRIBUTION_CHANNEL", "mac_app_store")

    with pytest.raises(ValueError, match="outside the application bundle"):
        Settings.load()


def test_settings_default_model_matches_anthropic_provider_default(monkeypatch, tmp_path):
    from backend.app.model_router import MODEL_PROVIDER_SPECS

    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.delenv("MUCARO_AI_MODEL", raising=False)

    cfg = Settings.load()

    assert cfg.default_model_name == MODEL_PROVIDER_SPECS["anthropic"].default_model
    assert cfg.default_model_name != "claude-3-5-sonnet-latest"


def test_preflight_warns_when_env_pins_retired_model(tmp_path, capsys):
    import scripts.preflight as preflight

    env_file = tmp_path / ".env"
    env_file.write_text("MUCARO_AI_LIVE_MODE=0\nMUCARO_AI_MODEL=claude-3-5-sonnet-latest\n", encoding="utf-8")
    preflight.warn_retired_env_model(env_file)
    assert "WARN ENV_MODEL_RETIRED claude-3-5-sonnet-latest" in capsys.readouterr().out

    env_file.write_text(f"MUCARO_AI_MODEL={preflight.CURRENT_DEFAULT_MODEL}\n", encoding="utf-8")
    preflight.warn_retired_env_model(env_file)
    assert capsys.readouterr().out == ""


def test_preflight_current_default_model_matches_settings(monkeypatch, tmp_path):
    import scripts.preflight as preflight

    monkeypatch.setenv("MUCARO_AI_ROOT", str(tmp_path))
    monkeypatch.delenv("MUCARO_AI_MODEL", raising=False)

    assert preflight.CURRENT_DEFAULT_MODEL == Settings.load().default_model_name
