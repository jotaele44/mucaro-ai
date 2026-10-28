"""Dependency inventory for standalone independence. Standard library only.

Run: python scripts/standalone_dependency_audit.py --root . --output artifacts/standalone-dependencies.json
Never treat this static inventory as a network isolation or release certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from collections.abc import Iterable

TEXT_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".toml", ".sh", ".ps1", ".cmd", ".yml", ".yaml"}
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".mucaro-runtime", ".pytest_cache"}
PATTERNS = {
    "external_url": re.compile(r"https?://[^\s<>]+"),
    "subprocess": re.compile(r"\b(subprocess\.|Popen\(|os\.system\(|execFile\(|spawn\()"),
    "package_acquisition": re.compile(r"\b(pip install|npm install|npm ci|curl |wget |brew install|Invoke-WebRequest)\b", re.I),
    "model_provider": re.compile(r"\b(anthropic|openai|ollama|llama\.cpp|lmstudio|model_provider)\b", re.I),
    "network_client": re.compile(r"\b(urllib\.request|requests\.|httpx\.|fetch\(|WebSocket\(|socket\.)"),
    "host_binary": re.compile(r"\b(git|docker|podman|tesseract|pdftoppm|chromium|node|python3?)\b", re.I),
}
MANIFEST_PATHS = {"pyproject.toml", "frontend/package.json", "frontend/package-lock.json", "README.md"}

def scan(root: Path, *, exclude_paths: Iterable[Path] = ()) -> dict:
    root = root.resolve()
    excluded = {Path(path).resolve() for path in exclude_paths}
    files = []
    sightings = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.resolve() in excluded or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() not in TEXT_SUFFIXES and rel not in MANIFEST_PATHS:
            continue
        raw = p.read_bytes()
        files.append({"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
        if len(raw) > 3_000_000:
            sightings.append({"path": rel, "class": "UNSCANNED_LARGE_FILE", "line": None, "excerpt": ""})
            continue
        for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            for key, pattern in PATTERNS.items():
                if pattern.search(line):
                    sightings.append({"path": rel, "class": key, "line": number, "excerpt": line[:240]})
    return {
        "schema_version": "mucaro-dependency-inventory/1.0",
        "certification": "AUDIT_ONLY",
        "scope": "static repository inventory; dynamic traffic, optional downloads, executables, native packaging unverified",
        "source_file_count": len(files),
        "sighting_count": len(sightings),
        "files": files,
        "sightings": sightings,
        "invariants": {
            "source_count_matches": len(files) == len({f["path"] for f in files}),
            "sighting_count_matches": len(sightings) == sum(1 for _ in sightings),
        },
    }

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--output")
    args = ap.parse_args()
    target = Path(args.output).resolve() if args.output else None
    result = scan(Path(args.root), exclude_paths=(() if target is None else (target,)))
    serialized = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")

if __name__ == "__main__":
    main()
