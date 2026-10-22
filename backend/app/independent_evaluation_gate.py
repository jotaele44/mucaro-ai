"""Independent benchmark comparison from frozen exact-answer fixtures.

This is a deterministic evaluation contract, not a separately authenticated
evaluator process or proof of model-weight training.
"""
from __future__ import annotations
import math
from typing import Any
from .governed_learning_loop import digest

SCHEMA = "mucaro-independent-evaluation/1.0"

class EvaluationGateError(RuntimeError):
    pass

def _records(items: dict[str, Any], label: str) -> None:
    if not isinstance(items, dict) or len(items) < 4 or any(
        not isinstance(k, str) or not k or not isinstance(v, str) for k, v in items.items()
    ):
        raise EvaluationGateError("INVALID_"+label)

def assess_holdout(*, held_out_truth: dict[str,str],
                   baseline_outputs: dict[str,str],
                   candidate_outputs: dict[str,str],
                   training_case_ids: tuple[str, ...],
                   proposal_id: str, proposer_id: str, evaluator_id: str,
                   independent_evaluator_authorized: bool,
                   improvement_floor: float = 0.01) -> dict[str, Any]:
    """Evaluate exactly the predeclared holdout; no implicit subset or missing rows.

    The caller must authenticate the evaluator out of band; this routine
    rejects self-evaluation but does not establish cryptographic identity.
    """
    _records(held_out_truth, "HOLDOUT")
    if not proposal_id or not proposer_id or not evaluator_id:
        raise EvaluationGateError("IDENTITY_REQUIRED")
    if proposer_id == evaluator_id or not independent_evaluator_authorized:
        raise EvaluationGateError("INDEPENDENT_EVALUATOR_REQUIRED")
    if not isinstance(training_case_ids, tuple) or len(set(training_case_ids)) != len(training_case_ids):
        raise EvaluationGateError("INVALID_TRAINING_SET")
    if set(training_case_ids) & set(held_out_truth):
        raise EvaluationGateError("TRAINING_HOLDOUT_OVERLAP")
    if not isinstance(baseline_outputs, dict) or set(baseline_outputs) != set(held_out_truth):
        raise EvaluationGateError("INCOMPLETE_BASELINE")
    if not isinstance(candidate_outputs, dict) or set(candidate_outputs) != set(held_out_truth):
        raise EvaluationGateError("INCOMPLETE_CANDIDATE")
    if any(not isinstance(x,str) for x in [*baseline_outputs.values(), *candidate_outputs.values()]):
        raise EvaluationGateError("INVALID_PREDICTION")
    if (isinstance(improvement_floor, bool) or not isinstance(improvement_floor,(int,float))
            or not math.isfinite(improvement_floor) or not 0 <= improvement_floor <= 1):
        raise EvaluationGateError("INVALID_IMPROVEMENT_FLOOR")
    base_pass = {key: baseline_outputs[key] == value for key,value in held_out_truth.items()}
    new_pass = {key: candidate_outputs[key] == value for key,value in held_out_truth.items()}
    regressions = sorted(k for k in held_out_truth if base_pass[k] and not new_pass[k])
    fixes = sorted(k for k in held_out_truth if not base_pass[k] and new_pass[k])
    count = len(held_out_truth)
    base_accuracy = sum(base_pass.values())/count
    candidate_accuracy = sum(new_pass.values())/count
    delta = candidate_accuracy-base_accuracy
    eligible = (delta >= improvement_floor and delta > 0 and not regressions and bool(fixes))
    evidence = {
        "schema": SCHEMA, "proposal_id": proposal_id,
        "proposer_id": proposer_id, "evaluator_id": evaluator_id,
        "holdout_digest": digest(held_out_truth),
        "training_case_digest": digest(sorted(training_case_ids)),
        "baseline_output_digest": digest(baseline_outputs),
        "candidate_output_digest": digest(candidate_outputs),
        "denominator": count, "baseline_pass": sum(base_pass.values()),
        "candidate_pass": sum(new_pass.values()), "baseline_accuracy": base_accuracy,
        "candidate_accuracy": candidate_accuracy, "accuracy_delta": delta,
        "fixed_cases": fixes, "regressed_cases": regressions,
        "improvement_floor": improvement_floor,
        "classification": "PROMOTION_CANDIDATE" if eligible else "REJECTED",
        "operational_limitation": "Evaluator identity and fixture independence require external binding",
        "promoted": False,
    }
    return {**evidence,"receipt_hash":digest(evidence)}
