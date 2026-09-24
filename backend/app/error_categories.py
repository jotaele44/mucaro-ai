"""Actionable error categories (TGT-034).

Status codes stay the precise machine-readable identity; the category tells
the user what kind of action could help. Unknown statuses fall back to
INTERNAL_FAILURE rather than being reported as success.
"""

from __future__ import annotations

ERROR_CATEGORIES = (
    "INPUT_INVALID",
    "PERMISSION_DENIED",
    "TOOL_FAILURE",
    "SOURCE_UNAVAILABLE",
    "REMOTE_SERVICE_UNAVAILABLE",
    "UNSUPPORTED_FORMAT",
    "MODEL_UNAVAILABLE",
    "OFFLINE",
    "BLOCKED_ACTION",
    "INTERNAL_FAILURE",
)

# Ordered (substring, category) rules; the first match wins.
_RULES: tuple[tuple[str, str], ...] = (
    ("OFFLINE", "OFFLINE"),
    ("DNS_FAILED", "OFFLINE"),
    ("PERMISSION", "PERMISSION_DENIED"),
    ("AUTH_REQUIRED", "PERMISSION_DENIED"),
    ("PRIVATE_HOST_BLOCKED", "BLOCKED_ACTION"),
    ("ROBOTS_DISALLOWED", "BLOCKED_ACTION"),
    ("DENIED", "BLOCKED_ACTION"),
    ("NOT_ALLOWED", "BLOCKED_ACTION"),
    ("APPROVAL_REQUIRED", "BLOCKED_ACTION"),
    ("DECISION_STALE", "BLOCKED_ACTION"),
    ("UNSUPPORTED_FILE_TYPE", "UNSUPPORTED_FORMAT"),
    ("SIGNATURE", "UNSUPPORTED_FORMAT"),
    ("EXECUTABLE_PAYLOAD", "UNSUPPORTED_FORMAT"),
    ("ARCHIVE", "UNSUPPORTED_FORMAT"),
    ("PARSE_FAILED", "UNSUPPORTED_FORMAT"),
    ("MODEL_PROVIDER", "MODEL_UNAVAILABLE"),
    ("MODEL_ROUTING", "MODEL_UNAVAILABLE"),
    ("PROVIDER_UNAVAILABLE", "MODEL_UNAVAILABLE"),
    ("ANTHROPIC", "MODEL_UNAVAILABLE"),
    ("LOCAL_MODEL", "MODEL_UNAVAILABLE"),
    ("SEARCH_KEY", "REMOTE_SERVICE_UNAVAILABLE"),
    ("FETCH_FAILED", "REMOTE_SERVICE_UNAVAILABLE"),
    ("HTTP_STATUS", "REMOTE_SERVICE_UNAVAILABLE"),
    ("TIMEOUT", "REMOTE_SERVICE_UNAVAILABLE"),
    ("NOT_FOUND", "SOURCE_UNAVAILABLE"),
    ("NO_SOURCES", "SOURCE_UNAVAILABLE"),
    ("INDEX", "SOURCE_UNAVAILABLE"),
    ("EMPTY", "INPUT_INVALID"),
    ("INVALID", "INPUT_INVALID"),
    ("REQUIRED", "INPUT_INVALID"),
    ("TOO_LARGE", "INPUT_INVALID"),
    ("TOO_LONG", "INPUT_INVALID"),
    ("UNKNOWN_COMMAND", "INPUT_INVALID"),
    ("TOOL", "TOOL_FAILURE"),
    ("WEB", "TOOL_FAILURE"),
    ("CODING", "TOOL_FAILURE"),
    ("GIS", "TOOL_FAILURE"),
    ("OCR", "TOOL_FAILURE"),
)


def classify_error(status: str | None) -> str:
    code = (status or "").upper()
    if not code:
        return "INTERNAL_FAILURE"
    for needle, category in _RULES:
        if needle in code:
            return category
    return "INTERNAL_FAILURE"
