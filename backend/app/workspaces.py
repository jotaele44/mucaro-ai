from __future__ import annotations

from typing import Any

from .config import Settings, settings
from .utils import read_json, stable_id, utc_now, write_json

WORKSPACE_PROFILES_NAME = "workspace_profiles.json"


def _profiles_path(cfg: Settings = settings):
    return cfg.processed_dir / WORKSPACE_PROFILES_NAME


def default_workspace_profile(cfg: Settings = settings) -> dict[str, Any]:
    return {
        "profile_id": stable_id("workspace", "default", str(cfg.project_root), length=12),
        "name": "default",
        "project_root": str(cfg.project_root),
        "data_dir": str(cfg.data_dir),
        "active": cfg.workspace_profile == "default",
        "created_at": None,
        "updated_at": None,
        "notes": "Runtime default profile from current Settings.",
    }


def list_workspace_profiles(cfg: Settings = settings) -> list[dict[str, Any]]:
    payload = read_json(_profiles_path(cfg), [])
    profiles = [item for item in payload if isinstance(item, dict) and isinstance(item.get("name"), str)]
    if not any(profile.get("name") == "default" for profile in profiles):
        profiles.append(default_workspace_profile(cfg))
    return sorted(profiles, key=lambda item: str(item.get("name", "")))


def upsert_workspace_profile(name: str, cfg: Settings = settings, *, notes: str = "") -> dict[str, Any]:
    resolved_name = " ".join(name.strip().split())
    if not resolved_name:
        return {"status": "ERROR_WORKSPACE_NAME_REQUIRED"}
    now = utc_now()
    profiles = [profile for profile in list_workspace_profiles(cfg) if profile.get("name") != resolved_name]
    existing = next((profile for profile in list_workspace_profiles(cfg) if profile.get("name") == resolved_name), None)
    profile = {
        "profile_id": existing.get("profile_id") if existing else stable_id("workspace", resolved_name, str(cfg.project_root), length=12),
        "name": resolved_name,
        "project_root": str(cfg.project_root),
        "data_dir": str(cfg.data_dir),
        "active": resolved_name == cfg.workspace_profile,
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "notes": notes,
    }
    profiles.append(profile)
    write_json(_profiles_path(cfg), profiles)
    return {"status": "WORKSPACE_PROFILE_SAVED", "profile": profile}
