from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CORS_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DEFAULT_DISTRIBUTION_CHANNEL = "local"


def parse_positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def find_project_root() -> Path:
    env_root = os.getenv("MUCARO_AI_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "schemas").exists() and (parent / "backend").exists():
            return parent
    return Path.cwd().resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    input_dir: Path
    processed_dir: Path
    export_dir: Path
    index_dir: Path
    sqlite_path: Path
    default_model_provider: str
    default_model_name: str
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    operator_token: str | None = None
    workspace_profile: str = "default"
    deployment_profile: str = "local"
    allow_public_bind: bool = False
    distribution_channel: str = DEFAULT_DISTRIBUTION_CHANNEL

    @classmethod
    def load(cls) -> "Settings":
        root = find_project_root()
        data_dir = root / "data"
        max_upload_bytes = parse_positive_int(os.getenv("MUCARO_AI_MAX_UPLOAD_BYTES"), DEFAULT_MAX_UPLOAD_BYTES)
        cors_origins = tuple(
            origin.strip()
            for origin in os.getenv("MUCARO_AI_CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS)).split(",")
            if origin.strip()
        )
        return cls(
            project_root=root,
            data_dir=data_dir,
            input_dir=data_dir / "input",
            processed_dir=data_dir / "processed",
            export_dir=data_dir / "exports",
            index_dir=data_dir / "indexes",
            sqlite_path=data_dir / "processed" / "mucaro_ai.sqlite3",
            default_model_provider=os.getenv("MUCARO_AI_MODEL_PROVIDER", "mock"),
            default_model_name=os.getenv("MUCARO_AI_MODEL", "claude-3-5-sonnet-latest"),
            max_upload_bytes=max_upload_bytes,
            cors_origins=cors_origins or DEFAULT_CORS_ORIGINS,
            operator_token=os.getenv("MUCARO_AI_OPERATOR_TOKEN") or None,
            workspace_profile=os.getenv("MUCARO_AI_WORKSPACE_PROFILE", "default"),
            deployment_profile=os.getenv("MUCARO_AI_DEPLOYMENT_PROFILE", "local").strip().lower() or "local",
            allow_public_bind=parse_bool(os.getenv("MUCARO_AI_ALLOW_PUBLIC_BIND"), False),
            distribution_channel=(
                os.getenv("MUCARO_AI_DISTRIBUTION_CHANNEL", DEFAULT_DISTRIBUTION_CHANNEL).strip().lower()
                or DEFAULT_DISTRIBUTION_CHANNEL
            ),
        )

    def ensure_dirs(self) -> None:
        for path in [self.data_dir, self.input_dir, self.processed_dir, self.export_dir, self.index_dir]:
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.load()
