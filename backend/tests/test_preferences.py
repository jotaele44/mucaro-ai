from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.preferences import (
    PreferenceError,
    apply_preference_prompt,
    get_preferences,
    parse_preference_prompt,
    run_options_from_preferences,
    update_preferences,
)
from backend.app.utils import write_json


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
        default_model_name="mock-cited-synthesizer-v1",
    )


def test_preferences_defaults_are_complete_and_hash_bound(tmp_path: Path):
    cfg = make_settings(tmp_path)
    result = get_preferences(cfg)
    assert result["status"] == "PREFERENCES_OK"
    assert result["revision"] == 0
    assert result["values"]["retrieval_strategy"] == "hybrid"
    assert result["values"]["top_k"] == 5
    assert result["values"]["theme"] == "auto"
    assert result["values_sha256"]
    assert set(result["definitions"]) == set(result["values"])


def test_update_is_atomic_versioned_and_persistent(tmp_path: Path):
    cfg = make_settings(tmp_path)
    result = update_preferences({"top_k": 8, "theme": "dark"}, expected_revision=0, source="test", cfg=cfg)
    assert result["status"] == "PREFERENCES_UPDATED"
    assert result["schema_version"] == "mucaro-user-preferences/1.0"
    assert result["revision"] == 1
    assert result["updated_at"]
    assert result["changed"] == {"top_k": 8, "theme": "dark"}
    loaded = get_preferences(cfg)
    assert loaded["revision"] == 1
    assert loaded["values"]["top_k"] == 8
    assert loaded["values"]["theme"] == "dark"
    assert loaded["values_sha256"] == result["values_sha256"]


def test_noop_update_does_not_increment_revision(tmp_path: Path):
    cfg = make_settings(tmp_path)
    result = update_preferences({"top_k": 5}, expected_revision=0, source="test", cfg=cfg)
    assert result["status"] == "PREFERENCES_UNCHANGED"
    assert result["revision"] == 0


def test_stale_revision_fails_closed(tmp_path: Path):
    cfg = make_settings(tmp_path)
    update_preferences({"top_k": 6}, expected_revision=0, source="test", cfg=cfg)
    with pytest.raises(PreferenceError) as exc:
        update_preferences({"top_k": 7}, expected_revision=0, source="test", cfg=cfg)
    assert exc.value.status == "ERROR_PREFERENCE_VERSION_CONFLICT"
    assert get_preferences(cfg)["values"]["top_k"] == 6


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"top_k": 0}, "ERROR_PREFERENCE_VALUE"),
        ({"top_k": 21}, "ERROR_PREFERENCE_VALUE"),
        ({"theme": "sepia"}, "ERROR_PREFERENCE_VALUE"),
        ({"retrieval_strategy": "nearest"}, "ERROR_PREFERENCE_VALUE"),
        ({"unknown": True}, "ERROR_PREFERENCE_UNKNOWN"),
        ({"allow_live": True, "require_offline": True}, "ERROR_PREFERENCE_CONTRADICTION"),
    ],
)
def test_invalid_updates_fail_closed(tmp_path: Path, changes, status):
    cfg = make_settings(tmp_path)
    with pytest.raises(PreferenceError) as exc:
        update_preferences(changes, source="test", cfg=cfg)
    assert exc.value.status == status
    assert get_preferences(cfg)["revision"] == 0


def test_explicit_prompt_assignments_support_multiple_settings():
    parsed = parse_preference_prompt('/settings theme=dark top_k=9 retrieval=hybrid')
    assert parsed["status"] == "PREFERENCE_PROMPT_MATCH"
    assert parsed["changes"] == {"theme": "dark", "top_k": 9, "retrieval_strategy": "hybrid"}


@pytest.mark.parametrize(
    ("prompt", "key", "value"),
    [
        ("Set theme to dark", "theme", "dark"),
        ("Use semantic retrieval", "retrieval_strategy", "semantic"),
        ("Use 12 retrieval results", "top_k", 12),
        ("Switch to local model", "model_provider", "openai_compatible_local"),
        ("Enable reduced motion", "reduced_motion", True),
        ("Disable mock fallback", "allow_mock_fallback", False),
    ],
)
def test_natural_setting_prompts_are_bounded(prompt, key, value):
    parsed = parse_preference_prompt(prompt)
    assert parsed["status"] == "PREFERENCE_PROMPT_MATCH"
    assert parsed["changes"][key] == value


