from __future__ import annotations

import re
import shlex
from typing import Any

from .config import Settings, settings
from .model_router import model_provider_names
from .utils import canonical_sha256, read_json, update_json, utc_now

PREFERENCES_SCHEMA_VERSION = "mucaro-user-preferences/1.0"
PREFERENCES_FILENAME = "user_preferences.json"
RETRIEVAL_STRATEGIES = ("hybrid", "bm25", "persistent", "semantic", "lexical", "vector")
THEMES = ("auto", "light", "dark")
RESTRICTED_PROMPT_SETTINGS = (
    "operator_token",
    "api_key",
    "anthropic_api_key",
    "password",
    "secret",
)

PREFERENCE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "model_provider": {
        "type": "enum",
        "label": "Answer model provider",
        "prompt_configurable": True,
    },
    "model_name": {
        "type": "string",
        "label": "Answer model name",
        "prompt_configurable": True,
        "max_length": 120,
    },
    "retrieval_strategy": {
        "type": "enum",
        "label": "Retrieval strategy",
        "values": list(RETRIEVAL_STRATEGIES),
        "prompt_configurable": True,
    },
    "top_k": {
        "type": "integer",
        "label": "Retrieved chunks",
        "minimum": 1,
        "maximum": 20,
        "prompt_configurable": True,
    },
    "allow_mock_fallback": {
        "type": "boolean",
        "label": "Allow mock fallback",
        "prompt_configurable": True,
    },
    "allow_live": {
        "type": "boolean",
        "label": "Allow remote model calls",
        "prompt_configurable": True,
    },
    "require_offline": {
        "type": "boolean",
        "label": "Require offline operation",
        "prompt_configurable": True,
    },
    "theme": {
        "type": "enum",
        "label": "Appearance theme",
        "values": list(THEMES),
        "prompt_configurable": True,
    },
    "reduced_motion": {
        "type": "boolean",
        "label": "Reduced motion",
        "prompt_configurable": True,
    },
}

_KEY_ALIASES = {
    "model": "model_provider",
    "model provider": "model_provider",
    "provider": "model_provider",
    "model name": "model_name",
    "retrieval": "retrieval_strategy",
    "retrieval strategy": "retrieval_strategy",
    "strategy": "retrieval_strategy",
    "top k": "top_k",
    "top_k": "top_k",
    "results": "top_k",
    "retrieved chunks": "top_k",
    "mock fallback": "allow_mock_fallback",
    "allow mock fallback": "allow_mock_fallback",
    "live": "allow_live",
    "live models": "allow_live",
    "remote models": "allow_live",
    "allow live": "allow_live",
    "offline": "require_offline",
    "offline only": "require_offline",
    "offline mode": "require_offline",
    "theme": "theme",
    "reduced motion": "reduced_motion",
    "motion": "reduced_motion",
}
_PROVIDER_ALIASES = {
    "local": "openai_compatible_local",
    "local model": "openai_compatible_local",
    "openai compatible local": "openai_compatible_local",
    "openai-compatible local": "openai_compatible_local",
    "mock": "mock",
    "anthropic": "anthropic",
}
_BOOL_TRUE = {"1", "true", "yes", "on", "enable", "enabled", "allow", "allowed"}
_BOOL_FALSE = {"0", "false", "no", "off", "disable", "disabled", "deny", "denied"}


class PreferenceError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _path(cfg: Settings) -> Any:
    return cfg.processed_dir / PREFERENCES_FILENAME


def _defaults(cfg: Settings) -> dict[str, Any]:
    provider = (cfg.default_model_provider or "mock").strip().lower() or "mock"
    if provider not in model_provider_names():
        provider = "mock"
    return {
        "model_provider": provider,
        "model_name": (cfg.default_model_name or "").strip()[:120],
        "retrieval_strategy": "hybrid",
        "top_k": 5,
        "allow_mock_fallback": False,
        "allow_live": False,
        "require_offline": False,
        "theme": "auto",
        "reduced_motion": False,
    }


def preference_definitions() -> dict[str, dict[str, Any]]:
    definitions = {key: dict(value) for key, value in PREFERENCE_DEFINITIONS.items()}
    definitions["model_provider"]["values"] = list(model_provider_names())
    return definitions


