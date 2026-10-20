"""Claude connection settings for the chat agent (Settings -> Claude).

The API key is stored in the macOS login Keychain (service ``ai.mucaro.app``),
scoped to this data directory by a random id kept in the settings file, so a
test or second data directory never sees another one's key. Where the Keychain
is unavailable it falls back to a 0600 file in the data directory. The key is
never written to logs, run events or API responses; only its last four
characters are shown. ``ANTHROPIC_API_KEY`` in the environment is used only
when no key was saved here (CLI and headless use).

Sending anything to Anthropic needs explicit consent: the Settings toggle, or
``MUCARO_AI_LIVE_MODE=1`` for headless use.
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import sys
from typing import Any

from .config import Settings, settings
from .utils import read_json, update_json, write_json

SETTINGS_NAME = "app_settings.json"
SECRETS_NAME = "app_secrets.json"
KEYCHAIN_SERVICE = "ai.mucaro.app"
SECRET_STORES = ("keychain", "file", "env")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

# List prices in USD per million tokens (cache_write is the 5-minute TTL rate); used only for
# the approximate per-turn cost shown in the app.
AGENT_MODELS: dict[str, dict[str, Any]] = {
    "claude-opus-5-5": {
        "label": "Claude Opus 5.5",
        "summary": "Most capable: hard reasoning, research and coding.",
        "pricing": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20},
        "efforts": EFFORTS,
        "default_effort": "medium",
        "adaptive_thinking": True,
        "progress_updates": True,
        "fallbacks": True,
        "web_tool_version": "20260209",
        "code_execution": True,
    },
    "claude-sonnet-5-5": {
        "label": "Claude Sonnet 5.5",
        "summary": "Fast and capable at half the price of Opus.",
        "pricing": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.20},
        "efforts": EFFORTS,
        "default_effort": "low",
        "adaptive_thinking": True,
        "progress_updates": True,
        "fallbacks": True,
        "web_tool_version": "20260209",
        "code_execution": True,
    },
    "claude-haiku-4-5": {
        "label": "Claude Haiku 4.5",
        "summary": "Fastest and cheapest, for simple questions.",
        "pricing": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10},
        "efforts": (),
        "default_effort": None,
        "adaptive_thinking": False,
        "progress_updates": False,
        "fallbacks": False,
        "web_tool_version": "basic",
        "code_execution": False,
    },
    "claude-fable-5-1": {
        "label": "Claude Fable 5.1",
        "summary": "Anthropic's most capable model; slower and the most expensive.",
        "pricing": {"input": 10.0, "output": 50.0, "cache_write": 12.5, "cache_read": 0.25},
        "efforts": EFFORTS,
        "default_effort": "medium",
        "adaptive_thinking": True,
        "progress_updates": True,
        "fallbacks": True,
        "web_tool_version": "20260209",
        "code_execution": True,
    },
}
DEFAULT_MODEL = "claude-opus-5-5"
WEB_SEARCH_USD = 0.01  # list price per web search request
DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "effort": "medium",
    "web_tools": True,
    "code_sandbox": True,
    "live_consent": False,
}


class AppSettingsError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _settings_path(cfg: Settings) -> Any:
    return cfg.processed_dir / SETTINGS_NAME


def _secrets_path(cfg: Settings) -> Any:
    return cfg.processed_dir / SECRETS_NAME


def load_app_settings(cfg: Settings = settings) -> dict[str, Any]:
    stored = read_json(_settings_path(cfg), {})
    stored = stored if isinstance(stored, dict) else {}
    values = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    if values["model"] not in AGENT_MODELS:
        values["model"] = DEFAULT_MODEL
    if values["effort"] not in EFFORTS:
        values["effort"] = DEFAULTS["effort"]
    for key in ("web_tools", "code_sandbox", "live_consent"):
        values[key] = values[key] is True
    return values


def save_app_settings(changes: dict[str, Any], cfg: Settings = settings) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if value is None:
            continue
        if key == "model":
            if value not in AGENT_MODELS:
                raise AppSettingsError("ERROR_CLAUDE_MODEL_UNSUPPORTED", f"Model must be one of: {', '.join(AGENT_MODELS)}.")
        elif key == "effort":
            if value not in EFFORTS:
                raise AppSettingsError("ERROR_CLAUDE_EFFORT_INVALID", f"Effort must be one of: {', '.join(EFFORTS)}.")
        elif key in {"web_tools", "code_sandbox", "live_consent"}:
            if not isinstance(value, bool):
                raise AppSettingsError("ERROR_CLAUDE_SETTING_INVALID", f"{key} must be true or false.")
        else:
            raise AppSettingsError("ERROR_CLAUDE_SETTING_UNKNOWN", f"Unknown setting: {key}")
        clean[key] = value
    cfg.ensure_dirs()

    def mutate(stored: dict[str, Any]) -> None:
        stored.update(clean)

    update_json(_settings_path(cfg), {}, mutate)
    return load_app_settings(cfg)


def _secret_id(cfg: Settings) -> str:
    """Random id that scopes this data directory's Keychain item; created on first use."""

    def mutate(stored: dict[str, Any]) -> str:
        if not isinstance(stored.get("secret_id"), str) or not stored["secret_id"]:
            stored["secret_id"] = secrets.token_hex(8)
        return stored["secret_id"]

    cfg.ensure_dirs()
    return update_json(_settings_path(cfg), {}, mutate)


