from backend.app.config import DEFAULT_MAX_UPLOAD_BYTES, parse_positive_int


def test_parse_positive_int_uses_default_for_invalid_values():
    assert parse_positive_int("not-a-number", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("-1", DEFAULT_MAX_UPLOAD_BYTES) == DEFAULT_MAX_UPLOAD_BYTES
    assert parse_positive_int("1024", DEFAULT_MAX_UPLOAD_BYTES) == 1024
