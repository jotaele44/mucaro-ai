import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.artifact_identity import sha256_bytes
from backend.app.research_benchmark import prepare_blind_pairs
from backend.app.research_comparison import compare_frozen, freeze_scores


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


@pytest.fixture
def frozen():
    public, key = prepare_blind_pairs(
        [
            {
                "case_id": "one",
                "question": "Fixture only",
                "responses": [
                    {"system_id": "alpha", "run_id": "a1", "answer": "alpha answer"},
                    {"system_id": "beta", "run_id": "b1", "answer": "beta answer"},
                ],
            }
        ]
    )
    scores = [
        {
            "case_id": "one",
            "scores": {"A": 70, "B": 70},
            "identity_leakage": False,
            "reviewer_id": "synthetic-test-only",
        }
    ]
    rubric = {"status": "FROZEN_BEFORE_RUNS", "purpose": "test fixture, not empirical quality assessment"}
    p, s, r, k = map(encode, (public, scores, rubric, key))
    seal = encode(freeze_scores(p, s, r))
    return [p, s, r, k, seal, sha256_bytes(seal)]


def test_compare_only_after_exact_freeze(frozen):
    result = compare_frozen(*frozen)
    assert result["ties"] == 1
    assert result["superiority"] == "NOT_ESTABLISHED"
    assert result["release_certification"] == "WITHHELD"


@pytest.mark.parametrize("index", [0, 1, 2, 4])
def test_changed_frozen_bytes_fail(frozen, index):
    frozen[index] += b" "
    with pytest.raises(ValueError):
        compare_frozen(*frozen)


def test_private_identity_swap_after_scores_rejected(frozen):
    key = json.loads(frozen[3])
    key["cases"][0]["mapping"]["A"]["system_id"] = "new-system"
    frozen[3] = encode(key)
    with pytest.raises(ValueError, match="commitment"):
        compare_frozen(*frozen)


def test_reviewer_attribution_required(frozen):
    scores = json.loads(frozen[1])
    del scores[0]["reviewer_id"]
    with pytest.raises(ValueError, match="reviewer"):
        freeze_scores(frozen[0], encode(scores), frozen[2])


def test_incomplete_scores_cannot_be_frozen(frozen):
    with pytest.raises(ValueError):
        freeze_scores(frozen[0], b"[]", frozen[2])


def test_cli_freeze_compare_and_refuse_overwrite(frozen, tmp_path):
    names = ["packet.json", "scores.json", "rubric.json", "key.json"]
    for name, raw in zip(names, frozen[:4]):
        (tmp_path / name).write_bytes(raw)
    script = Path(__file__).resolve().parents[2] / "scripts" / "research_audit.py"
    common = [str(tmp_path / n) for n in names[:3]]
    seal = tmp_path / "seal.json"
    freeze_command = [sys.executable, str(script), "freeze", *common, "--seal", str(seal)]
    result = subprocess.run(freeze_command, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    digest = json.loads(result.stdout)["seal_sha256"]
    duplicate = subprocess.run(freeze_command, text=True, capture_output=True, check=False)
    assert duplicate.returncode == 2
    compare = subprocess.run(
        [
            sys.executable,
            str(script),
            "compare",
            *common,
            "--private-key",
            str(tmp_path / "key.json"),
            "--seal",
            str(seal),
            "--expected-seal-sha256",
            digest,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert compare.returncode == 0, compare.stderr
    assert json.loads(compare.stdout)["superiority"] == "NOT_ESTABLISHED"