def test_offline_and_live_special_phrases_are_coherent():
    offline = parse_preference_prompt("Work offline only")
    assert offline["changes"] == {"require_offline": True, "allow_live": False}
    live = parse_preference_prompt("Allow live models")
    assert live["changes"] == {"allow_live": True, "require_offline": False}


@pytest.mark.parametrize(
    "prompt",
    [
        "Set API key to sk-secret",
        "Change operator token to secret-value",
        "Save my password as hunter2",
    ],
)
def test_secret_settings_are_blocked_from_persisted_chat(prompt):
    parsed = parse_preference_prompt(prompt)
    assert parsed["status"] == "PREFERENCE_PROMPT_BLOCKED"
    assert "persisted" in parsed["detail"].lower()


def test_unrelated_question_is_not_misclassified_as_settings():
    parsed = parse_preference_prompt("What theme does this document use?")
    assert parsed == {"status": "PREFERENCE_PROMPT_NONE"}


def test_apply_prompt_persists_receipt_without_raw_prompt(tmp_path: Path):
    cfg = make_settings(tmp_path)
    result = apply_preference_prompt("Set retrieval strategy to bm25", source="conversation:test", cfg=cfg)
    assert result["status"] == "PREFERENCE_PROMPT_APPLIED"
    assert result["changed"] == {"retrieval_strategy": "bm25"}
    stored = (cfg.processed_dir / "user_preferences.json").read_text(encoding="utf-8")
    assert "Set retrieval strategy" not in stored
    assert "conversation:test" in stored


def test_corrupt_schema_is_not_silently_replaced(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    write_json(cfg.processed_dir / "user_preferences.json", {"schema_version": "mucaro-user-preferences/999"})
    with pytest.raises(PreferenceError) as exc:
        get_preferences(cfg)
    assert exc.value.status == "ERROR_PREFERENCES_SCHEMA"


def test_run_options_projection_excludes_ui_only_preferences(tmp_path: Path):
    cfg = make_settings(tmp_path)
    payload = update_preferences({"theme": "dark", "top_k": 11, "allow_live": False}, source="test", cfg=cfg)
    projected = run_options_from_preferences(payload)
    assert projected["top_k"] == 11
    assert projected["allow_live"] is False
    assert "theme" not in projected
    assert "reduced_motion" not in projected

def test_explicit_secret_assignment_is_blocked_before_value_parsing():
    parsed = parse_preference_prompt("/settings operator_token=do-not-store")
    assert parsed["status"] == "PREFERENCE_PROMPT_BLOCKED"
    assert "do-not-store" not in parsed["detail"]


def test_unknown_persisted_key_fails_closed(tmp_path: Path):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    write_json(
        cfg.processed_dir / "user_preferences.json",
        {
            "schema_version": "mucaro-user-preferences/1.0",
            "revision": 1,
            "values": {"top_k": 5, "future_unreviewed_setting": True},
        },
    )
    with pytest.raises(PreferenceError) as exc:
        get_preferences(cfg)
    assert exc.value.status == "ERROR_PREFERENCES_SCHEMA"


@pytest.mark.parametrize("revision", [-1, "not-an-int", True])
def test_invalid_persisted_revision_fails_closed(tmp_path: Path, revision):
    cfg = make_settings(tmp_path)
    cfg.ensure_dirs()
    write_json(
        cfg.processed_dir / "user_preferences.json",
        {
            "schema_version": "mucaro-user-preferences/1.0",
            "revision": revision,
            "values": {"top_k": 5},
        },
    )
    with pytest.raises(PreferenceError) as exc:
        get_preferences(cfg)
    assert exc.value.status == "ERROR_PREFERENCES_SCHEMA"


def test_provider_change_updates_its_default_model(tmp_path: Path):
    cfg = make_settings(tmp_path)
    result = update_preferences({"model_provider": "openai_compatible_local"}, source="test", cfg=cfg)
    assert result["values"]["model_provider"] == "openai_compatible_local"
    assert result["values"]["model_name"] == "llama3.1:8b"


def test_positive_live_and_offline_prompts_clear_the_opposing_mode():
    live = parse_preference_prompt("Enable live models")
    assert live["changes"] == {"allow_live": True, "require_offline": False}
    offline = parse_preference_prompt("Enable offline mode")
    assert offline["changes"] == {"require_offline": True, "allow_live": False}
