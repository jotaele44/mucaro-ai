from copy import deepcopy
from pathlib import Path

from backend.app.flagpole_inventory import ALLOWED_TYPES, generate, validate


def test_exact_tree_inventory_closes_denominator_and_identity() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    assert inventory["file_count"] == len(inventory["files"])
    assert sum(inventory["classification_counts"].values()) == inventory["file_count"]
    assert set(inventory["classification_counts"]) == ALLOWED_TYPES
    assert [row["path"] for row in inventory["files"]] == sorted(row["path"] for row in inventory["files"])
    assert validate(repo, inventory) == []


def test_inventory_rejects_stale_sha() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    inventory["source_sha"] = "0" * 40
    assert "SOURCE_SHA_MISMATCH" in validate(repo, inventory)


def test_inventory_rejects_omitted_file_and_count_mismatch() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    inventory["files"] = inventory["files"][:-1]
    errors = validate(repo, inventory)
    assert "FILE_COUNT_MISMATCH" in errors
    assert "TREE_DENOMINATOR_MISMATCH" in errors


def test_inventory_rejects_duplicate_path() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    inventory["files"].append(deepcopy(inventory["files"][0]))
    assert "DUPLICATE_PATH" in validate(repo, inventory)


def test_inventory_rejects_blob_identity_mismatch() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    inventory["files"][0]["blob_sha"] = "f" * 40
    errors = validate(repo, inventory)
    assert any(error.startswith("BLOB_IDENTITY_MISMATCH:") for error in errors)


def test_inventory_rejects_noncanonical_order_and_unknown_class() -> None:
    repo = Path(__file__).resolve().parents[2]
    inventory = generate(repo)
    inventory["files"][0], inventory["files"][1] = inventory["files"][1], inventory["files"][0]
    inventory["files"][0]["classification"] = "mystery"
    errors = validate(repo, inventory)
    assert "NONCANONICAL_ORDER" in errors
    assert "UNKNOWN_CLASSIFICATION" in errors
    assert "CLASSIFICATION_ARITHMETIC_MISMATCH" in errors
