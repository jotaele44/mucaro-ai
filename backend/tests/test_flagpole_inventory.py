import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.cli import main as cli_main
from backend.app.flagpole_inventory import (
    ALLOWED_TYPES,
    _canonical_payload,
    classify,
    dirty_paths,
    generate,
    validate,
    validate_schema,
    verify_receipt,
)


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


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "flagpole_inventory.schema.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("print('a')\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_schema_files_outside_schemas_dir_classify_as_schema() -> None:
    assert classify("_architecture/schemas/foo.schema.json") == "schema"
    assert classify("schemas/foo.schema.json") == "schema"
    assert classify("_architecture/mucaro_flagpole_v1.json") == "data"


def test_generated_inventory_matches_published_schema() -> None:
    assert validate_schema(generate(REPO_ROOT), SCHEMA_PATH) == []


def test_schema_rejects_malformed_inventory() -> None:
    inventory = generate(REPO_ROOT)
    inventory["source_sha"] = "NOTASHA"
    assert any(e.startswith("SCHEMA_VIOLATION:source_sha") for e in validate_schema(inventory, SCHEMA_PATH))


def test_generate_rejects_non_sha_ref(tiny_repo: Path) -> None:
    with pytest.raises(ValueError):
        generate(tiny_repo, ref="--upload-pack=evil")


def test_receipt_for_earlier_commit_verifies_after_head_moves(tiny_repo: Path) -> None:
    receipt = generate(tiny_repo)
    (tiny_repo / "b.py").write_text("print('b')\n")
    _git(tiny_repo, "add", "b.py")
    _git(tiny_repo, "commit", "-q", "-m", "second")
    assert "SOURCE_SHA_MISMATCH" in validate(tiny_repo, receipt)  # a HEAD check would now fail...
    assert verify_receipt(tiny_repo, receipt) == []  # ...but the receipt is still valid for its own commit


def test_receipt_detects_tampered_file_identity(tiny_repo: Path) -> None:
    receipt = generate(tiny_repo)
    receipt["files"][0]["blob_sha"] = "0" * 40
    assert "INVENTORY_DIGEST_MISMATCH" in verify_receipt(tiny_repo, receipt)


def test_receipt_detects_forged_file_identity_with_recomputed_digest(tiny_repo: Path) -> None:
    receipt = generate(tiny_repo)
    receipt["files"][0]["blob_sha"] = "0" * 40
    receipt["inventory_sha256"] = hashlib.sha256(_canonical_payload(receipt)).hexdigest()
    errors = verify_receipt(tiny_repo, receipt)
    assert "INVENTORY_DIGEST_MISMATCH" not in errors  # self-consistent forgery...
    assert "RECEIPT_CONTENT_MISMATCH" in errors  # ...still caught against the real commit


def test_receipt_rejects_unknown_and_malformed_sha(tiny_repo: Path) -> None:
    receipt = generate(tiny_repo)
    unknown = {**receipt, "source_sha": "1" * 40}
    assert "RECEIPT_SHA_UNKNOWN" in verify_receipt(tiny_repo, unknown)
    malformed = {**receipt, "source_sha": "--bad"}
    assert "RECEIPT_SHA_INVALID" in verify_receipt(tiny_repo, malformed)


def test_dirty_paths_reports_modified_and_untracked(tiny_repo: Path) -> None:
    assert dirty_paths(tiny_repo) == []
    (tiny_repo / "a.py").write_text("changed\n")
    (tiny_repo / "new.txt").write_text("x\n")
    assert sorted(dirty_paths(tiny_repo)) == ["a.py", "new.txt"]


def test_generate_fails_outside_a_git_repo(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        generate(tmp_path)


def test_cli_verify_head_self_consistency(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["flagpole", "inventory", "verify"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FLAGPOLE_INVENTORY_OK"
    assert payload["mode"] == "HEAD_SELF_CONSISTENCY"


def test_cli_verify_receipt_fails_on_tamper(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    receipt = generate(REPO_ROOT)
    receipt["file_count"] += 1
    stored = tmp_path / "inv.json"
    stored.write_text(json.dumps(receipt))
    assert cli_main(["flagpole", "inventory", "verify", "--inventory", str(stored)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FLAGPOLE_INVENTORY_FAILED"
    assert "FILE_COUNT_MISMATCH" in payload["errors"]
