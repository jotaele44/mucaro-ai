"""Operator-chosen model server settings, saved on this device.

Environment variables stay the defaults; a value saved here (from the chat's
model setup or the Settings page) wins. The remote API key is write-only: it is
stored in a 0600 file and never returned by any read path.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import Settings, settings

DEFAULT_LOCAL_MODEL_URL = "http://127.0.0.1:11434/v1"
DEFAULT_LOCAL_MODEL = "llama3.1:8b"
SETTINGS_FILENAME = "model_settings.json"
MAX_URL_CHARS = 500
MAX_MODEL_CHARS = 200
MAX_KEY_CHARS = 2000
_LOCK = threading.RLock()


class ModelSettingsError(ValueError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _path(cfg: Settings) -> Path:
    return cfg.processed_dir / SETTINGS_FILENAME


def normalize_base_url(raw: str) -> str:
    """Validate an OpenAI-compatible base URL; a bare host gets the conventional /v1 path."""
    url = str(raw or "").strip()
    if not url or len(url) > MAX_URL_CHARS:
        raise ModelSettingsError("ERROR_MODEL_SETTINGS_URL_INVALID", "Server URL is required.")
    if "://" not in url:
        url = f"http://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ModelSettingsError("ERROR_MODEL_SETTINGS_URL_INVALID", "Server URL must be http(s)://host[:port][/path].")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ModelSettingsError("ERROR_MODEL_SETTINGS_URL_INVALID", "Server URL has an invalid port.") from exc
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path or '/v1'}"


def _read(cfg: Settings) -> dict[str, Any]:
    try:
        payload = json.loads(_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if key in {"local", "remote"} and isinstance(value, dict)}


def _saved(cfg: Settings, section: str, key: str) -> str:
    value = _read(cfg).get(section, {}).get(key)
    return value.strip() if isinstance(value, str) else ""


def local_settings(cfg: Settings = settings) -> dict[str, Any]:
    saved_model = _saved(cfg, "local", "model")
    env_model = (os.getenv("MUCARO_AI_LOCAL_MODEL") or "").strip()
    return {
        "base_url": (_saved(cfg, "local", "base_url") or os.getenv("MUCARO_AI_LOCAL_MODEL_URL") or DEFAULT_LOCAL_MODEL_URL).rstrip("/"),
        "model": saved_model or env_model or DEFAULT_LOCAL_MODEL,
        # A model somebody named is never swapped for another one; only the built-in default may be.
        "model_explicit": bool(saved_model or env_model),
    }


def remote_settings(cfg: Settings = settings) -> dict[str, Any]:
    saved_model = _saved(cfg, "remote", "model")
    env_model = (os.getenv("MUCARO_AI_REMOTE_MODEL") or "").strip()
    return {
        "base_url": (_saved(cfg, "remote", "base_url") or os.getenv("MUCARO_AI_REMOTE_MODEL_URL") or "").strip().rstrip("/"),
        "model": saved_model or env_model,
        "model_explicit": bool(saved_model or env_model),
        "api_key": _saved(cfg, "remote", "api_key") or (os.getenv("MUCARO_AI_REMOTE_MODEL_API_KEY") or "").strip(),
    }


def public_model_settings(cfg: Settings = settings) -> dict[str, Any]:
    """Settings as shown to the UI. The API key itself never leaves this module's callers."""
    local = local_settings(cfg)
    remote = remote_settings(cfg)
    return {
        "local": {"base_url": local["base_url"], "model": local["model"] if local["model_explicit"] else ""},
        "remote": {"base_url": remote["base_url"], "model": remote["model"], "api_key_configured": bool(remote["api_key"])},
    }


def _clean_model(value: Any) -> str:
    model = str(value or "").strip()
    if len(model) > MAX_MODEL_CHARS or any(ord(ch) < 32 for ch in model):
        raise ModelSettingsError("ERROR_MODEL_SETTINGS_MODEL_INVALID", "Model name is too long or contains control characters.")
    return model


def save_model_settings(update: dict[str, Any], cfg: Settings = settings) -> dict[str, Any]:
    """Merge an update into the saved settings.

    Omitted keys are kept. An empty string clears a value (back to the env/default).
    """
    with _LOCK:
        current = _read(cfg)
        for section in ("local", "remote"):
            incoming = update.get(section)
            if not isinstance(incoming, dict):
                continue
            target = dict(current.get(section) or {})
            if incoming.get("base_url") is not None:
                raw = str(incoming["base_url"]).strip()
                target["base_url"] = normalize_base_url(raw) if raw else ""
            if incoming.get("model") is not None:
                target["model"] = _clean_model(incoming["model"])
            if section == "remote" and incoming.get("api_key") is not None:
                key = str(incoming["api_key"]).strip()
                if len(key) > MAX_KEY_CHARS or any(ord(ch) < 33 for ch in key):
                    raise ModelSettingsError("ERROR_MODEL_SETTINGS_KEY_INVALID", "API key is too long or contains whitespace.")
                target["api_key"] = key
            current[section] = {key: value for key, value in target.items() if value}
        cfg.ensure_dirs()
        path = _path(cfg)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return public_model_settings(cfg)
