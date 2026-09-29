from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_api_version_matches_package_metadata() -> None:
    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    module = ast.parse((ROOT / "backend/app/main.py").read_text(encoding="utf-8"))
    versions = [
        keyword.value.value
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FastAPI"
        for keyword in node.keywords
        if keyword.arg == "version" and isinstance(keyword.value, ast.Constant)
    ]
    assert versions == [package_version]
