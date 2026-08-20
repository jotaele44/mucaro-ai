from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def load(cls) -> "Settings":
        root = find_project_root()
        data_dir = root / "data"
        return cls(
            project_root=root,
            data_dir=data_dir,
            input_dir=data_dir / "input",
            processed_dir=data_dir / "processed",
            export_dir=data_dir / "exports",
            index_dir=data_dir / "indexes",
            sqlite_path=data_dir / "processed" / "mucaro_ai.sqlite3",
            default_model_provider="anthropic" if os.getenv("ANTHROPIC_API_KEY") else "mock",
            default_model_name=os.getenv("MUCARO_AI_MODEL", "claude-3-5-sonnet-latest"),
        )

    def ensure_dirs(self) -> None:
        for path in [self.data_dir, self.input_dir, self.processed_dir, self.export_dir, self.index_dir]:
            path.mkdir(parents=True, exist_ok=True)


settings = Settings.load()
