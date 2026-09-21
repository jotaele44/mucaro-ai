from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "mucaro-flagpole-exact-tree-inventory/1.0"
ALLOWED_TYPES = {"source", "test", "schema", "workflow", "documentation", "configuration", "data", "other"}


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def classify(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if path.startswith("backend/tests/") or "/tests/" in path or p.name.startswith("test_"):
        return "test"
    if path.startswith("schemas/") or suffix == ".schema.json":
        return "schema"
    if path.startswith(".github/workflows/"):
        return "workflow"
    if path.startswith("docs/") or suffix in {".md", ".rst"}:
        return "documentation"
    if p.name in {"pyproject.toml", "package.json", "package-lock.json", ".gitignore"} or suffix in {".toml", ".yaml", ".yml"}:
        return "configuration"
    if path.startswith("data/") or path.startswith("_architecture/") or suffix in {".json", ".csv", ".geojson", ".tif", ".gpkg"}:
        return "data"
    if suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh"}:
        return "source"
    return "other"


def _canonical_payload(inventory: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in inventory.items() if k != "inventory_sha256"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def generate(repo: Path) -> dict[str, Any]:
    repo = repo.resolve()
    source_sha = _git(repo, "rev-parse", "HEAD")
    tree_sha = _git(repo, "rev-parse", "HEAD^{tree}")
    raw = _git(repo, "ls-tree", "-r", "-l", "--full-tree", "HEAD")
    files: list[dict[str, Any]] = []
    for line in raw.splitlines():
        meta, path = line.split("\t", 1)
        mode, obj_type, blob_sha, size_text = meta.split()
        if obj_type != "blob":
            continue
        files.append({
            "path": path,
            "mode": mode,
            "blob_sha": blob_sha,
            "size": int(size_text),
            "classification": classify(path),
        })
    files.sort(key=lambda row: row["path"])
    counts = Counter(row["classification"] for row in files)
    inventory: dict[str, Any] = {
        "schema": SCHEMA,
        "source_sha": source_sha,
        "tree_sha": tree_sha,
        "scope": "complete tracked Git blob tree at exact HEAD",
        "exclusions": [],
        "file_count": len(files),
        "classification_counts": {key: counts.get(key, 0) for key in sorted(ALLOWED_TYPES)},
        "files": files,
    }
    inventory["inventory_sha256"] = hashlib.sha256(_canonical_payload(inventory)).hexdigest()
    return inventory


def validate(repo: Path, inventory: dict[str, Any], *, require_current_head: bool = True) -> list[str]:
    errors: list[str] = []
    if inventory.get("schema") != SCHEMA:
        errors.append("SCHEMA_MISMATCH")
    files = inventory.get("files")
    if not isinstance(files, list):
        return [*errors, "FILES_NOT_LIST"]
    paths = [row.get("path") for row in files if isinstance(row, dict)]
    if len(paths) != len(set(paths)):
        errors.append("DUPLICATE_PATH")
    if paths != sorted(paths):
        errors.append("NONCANONICAL_ORDER")
    if any(not isinstance(row, dict) or row.get("classification") not in ALLOWED_TYPES for row in files):
        errors.append("UNKNOWN_CLASSIFICATION")
    if inventory.get("file_count") != len(files):
        errors.append("FILE_COUNT_MISMATCH")
    counts = Counter(row.get("classification") for row in files if isinstance(row, dict))
    expected_counts = {key: counts.get(key, 0) for key in sorted(ALLOWED_TYPES)}
    if inventory.get("classification_counts") != expected_counts or sum(expected_counts.values()) != len(files):
        errors.append("CLASSIFICATION_ARITHMETIC_MISMATCH")
    digest = hashlib.sha256(_canonical_payload(inventory)).hexdigest()
    if inventory.get("inventory_sha256") != digest:
        errors.append("INVENTORY_DIGEST_MISMATCH")
    if require_current_head:
        current = generate(repo)
        if inventory.get("source_sha") != current["source_sha"]:
            errors.append("SOURCE_SHA_MISMATCH")
        if inventory.get("tree_sha") != current["tree_sha"]:
            errors.append("TREE_SHA_MISMATCH")
        current_by_path = {row["path"]: row for row in current["files"]}
        supplied_by_path = {row.get("path"): row for row in files if isinstance(row, dict)}
        if set(current_by_path) != set(supplied_by_path):
            errors.append("TREE_DENOMINATOR_MISMATCH")
        else:
            for path, expected in current_by_path.items():
                actual = supplied_by_path[path]
                if actual.get("blob_sha") != expected["blob_sha"] or actual.get("size") != expected["size"] or actual.get("mode") != expected["mode"]:
                    errors.append(f"BLOB_IDENTITY_MISMATCH:{path}")
                    break
    return errors


def write_inventory(repo: Path, output: Path) -> dict[str, Any]:
    inventory = generate(repo)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return inventory


def load_and_validate(repo: Path, path: Path, *, require_current_head: bool = True) -> list[str]:
    return validate(repo, json.loads(path.read_text(encoding="utf-8")), require_current_head=require_current_head)
