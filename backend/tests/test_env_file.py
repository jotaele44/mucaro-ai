from backend.app.env_file import merge_env_file, read_env_file


def test_read_env_file_parses_comments_quotes_export_and_skips_blanks(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# Optional. Leave unset for deterministic mock mode.",
                "MUCARO_AI_LIVE_MODE=1",
                "MUCARO_AI_OPERATOR_TOKEN=",
                'MUCARO_AI_MODEL="claude-sonnet-5"',
                "export MUCARO_AI_WORKSPACE_PROFILE='research'",
                "MUCARO_AI_MAX_UPLOAD_BYTES=1024  # bytes",
                "not a pair",
                "1BAD=value",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert read_env_file(env_file) == {
        "MUCARO_AI_LIVE_MODE": "1",
        "MUCARO_AI_MODEL": "claude-sonnet-5",
        "MUCARO_AI_WORKSPACE_PROFILE": "research",
        "MUCARO_AI_MAX_UPLOAD_BYTES": "1024",
    }


def test_read_env_file_missing_file_is_empty(tmp_path):
    assert read_env_file(tmp_path / ".env") == {}


def test_merge_env_file_never_overrides_existing_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MUCARO_AI_LIVE_MODE=1\nMUCARO_AI_MODEL=from-file\n", encoding="utf-8")
    base = {"MUCARO_AI_MODEL": "from-shell", "PATH": "/usr/bin"}

    merged = merge_env_file(base, env_file)

    assert merged == {"MUCARO_AI_MODEL": "from-shell", "PATH": "/usr/bin", "MUCARO_AI_LIVE_MODE": "1"}
    assert base == {"MUCARO_AI_MODEL": "from-shell", "PATH": "/usr/bin"}
