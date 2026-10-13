from __future__ import annotations

import copy

import pytest

from scripts import shadow_evidence


BASE = "a" * 40
HEAD = "b" * 40
REQUIRED = ["stale", "unexpected_codes"]


def manifest():
    return {
        "schema_version": shadow_evidence.SCHEMA_VERSION,
        "base_sha": BASE,
        "checkpoint_sha": HEAD,
        "vectors": [
            {
                "vector": "stale",
                "state": "PASS",
                "rationale": "Exact stale-input regression.",
                "evidence": [
                    {
                        "evidence_id": "stale-test",
                        "kind": "CHECKPOINT_TEST",
                        "path": "backend/tests/test_example.py",
                        "selector": "test_stale",
                    },
                    {
                        "evidence_id": "stale-execution",
                        "kind": "EXECUTION_RECEIPT",
                        "path": "_architecture/shadow/execution/example.json",
                        "selector": "",
                    },
                ],
            },
            {
                "vector": "unexpected_codes",
                "state": "NOT_APPLICABLE",
                "rationale": "No exit/status-code surface changed.",
                "evidence": [],
            },
        ],
    }


@pytest.fixture(autouse=True)
def fake_checkpoint(monkeypatch):
    monkeypatch.setattr(
        shadow_evidence,
        "_git_show_text",
        lambda checkpoint, path: "def test_stale():\n    pass\n",
    )
    monkeypatch.setattr(shadow_evidence, "_load_execution_receipt", lambda path, checkpoint_sha: None)


def validate(payload):
    return shadow_evidence.validate_manifest(
        payload,
        base_sha=BASE,
        checkpoint_sha=HEAD,
        required_vectors=REQUIRED,
    )


def test_valid_manifest_closes_exact_vector_denominator():
    result = validate(manifest())
    assert set(result) == set(REQUIRED)
    assert result["stale"]["state"] == "PASS"


def test_rejects_stale_base_binding():
    payload = manifest()
    payload["base_sha"] = "c" * 40
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="base_sha mismatch"):
        validate(payload)


def test_rejects_stale_checkpoint_binding():
    payload = manifest()
    payload["checkpoint_sha"] = "c" * 40
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="checkpoint_sha mismatch"):
        validate(payload)


def test_rejects_duplicate_vector_disposition():
    payload = manifest()
    payload["vectors"].append(copy.deepcopy(payload["vectors"][0]))
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="duplicate vector"):
        validate(payload)


def test_rejects_missing_vector():
    payload = manifest()
    payload["vectors"] = payload["vectors"][:1]
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="vector denominator mismatch"):
        validate(payload)


def test_pass_requires_evidence():
    payload = manifest()
    payload["vectors"][0]["evidence"] = []
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="PASS vector requires"):
        validate(payload)


def test_pass_requires_exact_checkpoint_execution_receipt():
    payload = manifest()
    payload["vectors"][0]["evidence"] = payload["vectors"][0]["evidence"][:1]
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="execution receipt"):
        validate(payload)


def test_not_applicable_requires_rationale():
    payload = manifest()
    payload["vectors"][1]["rationale"] = ""
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="requires rationale"):
        validate(payload)


def test_not_applicable_cannot_carry_positive_evidence():
    payload = manifest()
    payload["vectors"][1]["evidence"] = [
        {
            "evidence_id": "na-test",
            "kind": "CHECKPOINT_FILE",
            "path": "backend/tests/test_example.py",
            "selector": "",
        }
    ]
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="must not carry"):
        validate(payload)


def test_duplicate_evidence_id_is_rejected_across_vectors():
    payload = manifest()
    payload["vectors"][1]["state"] = "BLOCKED"
    payload["vectors"][1]["evidence"] = [
        {
            "evidence_id": "stale-test",
            "kind": "CHECKPOINT_FILE",
            "path": "backend/tests/test_example.py",
            "selector": "",
        }
    ]
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="duplicate evidence_id"):
        validate(payload)


def test_pass_test_selector_must_exist_at_checkpoint(monkeypatch):
    monkeypatch.setattr(shadow_evidence, "_git_show_text", lambda checkpoint, path: "def other():\n    pass\n")
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="exact test definition"):
        validate(manifest())


def test_checkpoint_file_selector_must_exist_at_checkpoint():
    payload = manifest()
    payload["vectors"][0]["evidence"][0]["kind"] = "CHECKPOINT_FILE"
    payload["vectors"][0]["evidence"][0]["selector"] = "MISSING_SYMBOL"
    with pytest.raises(
        shadow_evidence.ShadowEvidenceError,
        match="selector for stale-test is absent from checkpoint file",
    ):
        validate(payload)


def test_unknown_vector_is_rejected():
    payload = manifest()
    payload["vectors"][1]["vector"] = "made_up"
    with pytest.raises(shadow_evidence.ShadowEvidenceError, match="unknown vector"):
        validate(payload)
