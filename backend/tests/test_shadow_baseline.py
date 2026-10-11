from __future__ import annotations

import pytest

from scripts import resolve_shadow_baseline as baseline


CHECKPOINT = "c" * 40
PASS_OLD = "a" * 40
PROVISIONAL_NEWER = "b" * 40


def test_select_latest_closed_baseline_skips_failed_and_provisional_runs() -> None:
    runs = [
        {
            "id": 8,
            "event": "push",
            "conclusion": "failure",
            "head_branch": "main",
            "head_sha": PROVISIONAL_NEWER,
            "html_url": "https://example.invalid/run/8",
        },
        {
            "id": 7,
            "event": "push",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": PASS_OLD,
            "html_url": "https://example.invalid/run/7",
        },
    ]

    selected = baseline.select_latest_closed_baseline(
        runs,
        checkpoint_sha=CHECKPOINT,
        is_ancestor=lambda candidate, checkpoint: candidate == PASS_OLD and checkpoint == CHECKPOINT,
    )

    assert selected["base_sha"] == PASS_OLD
    assert selected["baseline_run_id"] == 7


def test_select_latest_closed_baseline_rejects_same_checkpoint_and_non_ancestor() -> None:
    runs = [
        {
            "id": 9,
            "event": "push",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": CHECKPOINT,
        },
        {
            "id": 8,
            "event": "push",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": PROVISIONAL_NEWER,
        },
    ]

    with pytest.raises(baseline.ShadowBaselineError):
        baseline.select_latest_closed_baseline(
            runs,
            checkpoint_sha=CHECKPOINT,
            is_ancestor=lambda candidate, checkpoint: False,
        )


def test_select_latest_closed_baseline_ignores_pr_success() -> None:
    runs = [
        {
            "id": 12,
            "event": "pull_request",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": PROVISIONAL_NEWER,
        },
        {
            "id": 6,
            "event": "push",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": PASS_OLD,
        },
    ]

    selected = baseline.select_latest_closed_baseline(
        runs,
        checkpoint_sha=CHECKPOINT,
        is_ancestor=lambda candidate, checkpoint: candidate == PASS_OLD,
    )
    assert selected["baseline_run_id"] == 6


def test_explicit_override_must_be_strict_ancestor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(baseline, "git_is_ancestor", lambda base, checkpoint: True)
    payload = baseline.resolve(
        repository="owner/repo",
        checkpoint_sha=CHECKPOINT,
        workflow="shadow-qa.yml",
        token=None,
        base_override=PASS_OLD,
    )
    assert payload["base_sha"] == PASS_OLD
    assert payload["source"] == "EXPLICIT_OPERATOR_OVERRIDE"

    with pytest.raises(baseline.ShadowBaselineError):
        baseline.resolve(
            repository="owner/repo",
            checkpoint_sha=CHECKPOINT,
            workflow="shadow-qa.yml",
            token=None,
            base_override=CHECKPOINT,
        )


def test_invalid_sha_fails_closed() -> None:
    with pytest.raises(baseline.ShadowBaselineError):
        baseline.select_latest_closed_baseline([], checkpoint_sha="main")
