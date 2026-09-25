from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA = "mucaro-flagpole-exact-tree-inventory/1.0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
    if path.startswith("schemas/") or p.name.endswith(".schema.json"):
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


def generate(repo: Path, ref: str = "HEAD") -> dict[str, Any]:
    repo = repo.resolve()
    if ref != "HEAD" and not SHA_RE.match(ref):
        raise ValueError(f"ref must be HEAD or a full lowercase commit SHA: {ref!r}")
    source_sha = _git(repo, "rev-parse", ref)
    tree_sha = _git(repo, "rev-parse", f"{ref}^{{tree}}")
    raw = _git(repo, "ls-tree", "-r", "-l", "--full-tree", ref)
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


def dirty_paths(repo: Path) -> list[str]:
    """Tracked-but-modified and untracked paths; the HEAD-tree inventory silently ignores both."""
    # Not _git(): its .strip() would eat the leading status space of " M path".
    result = subprocess.run(
        ["git", "-C", str(repo.resolve()), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    entries = result.stdout.split("\0")
    paths: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC":  # rename/copy entries are followed by the original path
            i += 1
    return paths


def validate_schema(inventory: dict[str, Any], schema_path: Path) -> list[str]:
    import jsonschema

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [f"SCHEMA_VIOLATION:{'/'.join(str(p) for p in e.absolute_path) or '<root>'}" for e in validator.iter_errors(inventory)]


def verify_receipt(repo: Path, inventory: dict[str, Any]) -> list[str]:
    """Verify a stored inventory against the exact commit it claims, not against HEAD.

    A committed inventory can never match the HEAD that contains it (adding the file changes the
    tree), so a stored inventory is a receipt for its own source_sha.
    """
    errors = validate(repo, inventory, require_current_head=False)
    source_sha = inventory.get("source_sha")
    if not isinstance(source_sha, str) or not SHA_RE.match(source_sha):
        return [*errors, "RECEIPT_SHA_INVALID"]
    try:
        expected = generate(repo, ref=source_sha)
    except subprocess.CalledProcessError:
        return [*errors, "RECEIPT_SHA_UNKNOWN"]
    if inventory.get("tree_sha") != expected["tree_sha"]:
        errors.append("RECEIPT_TREE_MISMATCH")
    if inventory.get("inventory_sha256") != expected["inventory_sha256"]:
        errors.append("RECEIPT_CONTENT_MISMATCH")
    return errors
