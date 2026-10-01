import hashlib
import json
import shutil
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
from backend.tests.test_api import make_settings


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


def test_generated_inventory_matches_published_schema(tiny_repo: Path) -> None:
    assert validate_schema(generate(tiny_repo), SCHEMA_PATH) == []


def test_schema_rejects_malformed_inventory(tiny_repo: Path) -> None:
    inventory = generate(tiny_repo)
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


def test_receipt_source_must_be_a_commit(tiny_repo: Path) -> None:
    receipt = generate(tiny_repo)
    tree = receipt["tree_sha"]
    with pytest.raises(subprocess.CalledProcessError):
        generate(tiny_repo, ref=tree)
    forged = {**receipt, "source_sha": tree}
    forged["inventory_sha256"] = hashlib.sha256(_canonical_payload(forged)).hexdigest()
    assert "RECEIPT_SHA_UNKNOWN" in verify_receipt(tiny_repo, forged)


def test_dirty_paths_reports_modified_and_untracked(tiny_repo: Path) -> None:
    assert dirty_paths(tiny_repo) == []
    (tiny_repo / "a.py").write_text("changed\n")
    (tiny_repo / "new.txt").write_text("x\n")
    assert sorted(dirty_paths(tiny_repo)) == ["a.py", "new.txt"]


@pytest.fixture()
def checkout_cfg(tiny_repo: Path):
    (tiny_repo / "schemas").mkdir()
    shutil.copyfile(SCHEMA_PATH, tiny_repo / "schemas" / SCHEMA_PATH.name)
    _git(tiny_repo, "add", "schemas")
    _git(tiny_repo, "commit", "-q", "-m", "schema")
    return make_settings(tiny_repo)


def test_cli_verify_head_self_consistency(checkout_cfg, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["flagpole", "inventory", "verify"], cfg=checkout_cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FLAGPOLE_INVENTORY_OK"
    assert payload["mode"] == "HEAD_SELF_CONSISTENCY"

    (checkout_cfg.project_root / "untracked.txt").write_text("x\n")
    assert cli_main(["flagpole", "inventory", "verify", "--require-clean"], cfg=checkout_cfg) == 2
    assert "DIRTY_PATH:untracked.txt" in json.loads(capsys.readouterr().out)["errors"]


def test_cli_verify_receipt_passes_and_fails_on_tamper(
    checkout_cfg, tmp_path_factory: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt = generate(checkout_cfg.project_root)
    stored = tmp_path_factory.mktemp("receipts") / "inv.json"
    stored.write_text(json.dumps(receipt))
    assert cli_main(["flagpole", "inventory", "verify", "--inventory", str(stored)], cfg=checkout_cfg) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "RECEIPT"

    receipt["file_count"] += 1
    stored.write_text(json.dumps(receipt))
    assert cli_main(["flagpole", "inventory", "verify", "--inventory", str(stored)], cfg=checkout_cfg) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FLAGPOLE_INVENTORY_FAILED"
    assert "FILE_COUNT_MISMATCH" in payload["errors"]