def _validate_value(key: str, value: Any) -> Any:
    if key not in PREFERENCE_DEFINITIONS:
        raise PreferenceError("ERROR_PREFERENCE_UNKNOWN", f"Unknown preference: {key}")
    if key == "model_provider":
        normalized = str(value).strip().lower()
        normalized = _PROVIDER_ALIASES.get(normalized, normalized)
        if normalized not in model_provider_names():
            raise PreferenceError("ERROR_PREFERENCE_VALUE", f"Unsupported model provider: {value}")
        return normalized
    if key == "model_name":
        if not isinstance(value, str):
            raise PreferenceError("ERROR_PREFERENCE_VALUE", "model_name must be text")
        normalized = value.strip()
        if not normalized or len(normalized) > 120 or any(ord(char) < 32 for char in normalized):
            raise PreferenceError("ERROR_PREFERENCE_VALUE", "model_name must be 1-120 printable characters")
        return normalized
    if key == "retrieval_strategy":
        normalized = str(value).strip().lower()
        if normalized not in RETRIEVAL_STRATEGIES:
            raise PreferenceError("ERROR_PREFERENCE_VALUE", f"Unsupported retrieval strategy: {value}")
        return normalized
    if key == "top_k":
        if isinstance(value, bool):
            raise PreferenceError("ERROR_PREFERENCE_VALUE", "top_k must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise PreferenceError("ERROR_PREFERENCE_VALUE", "top_k must be an integer") from exc
        if parsed < 1 or parsed > 20:
            raise PreferenceError("ERROR_PREFERENCE_VALUE", "top_k must be between 1 and 20")
        return parsed
    if key in {"allow_mock_fallback", "allow_live", "require_offline", "reduced_motion"}:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in _BOOL_TRUE:
            return True
        if normalized in _BOOL_FALSE:
            return False
        raise PreferenceError("ERROR_PREFERENCE_VALUE", f"{key} must be true or false")
    if key == "theme":
        normalized = str(value).strip().lower()
        if normalized == "system":
            normalized = "auto"
        if normalized not in THEMES:
            raise PreferenceError("ERROR_PREFERENCE_VALUE", f"theme must be one of {THEMES}")
        return normalized
    raise PreferenceError("ERROR_PREFERENCE_UNKNOWN", key)


def _validate_values(values: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    defaults = _defaults(cfg)
    result = dict(defaults)
    for key, value in values.items():
        if key not in PREFERENCE_DEFINITIONS:
            continue
        result[key] = _validate_value(key, value)
    if result["allow_live"] and result["require_offline"]:
        raise PreferenceError(
            "ERROR_PREFERENCE_CONTRADICTION",
            "allow_live and require_offline cannot both be enabled",
        )
    return result


def _initial(cfg: Settings) -> dict[str, Any]:
    now = utc_now()
    values = _defaults(cfg)
    return {
        "schema_version": PREFERENCES_SCHEMA_VERSION,
        "revision": 0,
        "created_at": now,
        "updated_at": now,
        "values": values,
        "values_sha256": canonical_sha256(values),
        "last_change": None,
    }


def get_preferences(cfg: Settings = settings) -> dict[str, Any]:
    cfg.ensure_dirs()
    raw = read_json(_path(cfg), None)
    if raw is None:
        state = _initial(cfg)
    elif not isinstance(raw, dict) or raw.get("schema_version") != PREFERENCES_SCHEMA_VERSION:
        raise PreferenceError("ERROR_PREFERENCES_SCHEMA", "Stored preferences use an unsupported schema")
    else:
        values = _validate_values(dict(raw.get("values") or {}), cfg)
        state = {
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            "revision": max(0, int(raw.get("revision") or 0)),
            "created_at": raw.get("created_at") or utc_now(),
            "updated_at": raw.get("updated_at") or raw.get("created_at") or utc_now(),
            "values": values,
            "values_sha256": canonical_sha256(values),
            "last_change": raw.get("last_change"),
        }
    return {
        "status": "PREFERENCES_OK",
        **state,
        "definitions": preference_definitions(),
        "restricted_prompt_settings": list(RESTRICTED_PROMPT_SETTINGS),
    }


def update_preferences(
    changes: dict[str, Any],
    *,
    expected_revision: int | None = None,
    source: str = "api",
    cfg: Settings = settings,
) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise PreferenceError("ERROR_PREFERENCE_CHANGES_REQUIRED", "At least one preference change is required")
    if any(key not in PREFERENCE_DEFINITIONS for key in changes):
        unknown = sorted(key for key in changes if key not in PREFERENCE_DEFINITIONS)
        raise PreferenceError("ERROR_PREFERENCE_UNKNOWN", f"Unknown preference(s): {', '.join(unknown)}")
    normalized_changes = {key: _validate_value(key, value) for key, value in changes.items()}
    cfg.ensure_dirs()

    def mutate(state: Any) -> dict[str, Any]:
        current = _initial(cfg) if not isinstance(state, dict) or not state else state
        if current.get("schema_version") != PREFERENCES_SCHEMA_VERSION:
            raise PreferenceError("ERROR_PREFERENCES_SCHEMA", "Stored preferences use an unsupported schema")
        revision = max(0, int(current.get("revision") or 0))
        if expected_revision is not None and revision != expected_revision:
            raise PreferenceError(
                "ERROR_PREFERENCE_VERSION_CONFLICT",
                f"Preferences changed since revision {expected_revision}; current revision is {revision}",
            )
        before = _validate_values(dict(current.get("values") or {}), cfg)
        after = dict(before)
        after.update(normalized_changes)
        after = _validate_values(after, cfg)
        changed = {key: after[key] for key in normalized_changes if before.get(key) != after.get(key)}
        if not changed:
            current.update({
                "schema_version": PREFERENCES_SCHEMA_VERSION,
                "revision": revision,
                "values": before,
                "values_sha256": canonical_sha256(before),
            })
            return {
                "status": "PREFERENCES_UNCHANGED",
                "revision": revision,
                "changed": {},
                "values": before,
                "values_sha256": canonical_sha256(before),
            }
        now = utc_now()
        receipt = {
            "source": source[:160],
            "changed": changed,
            "previous_values_sha256": canonical_sha256(before),
            "new_values_sha256": canonical_sha256(after),
            "updated_at": now,
        }
        current.update({
            "schema_version": PREFERENCES_SCHEMA_VERSION,
            "revision": revision + 1,
            "created_at": current.get("created_at") or now,
            "updated_at": now,
            "values": after,
            "values_sha256": receipt["new_values_sha256"],
            "last_change": receipt,
        })
        return {
            "status": "PREFERENCES_UPDATED",
            "revision": revision + 1,
            "changed": changed,
            "values": after,
            "values_sha256": receipt["new_values_sha256"],
            "receipt": receipt,
        }

    result = update_json(_path(cfg), _initial(cfg), mutate)
    return {
        **result,
        "definitions": preference_definitions(),
        "restricted_prompt_settings": list(RESTRICTED_PROMPT_SETTINGS),
    }


def run_options_from_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(payload.get("values") or {})
    return {
        key: values.get(key)
        for key in (
            "model_provider",
            "model_name",
            "retrieval_strategy",
            "top_k",
            "allow_mock_fallback",
            "allow_live",
            "require_offline",
        )
    }


def _canonical_key(raw: str) -> str | None:
    normalized = " ".join(raw.strip().lower().replace("-", " ").replace("_", " ").split())
    return _KEY_ALIASES.get(normalized) or (normalized.replace(" ", "_") if normalized.replace(" ", "_") in PREFERENCE_DEFINITIONS else None)


def _assignment_value(key: str, raw: str) -> Any:
    value = raw.strip().strip(".,")
    if key == "model_provider":
        value = _PROVIDER_ALIASES.get(value.lower(), value)
    return _validate_value(key, value)


def _blocked_secret_prompt(text: str) -> bool:
    lowered = text.lower()
    if not re.search(r"\b(set|change|save|store|configure|use)\b", lowered):
        return False
    return bool(re.search(r"\b(api[ _-]?key|access[ _-]?token|operator[ _-]?token|password|secret)\b", lowered))


def parse_preference_prompt(prompt: str) -> dict[str, Any]:
    text = " ".join((prompt or "").strip().split())
    if not text:
        return {"status": "PREFERENCE_PROMPT_NONE"}
    if _blocked_secret_prompt(text):
        return {
            "status": "PREFERENCE_PROMPT_BLOCKED",
            "detail": "Credentials cannot be changed through chat because conversation prompts are persisted locally.",
        }

    if text.lower().startswith("/settings"):
        try:
            tokens = shlex.split(text[len("/settings"):].strip())
        except ValueError as exc:
            return {"status": "PREFERENCE_PROMPT_INVALID", "detail": str(exc)}
        if not tokens:
            return {"status": "PREFERENCE_PROMPT_INVALID", "detail": "Use /settings key=value [key=value ...]"}
        changes: dict[str, Any] = {}
        for token in tokens:
            if "=" not in token:
                return {"status": "PREFERENCE_PROMPT_INVALID", "detail": f"Expected key=value, got: {token}"}
            raw_key, raw_value = token.split("=", 1)
            key = _canonical_key(raw_key)
            if key is None:
                return {"status": "PREFERENCE_PROMPT_INVALID", "detail": f"Unknown setting: {raw_key}"}
            try:
                changes[key] = _assignment_value(key, raw_value)
            except PreferenceError as exc:
                return {"status": "PREFERENCE_PROMPT_INVALID", "detail": exc.detail, "error_status": exc.status}
        return {"status": "PREFERENCE_PROMPT_MATCH", "changes": changes, "parser": "explicit_assignments_v1"}

    lowered = text.lower().rstrip(".!?")

    boolean_patterns = [
        (r"^(?:turn|switch) (on|off) (?:the )?(live models|remote models|live)$", "allow_live"),
        (r"^(?:enable|disable) (?:the )?(live models|remote models|live)$", "allow_live"),
        (r"^(?:turn|switch) (on|off) (?:the )?(offline mode|offline only|offline)$", "require_offline"),
        (r"^(?:enable|disable) (?:the )?(offline mode|offline only|offline)$", "require_offline"),
        (r"^(?:turn|switch) (on|off) (?:the )?(mock fallback)$", "allow_mock_fallback"),
        (r"^(?:enable|disable) (?:the )?(mock fallback)$", "allow_mock_fallback"),
        (r"^(?:turn|switch) (on|off) (?:the )?(reduced motion)$", "reduced_motion"),
        (r"^(?:enable|disable) (?:the )?(reduced motion)$", "reduced_motion"),
    ]
    for pattern, key in boolean_patterns:
        match = re.fullmatch(pattern, lowered)
        if match:
            verb = match.group(1)
            value = verb in {"on", "enable"}
            return {"status": "PREFERENCE_PROMPT_MATCH", "changes": {key: value}, "parser": "boolean_phrase_v1"}

    special = [
        (r"^(?:work|stay|run) offline only$", {"require_offline": True, "allow_live": False}),
        (r"^(?:allow|use) (?:live|remote) models$", {"allow_live": True, "require_offline": False}),
        (r"^use (light|dark|system|auto) theme$", None),
        (r"^use (hybrid|bm25|persistent|semantic|lexical|vector) retrieval$", None),
        (r"^use ([1-9]|1[0-9]|20) (?:retrieval )?(?:results|chunks)$", None),
    ]
    for pattern, fixed in special:
        match = re.fullmatch(pattern, lowered)
        if not match:
            continue
        if fixed is not None:
            return {"status": "PREFERENCE_PROMPT_MATCH", "changes": fixed, "parser": "special_phrase_v1"}
        raw = match.group(1)
        if raw in THEMES or raw == "system":
            return {"status": "PREFERENCE_PROMPT_MATCH", "changes": {"theme": _validate_value("theme", raw)}, "parser": "theme_phrase_v1"}
        if raw in RETRIEVAL_STRATEGIES:
            return {"status": "PREFERENCE_PROMPT_MATCH", "changes": {"retrieval_strategy": raw}, "parser": "retrieval_phrase_v1"}
        return {"status": "PREFERENCE_PROMPT_MATCH", "changes": {"top_k": int(raw)}, "parser": "top_k_phrase_v1"}

    assignment = re.fullmatch(
        r"^(?:set|change|switch) (?:the )?(.{1,40}?) (?:to|=) (.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if assignment:
        key = _canonical_key(assignment.group(1))
        if key is None:
            return {"status": "PREFERENCE_PROMPT_NONE"}
        try:
            value = _assignment_value(key, assignment.group(2))
        except PreferenceError as exc:
            return {"status": "PREFERENCE_PROMPT_INVALID", "detail": exc.detail, "error_status": exc.status}
        return {"status": "PREFERENCE_PROMPT_MATCH", "changes": {key: value}, "parser": "natural_assignment_v1"}

    provider = re.fullmatch(r"^(?:use|switch to) (mock|anthropic|local model|openai[- ]compatible local)(?: model)?$", lowered)
    if provider:
        return {
            "status": "PREFERENCE_PROMPT_MATCH",
            "changes": {"model_provider": _assignment_value("model_provider", provider.group(1))},
            "parser": "provider_phrase_v1",
        }
    return {"status": "PREFERENCE_PROMPT_NONE"}


def apply_preference_prompt(
    prompt: str,
    *,
    source: str,
    cfg: Settings = settings,
) -> dict[str, Any]:
    parsed = parse_preference_prompt(prompt)
    if parsed["status"] != "PREFERENCE_PROMPT_MATCH":
        return parsed
    result = update_preferences(parsed["changes"], source=source, cfg=cfg)
    changed = result.get("changed") or {}
    if changed:
        summary = ", ".join(f"{key}={value}" for key, value in changed.items())
        assistant_text = f"Settings updated: {summary}."
    else:
        assistant_text = "Those settings were already in effect."
    return {
        **result,
        "status": "PREFERENCE_PROMPT_APPLIED",
        "parser": parsed.get("parser"),
        "requested_changes": parsed["changes"],
        "assistant_text": assistant_text,
    }
