"""Positive and adversarial tests for the governed learning candidate ledger."""
import copy
import pytest

from backend.app.governed_learning_loop import (
    LearningRun, digest, verify_receipt, PHASES,
)


def make_run():
    run = LearningRun("run-001", "Research a supported company", digest({"input": 1}))
    event = run.observe(phase="PERCEIVE", state="PASS", evidence_digest=digest("source"),
                        details={"source": "authoritative"})
    return run, event


def test_phase_contract_is_complete():
    assert len(PHASES) == 12
    assert PHASES[0] == "PERCEIVE" and PHASES[-1] == "RECOVER"


def test_replay_and_hash_chain():
    run, first = make_run()
    second = run.observe(phase="UNDERSTAND", state="PASS", evidence_digest=digest("interpretation"))
    receipt = run.receipt()
    assert second["prev_hash"] == first["event_hash"]
    assert verify_receipt(receipt)
    assert receipt == run.receipt()


def test_rejects_out_of_order_phase():
    run, _ = make_run()
    run.observe(phase="PLAN", state="PASS", evidence_digest=digest("plan"))
    with pytest.raises(ValueError):
        run.observe(phase="UNDERSTAND", state="PASS", evidence_digest=digest("late"))


def test_fails_closed_on_unverified_or_duplicate_sources():
    run, event = make_run()
    with pytest.raises(ValueError):
        run.propose(proposal_id="p", destination="skill", source_event_hashes=[digest("unknown")],
                    candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))
    with pytest.raises(ValueError):
        run.propose(proposal_id="p", destination="skill",
                    source_event_hashes=[event["event_hash"], event["event_hash"]],
                    candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))


def test_fails_closed_on_failed_evidence():
    run, _ = make_run()
    failed = run.observe(phase="UNDERSTAND", state="FAIL", evidence_digest=digest("bad"))
    with pytest.raises(ValueError):
        run.propose(proposal_id="bad", destination="workflow",
                    source_event_hashes=[failed["event_hash"]],
                    candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))


def test_denies_undeclared_protected_destination():
    run, first = make_run()
    for destination in ("core_policy", "production", "capability_authority", "other"):
        with pytest.raises(ValueError):
            run.propose(proposal_id=destination, destination=destination,
                        source_event_hashes=[first["event_hash"]],
                        candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))


def test_eligibility_requires_independent_measured_improvement_and_approval():
    run, first = make_run()
    run.propose(proposal_id="p", destination="skill",
                source_event_hashes=[first["event_hash"]],
                candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))
    result = run.evaluate_proposal(
        "p", baseline_score=.5, candidate_score=.7,
        regression_pass=True, independent_evaluator=True,
        permitted_use=True, operator_approved=True,
    )
    assert result["status"] == "PROMOTION_ELIGIBLE"
    assert not result["promoted"]
    assert verify_receipt(run.receipt())


@pytest.mark.parametrize("override", [
    {"candidate_score": .4},
    {"candidate_score": .5},
    {"regression_pass": False},
    {"independent_evaluator": False},
    {"permitted_use": False},
    {"operator_approved": False},
])
def test_rejects_unsafe_or_ineffective_improvements(override):
    run, first = make_run()
    run.propose(proposal_id="p", destination="training_candidate",
                source_event_hashes=[first["event_hash"]],
                candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))
    args = dict(baseline_score=.5, candidate_score=.7, regression_pass=True,
                independent_evaluator=True, permitted_use=True, operator_approved=True)
    args.update(override)
    assert run.evaluate_proposal("p", **args)["status"] == "REJECTED"


def test_detects_tampered_event():
    run, _ = make_run()
    receipt = run.receipt()
    tampered = copy.deepcopy(receipt)
    tampered["events"][0]["state"] = "FAIL"
    assert not verify_receipt(tampered)


def test_duplicate_proposal_rejected():
    run, first = make_run()
    args = dict(proposal_id="p", destination="memory",
                source_event_hashes=[first["event_hash"]],
                candidate_digest=digest("c"), benchmark_digest=digest("b"))
    run.propose(**args)
    with pytest.raises(ValueError):
        run.propose(**args)


@pytest.mark.parametrize("score", [-1, 1.1, True, float("nan")])
def test_invalid_benchmark_rejected(score):
    run, first = make_run()
    run.propose(proposal_id="p", destination="skill",
                source_event_hashes=[first["event_hash"]],
                candidate_digest=digest("candidate"), benchmark_digest=digest("benchmark"))
    with pytest.raises(ValueError):
        run.evaluate_proposal(
            "p", baseline_score=score, candidate_score=.7,
            regression_pass=True, independent_evaluator=True,
            permitted_use=True, operator_approved=True,
        )
