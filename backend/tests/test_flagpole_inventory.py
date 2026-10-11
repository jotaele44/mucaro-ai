from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from backend.app.flagpole_inventory import ALLOWED_TYPES, generate, validate


@pytest.fixture()
def inventory_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Inventory fixture\n", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Mucaro Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_exact_tree_inventory_closes_denominator_and_identity(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    assert inventory["file_count"] == len(inventory["files"])
    assert sum(inventory["classification_counts"].values()) == inventory["file_count"]
    assert set(inventory["classification_counts"]) == ALLOWED_TYPES
    assert [row["path"] for row in inventory["files"]] == sorted(row["path"] for row in inventory["files"])
    assert validate(inventory_repo, inventory) == []


def test_inventory_rejects_stale_sha(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    inventory["source_sha"] = "0" * 40
    assert "SOURCE_SHA_MISMATCH" in validate(inventory_repo, inventory)


def test_inventory_rejects_omitted_file_and_count_mismatch(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    inventory["files"] = inventory["files"][:-1]
    errors = validate(inventory_repo, inventory)
    assert "FILE_COUNT_MISMATCH" in errors
    assert "TREE_DENOMINATOR_MISMATCH" in errors


def test_inventory_rejects_duplicate_path(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    inventory["files"].append(deepcopy(inventory["files"][0]))
    assert "DUPLICATE_PATH" in validate(inventory_repo, inventory)


def test_inventory_rejects_blob_identity_mismatch(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    inventory["files"][0]["blob_sha"] = "f" * 40
    errors = validate(inventory_repo, inventory)
    assert any(error.startswith("BLOB_IDENTITY_MISMATCH:") for error in errors)


def test_inventory_rejects_noncanonical_order_and_unknown_class(inventory_repo: Path) -> None:
    inventory = generate(inventory_repo)
    inventory["files"][0], inventory["files"][1] = inventory["files"][1], inventory["files"][0]
    inventory["files"][0]["classification"] = "mystery"
    errors = validate(inventory_repo, inventory)
    assert "NONCANONICAL_ORDER" in errors
    assert "UNKNOWN_CLASSIFICATION" in errors
    assert "CLASSIFICATION_ARITHMETIC_MISMATCH" in errors


def test_inventory_generation_requires_a_git_repository(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("not a checkout\n", encoding="utf-8")

    with pytest.raises(subprocess.CalledProcessError):
        generate(tmp_path)
