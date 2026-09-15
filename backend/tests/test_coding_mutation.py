from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.coding_mutation import CodingMutationError, apply_patch_transaction
from backend.app.coding_patch import validate_patch_proposal
from backend.app.config import Settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required for coding mutation tests")


def make_settings(root: Path) -> Settings:
    cfg = Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )
    cfg.ensure_dirs()
    return cfg


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "mucaro-tests@example.invalid")
    git(repo, "config", "user.name", "Mucaro Tests")
    (repo / "tracked.txt").write_text("alpha\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("stable\n", encoding="utf-8")
    git(repo, "add", "tracked.txt", "unrelated.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def patch_for(old: str = "alpha", new: str = "beta") -> str:
    return (
        "diff --git a/tracked.txt b/tracked.txt\n"
        "--- a/tracked.txt\n"
        "+++ b/tracked.txt\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


def approved_inputs(repo: Path, cfg: Settings, patch: str) -> tuple[str, str, list[dict]]:
    proposal = validate_patch_proposal(repo, patch, cfg=cfg)
    return (
        git(repo, "rev-parse", "HEAD").strip(),
        hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        proposal["preimages"],
    )


def test_patch_transaction_revalidates_and_applies_without_commit(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for()
    head, patch_hash, preimages = approved_inputs(repo, cfg, patch)

    receipt = apply_patch_transaction(
        repo,
        patch,
        expected_head_sha=head,
        expected_patch_sha256=patch_hash,
        expected_preimages=preimages,
        cfg=cfg,
    )

    assert receipt["status"] == "CODING_MUTATION_APPLIED"
    assert receipt["head_sha"] == head
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "beta\n"
    assert receipt["newly_dirty_paths"] == ["tracked.txt"]
    assert all(receipt["invariants"].values())


def test_unrelated_dirty_work_is_preserved_when_not_targeted(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    (repo / "unrelated.txt").write_text("local\n", encoding="utf-8")
    patch = patch_for()
    head, patch_hash, preimages = approved_inputs(repo, cfg, patch)

    receipt = apply_patch_transaction(
        repo,
        patch,
        expected_head_sha=head,
        expected_patch_sha256=patch_hash,
        expected_preimages=preimages,
        cfg=cfg,
    )

    assert receipt["status"] == "CODING_MUTATION_APPLIED"
    assert (repo / "unrelated.txt").read_text(encoding="utf-8") == "local\n"
    assert "unrelated.txt" not in receipt["patch_paths"]


def test_head_patch_preimage_and_dirty_overlap_drift_fail_closed(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    repo = make_repo(tmp_path)
    patch = patch_for()
    head, patch_hash, preimages = approved_inputs(repo, cfg, patch)

    with pytest.raises(CodingMutationError) as bad_head:
        apply_patch_transaction(repo, patch, expected_head_sha="0" * 40, expected_patch_sha256=patch_hash, expected_preimages=preimages, cfg=cfg)
    assert bad_head.value.status == "ERROR_CODING_MUTATION_HEAD_DRIFT"

    with pytest.raises(CodingMutationError) as bad_patch:
        apply_patch_transaction(repo, patch, expected_head_sha=head, expected_patch_sha256="f" * 64, expected_preimages=preimages, cfg=cfg)
    assert bad_patch.value.status == "ERROR_CODING_MUTATION_PATCH_DRIFT"

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(CodingMutationError) as dirty:
        apply_patch_transaction(repo, patch_for(old="changed"), expected_head_sha=head, expected_patch_sha256=hashlib.sha256(patch_for(old="changed").encode()).hexdigest(), expected_preimages=preimages, cfg=cfg)
    assert dirty.value.status in {"ERROR_CODING_MUTATION_DIRTY_OVERLAP", "ERROR_CODING_MUTATION_PREIMAGE_DRIFT"}
