import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.flagpole_inventory import ALLOWED_TYPES, generate, validate

# One file per classification. The inventory reads a Git tree, so the tests build their own
# repository instead of depending on the checkout they run from (source artifacts carry no .git).
FIXTURE_FILES = {
    "backend/app/core.py": "print('x')\n",
    "backend/tests/test_core.py": "def test_x():\n    pass\n",
    "schemas/thing.schema.json": "{}\n",
    ".github/workflows/ci.yml": "name: ci\n",
    "docs/guide.md": "# Guide\n",
    "pyproject.toml": "[project]\nname = 'x'\n",
    "data/input/sample.csv": "a,b\n1,2\n",
    "LICENSE": "text\n",
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("flagpole-repo")
    for relative, text in FIXTURE_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    return root


def test_exact_tree_inventory_closes_denominator_and_identity(repo: Path) -> None:
    inventory = generate(repo)
    assert inventory["file_count"] == len(inventory["files"])
    assert sum(inventory["classification_counts"].values()) == inventory["file_count"]
    assert set(inventory["classification_counts"]) == ALLOWED_TYPES
    assert all(count == 1 for count in inventory["classification_counts"].values()), inventory["classification_counts"]
    assert [row["path"] for row in inventory["files"]] == sorted(row["path"] for row in inventory["files"])
    assert validate(repo, inventory) == []


def test_inventory_rejects_stale_sha(repo: Path) -> None:
    inventory = generate(repo)
    inventory["source_sha"] = "0" * 40
    assert "SOURCE_SHA_MISMATCH" in validate(repo, inventory)


def test_inventory_rejects_omitted_file_and_count_mismatch(repo: Path) -> None:
    inventory = generate(repo)
    inventory["files"] = inventory["files"][:-1]
    errors = validate(repo, inventory)
    assert "FILE_COUNT_MISMATCH" in errors
    assert "TREE_DENOMINATOR_MISMATCH" in errors


def test_inventory_rejects_duplicate_path(repo: Path) -> None:
    inventory = generate(repo)
    inventory["files"].append(deepcopy(inventory["files"][0]))
    assert "DUPLICATE_PATH" in validate(repo, inventory)


def test_inventory_rejects_blob_identity_mismatch(repo: Path) -> None:
    inventory = generate(repo)
    inventory["files"][0]["blob_sha"] = "f" * 40
    errors = validate(repo, inventory)
    assert any(error.startswith("BLOB_IDENTITY_MISMATCH:") for error in errors)


def test_inventory_rejects_noncanonical_order_and_unknown_class(repo: Path) -> None:
    inventory = generate(repo)
    inventory["files"][0], inventory["files"][1] = inventory["files"][1], inventory["files"][0]
    inventory["files"][0]["classification"] = "mystery"
    errors = validate(repo, inventory)
    assert "NONCANONICAL_ORDER" in errors
    assert "UNKNOWN_CLASSIFICATION" in errors
    assert "CLASSIFICATION_ARITHMETIC_MISMATCH" in errors
