from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .config import Settings, settings


def validate_json_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        json.load(f)
    return {"path": str(path), "status": "PARSE_OK"}


def validate_schema_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validators.validator_for(schema).check_schema(schema)
    return {"path": str(path), "status": "SCHEMA_OK"}


def validate_project_json(cfg: Settings = settings) -> dict[str, Any]:
    checked = []
    for folder in [cfg.project_root / "schemas", cfg.export_dir, cfg.processed_dir]:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            checked.append(validate_json_file(path))
            if folder.name == "schemas":
                checked.append(validate_schema_file(path))
    return {"status": "JSON_OK", "checked": checked}
