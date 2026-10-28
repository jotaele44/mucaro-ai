"""Positive and negative static-audit regression controls."""
import json
from pathlib import Path

from scripts.standalone_dependency_audit import scan


def test_inventory_stable_and_counted(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import sqlite3\n", encoding="utf-8")
    first = scan(tmp_path)
    second = scan(tmp_path)
    assert first == second
    assert first["source_file_count"] == 1
    assert first["sighting_count"] == 0
    assert first["certification"] == "AUDIT_ONLY"
    assert all(first["invariants"].values())


def test_network_and_subprocess_are_discovered_not_certified(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        'import subprocess\nimport urllib.request\nsubprocess.run(["git", "status"])\n'
        'urllib.request.urlopen("https://example.org")\n',
        encoding="utf-8",
    )
    result = scan(tmp_path)
    kinds = {s["class"] for s in result["sightings"]}
    assert {"subprocess", "network_client", "external_url", "host_binary"} <= kinds
    assert result["certification"] == "AUDIT_ONLY"


def test_ignored_vendor_tree_cannot_inflate_inventory(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "bad.js").write_text('fetch("https://example.org")')
    (tmp_path / "main.py").write_text("x = 1\n")
    result = scan(tmp_path)
    assert [f["path"] for f in result["files"]] == ["main.py"]


def test_byte_changes_are_visible(tmp_path: Path) -> None:
    path = tmp_path / "main.py"
    path.write_text("x = 1\n")
    before = scan(tmp_path)["files"][0]["sha256"]
    path.write_text("x = 2\n")
    after = scan(tmp_path)["files"][0]["sha256"]
    assert before != after


def test_output_receipt_is_excluded_from_repeated_inventory(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("import sqlite3\n", encoding="utf-8")
    output = tmp_path / "artifacts" / "standalone-dependencies.json"
    output.parent.mkdir()

    first = scan(tmp_path, exclude_paths=(output,))
    output.write_text(json.dumps(first, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    first_bytes = output.read_bytes()

    second = scan(tmp_path, exclude_paths=(output,))
    output.write_text(json.dumps(second, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    assert second == first
    assert output.read_bytes() == first_bytes
    assert "artifacts/standalone-dependencies.json" not in {row["path"] for row in second["files"]}
    assert all(row["path"] != "artifacts/standalone-dependencies.json" for row in second["sightings"])