def secret_store_kind() -> str:
    configured = os.getenv("MUCARO_AI_SECRET_STORE", "").strip().lower()
    if configured in SECRET_STORES:
        return configured
    return "keychain" if sys.platform == "darwin" else "file"


class _Keychain:
    """Generic-password items in the login Keychain through the Security framework (pyobjc)."""

    def __init__(self, account: str) -> None:
        import Security  # type: ignore[import-not-found]

        self.security = Security
        self.query = {
            Security.kSecClass: Security.kSecClassGenericPassword,
            Security.kSecAttrService: KEYCHAIN_SERVICE,
            Security.kSecAttrAccount: account,
        }

    def get(self) -> str | None:
        security = self.security
        status, data = security.SecItemCopyMatching(
            {**self.query, security.kSecReturnData: True, security.kSecMatchLimit: security.kSecMatchLimitOne}, None
        )
        if status == security.errSecItemNotFound:
            return None
        if status != 0 or data is None:
            raise OSError(f"Keychain read failed: {status}")
        return bytes(data).decode("utf-8")

    def set(self, value: str) -> None:
        from Foundation import NSData  # type: ignore[import-not-found]

        security = self.security
        encoded = value.encode("utf-8")
        data = NSData.dataWithBytes_length_(encoded, len(encoded))
        status = security.SecItemUpdate(self.query, {security.kSecValueData: data})
        if status == security.errSecItemNotFound:
            result = security.SecItemAdd({**self.query, security.kSecValueData: data}, None)
            status = result[0] if isinstance(result, tuple) else result
        if status != 0:
            raise OSError(f"Keychain write failed: {status}")

    def delete(self) -> None:
        status = self.security.SecItemDelete(self.query)
        if status not in (0, self.security.errSecItemNotFound):
            raise OSError(f"Keychain delete failed: {status}")


def _keychain(cfg: Settings) -> _Keychain | None:
    try:
        return _Keychain(f"anthropic-api-key:{_secret_id(cfg)}")
    except Exception:  # noqa: BLE001 - no Security framework: use the file store
        return None


def _file_key(cfg: Settings) -> str | None:
    stored = read_json(_secrets_path(cfg), {})
    value = stored.get("anthropic_api_key") if isinstance(stored, dict) else None
    return value if isinstance(value, str) and value else None


def _write_file_key(cfg: Settings, key: str | None) -> None:
    path = _secrets_path(cfg)
    if key is None:
        if path.exists():
            path.unlink()
        return
    write_json(path, {"anthropic_api_key": key})
    path.chmod(0o600)


