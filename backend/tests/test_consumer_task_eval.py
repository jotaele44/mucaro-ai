import pytest

from backend.app import consumer_task_eval as e
from backend.tests.test_agent_consumers import context, git

__all__ = ["context"]


def test_operational_source_must_be_exact_revision_and_clean(context):
    _cfg, repo, _, _, _ = context
    with pytest.raises(ValueError, match="SOURCE_NOT_FROZEN"):
        e.freeze_tasks(repo)


def test_task_split_uses_payload_identity_and_rejects_duplicate_or_overlapping_bytes(context, monkeypatch):
    _, repo, _, _, _ = context
    monkeypatch.setattr(e, "SOURCE_HEAD", git(repo, "rev-parse", "HEAD"))
    monkeypatch.setattr(e, "SOURCE_BASE", git(repo, "rev-parse", "HEAD"))
    monkeypatch.setattr(e, "TASKS", [("repository_review", "train", "file.txt", "Train"),
                                   ("repository_review", "held_out", "file.txt", "Held out")])
    with pytest.raises(ValueError, match="SPLIT_COLLISION"):
        e.freeze_tasks(repo)


def test_coverage_never_claims_missing_file_and_comparison_evidence(context):
    _, repo, _, _, args = context
    task = {"task_id": "test", "file_path_raw": "file.txt", "file_sha256": args["file_sha256"], "file_bytes": 17}
    monkey_run = {"run_id": "run", "state": "COMPLETED", "plan_sha256": "p", "output_sha256": "o", "results": [
        {"result": {"status": "CODING_REPOSITORY_STATUS_OK", "head_sha": e.SOURCE_HEAD,
                    "repo_root": str(repo.resolve()), "dirty_status": "CLEAN"}}]}
    observed = e.measure(monkey_run, task, {"diff_sha256": "none"}, repo)
    assert observed["coverage"] == 1 / 3
    assert observed["criteria"]["exact_requested_file_bytes"] is False
    assert observed["criteria"]["bound_comparison_bytes"] is False
