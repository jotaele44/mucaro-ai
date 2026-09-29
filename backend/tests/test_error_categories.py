from __future__ import annotations

import pytest

from backend.app.error_categories import ERROR_CATEGORIES, classify_error, classify_http_error


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ERROR_EMPTY_FILENAME", "INPUT_INVALID"),
        ("ERROR_OPERATOR_AUTH_REQUIRED", "PERMISSION_DENIED"),
        ("ERROR_TOOL_TIMEOUT", "TOOL_FAILURE"),
        ("ERROR_INPUT_PATH_NOT_FOUND", "SOURCE_UNAVAILABLE"),
        ("ERROR_FETCH_FAILED", "REMOTE_SERVICE_UNAVAILABLE"),
        ("ERROR_UNSUPPORTED_FILE_TYPE", "UNSUPPORTED_FORMAT"),
        ("ERROR_MODEL_PROVIDER_UNAVAILABLE", "MODEL_UNAVAILABLE"),
        ("ERROR_OFFLINE", "OFFLINE"),
        ("ERROR_TOOL_APPROVAL_REQUIRED", "BLOCKED_ACTION"),
        ("ERROR_UNTRUSTED_CONTENT", "UNTRUSTED_CONTENT"),
        ("ERROR_UNCLASSIFIED_FAILURE", "INTERNAL_FAILURE"),
    ],
)
def test_status_classifier_covers_actionable_error_families(status: str, expected: str) -> None:
    assert classify_error(status) == expected
    assert expected in ERROR_CATEGORIES


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, "INPUT_INVALID"),
        (401, "PERMISSION_DENIED"),
        (403, "PERMISSION_DENIED"),
        (404, "SOURCE_UNAVAILABLE"),
        (415, "UNSUPPORTED_FORMAT"),
        (422, "INPUT_INVALID"),
        (503, "REMOTE_SERVICE_UNAVAILABLE"),
        (500, "INTERNAL_FAILURE"),
    ],
)
def test_http_classifier_covers_framework_errors_without_named_status(status_code: int, expected: str) -> None:
    assert classify_http_error(None, status_code) == expected


def test_named_status_has_priority_over_generic_http_code() -> None:
    assert classify_http_error("ERROR_TOOL_TIMEOUT", 400) == "TOOL_FAILURE"
    assert classify_http_error("ERROR_UNSUPPORTED_FILE_TYPE", 400) == "UNSUPPORTED_FORMAT"