def _stored_key(cfg: Settings) -> tuple[str | None, str | None]:
    kind = secret_store_kind()
    if kind == "env":
        return None, None
    if kind == "keychain":
        keychain = _keychain(cfg)
        if keychain is not None:
            try:
                value = keychain.get()
            except OSError:
                value = None
            if value:
                return value, "keychain"
    value = _file_key(cfg)
    return (value, "file") if value else (None, None)


def get_api_key(cfg: Settings = settings) -> tuple[str | None, str | None]:
    """The key to use and where it came from: keychain, file or env."""
    key, source = _stored_key(cfg)
    if key:
        return key, source
    env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return (env_key, "env") if env_key else (None, None)


def validate_api_key(key: str) -> str:
    value = (key or "").strip()
    if not value:
        raise AppSettingsError("ERROR_CLAUDE_KEY_REQUIRED", "Paste your Anthropic API key.")
    if value.startswith("sk-ant-oat"):
        raise AppSettingsError(
            "ERROR_CLAUDE_KEY_INVALID",
            "That is a Claude subscription token. Mucaro needs an API key from console.anthropic.com (it starts with sk-ant-api).",
        )
    if not value.startswith("sk-ant-") or any(char.isspace() for char in value) or not 30 <= len(value) <= 400:
        raise AppSettingsError("ERROR_CLAUDE_KEY_INVALID", "That does not look like an Anthropic API key (it starts with sk-ant-).")
    return value


def store_api_key(key: str, cfg: Settings = settings) -> str:
    """Save the key and return the store used; the other store is cleared so only one copy exists."""
    value = validate_api_key(key)
    if secret_store_kind() == "keychain":
        keychain = _keychain(cfg)
        if keychain is not None:
            try:
                keychain.set(value)
                _write_file_key(cfg, None)
                return "keychain"
            except OSError:
                pass
    _write_file_key(cfg, value)
    return "file"


def delete_api_key(cfg: Settings = settings) -> None:
    if secret_store_kind() == "keychain":
        keychain = _keychain(cfg)
        if keychain is not None:
            try:
                keychain.delete()
            except OSError:
                pass
    _write_file_key(cfg, None)


def live_consent(cfg: Settings = settings) -> tuple[bool, str | None]:
    if load_app_settings(cfg)["live_consent"]:
        return True, "settings"
    if os.getenv("MUCARO_AI_LIVE_MODE") == "1":
        return True, "env"
    return False, None


def model_info(model: str) -> dict[str, Any]:
    return AGENT_MODELS.get(model) or AGENT_MODELS[DEFAULT_MODEL]


def public_models() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "label": info["label"],
            "summary": info["summary"],
            "efforts": list(info["efforts"]),
            "default_effort": info["default_effort"],
            "pricing": info["pricing"],
            "code_execution": info["code_execution"],
        }
        for model_id, info in AGENT_MODELS.items()
    ]


def sdk_available() -> bool:
    return importlib.util.find_spec("anthropic") is not None


def claude_status(cfg: Settings = settings) -> dict[str, Any]:
    """What the Settings screen shows and what the agent checks before every turn. Never includes the key."""
    values = load_app_settings(cfg)
    key, source = get_api_key(cfg)
    consent, consent_source = live_consent(cfg)
    if not sdk_available():
        status, detail = "CLAUDE_SDK_MISSING", "The Anthropic SDK is not installed; open Mucaro AI again to finish setup."
    elif not key:
        status, detail = "CLAUDE_KEY_MISSING", "Add your Anthropic API key in Settings to get full answers."
    elif not consent:
        status, detail = "CLAUDE_CONSENT_REQUIRED", "Allow Mucaro to send your messages to Anthropic in Settings."
    else:
        status, detail = "CLAUDE_READY", "Claude is connected."
    return {
        "status": status,
        "ready": status == "CLAUDE_READY",
        "detail": detail,
        "key_present": bool(key),
        "key_source": source,
        "key_hint": f"…{key[-4:]}" if key else None,
        "secret_store": secret_store_kind(),
        "live_consent": consent,
        "consent_source": consent_source,
        **values,
        "models": public_models(),
        "efforts": list(EFFORTS),
    }
