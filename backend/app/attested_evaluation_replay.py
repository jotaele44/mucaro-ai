"""V11 evidence-bound evaluation replay (candidate-only).

Recomputes all reported scores and artifact digests from provided immutable-
intent input manifestations. Does not authenticate acquisition of real tasks,
original upstream bytes, custody of signing keys, or actual model execution.
"""
from __future__ import annotations
import math
from typing import Any
from .governed_learning_loop import digest
from .independent_evidence_admission import EvidenceAdmissionGate,EvidenceAdmissionError
from .sealed_evaluation_gate import VerifiedEvaluationGate,SealedEvaluationError
from .independent_evaluation_gate import EvaluationGateError

class ReplayGateError(RuntimeError):
    pass

def benchmark_manifest(sealed:dict[str,Any])->dict[str,Any]:
    return {"benchmark_id":sealed["benchmark_id"],
            "revision":sealed["revision"],
            "truth_digest":sealed["truth_digest"],
            "case_ids":sealed["case_ids"],
            "seal":sealed["seal"]}

def replay_attested_evaluation(*,admission:EvidenceAdmissionGate,
        evaluator:VerifiedEvaluationGate,sealed:dict[str,Any],
        source_attestation:dict[str,Any],
        holdout_attestation:dict[str,Any],
        evaluation_attestation:dict[str,Any],
        baseline:dict[str,str],candidate:dict[str,str],
        training_case_ids:tuple[str,...],
        proposal_id:str,proposer_id:str,
        improvement_floor:float=0.01)->dict[str,Any]:
    """Verify signatures, recompute outputs/delta and reject conflicting claims."""
    try:
        if not isinstance(training_case_ids,tuple):
            raise ReplayGateError("INVALID_TRAINING_MEMBERSHIP")
        if not isinstance(baseline,dict) or not isinstance(candidate,dict):
            raise ReplayGateError("INVALID_OUTPUT_MANIFESTATION")
        eval_receipt=evaluator.evaluate(sealed=sealed,proposal_id=proposal_id,
             proposer_id=proposer_id,baseline=baseline,candidate=candidate,
             training_case_ids=training_case_ids,improvement_floor=improvement_floor)
        if not evaluator.verify_receipt(eval_receipt):
            raise ReplayGateError("INVALID_EVALUATOR_SIGNATURE")
        source=admission._validate(source_attestation,"SOURCE_AUDITOR")
        holdout=admission._validate(holdout_attestation,"HOLDOUT_CUSTODIAN")
        reported=admission._validate(evaluation_attestation,"EVALUATION_AUDITOR")
        manifest=benchmark_manifest(sealed)
        checks={
            "baseline_manifest_digest":digest(baseline),
            "candidate_manifest_digest":digest(candidate),
            "holdout_manifest_digest":digest(manifest),
            "sealed_truth_digest":digest(sealed["truth"]),
            "excluded_training_digest":digest(sorted(training_case_ids)),
            "evaluator_receipt_digest":digest(eval_receipt),
        }
        for field in ("baseline_manifest_digest","candidate_manifest_digest"):
            if source[field]!=checks[field]:
                raise ReplayGateError("OUTPUT_MANIFEST_BYTES_MISMATCH")
        for field in ("holdout_manifest_digest","sealed_truth_digest",
                      "excluded_training_digest"):
            if holdout[field]!=checks[field]:
                raise ReplayGateError("HOLDOUT_OR_TRAINING_MANIFEST_MISMATCH")
        if reported["evaluator_receipt_digest"]!=checks["evaluator_receipt_digest"]:
            raise ReplayGateError("EVALUATOR_RECEIPT_DIGEST_MISMATCH")
        values=eval_receipt["evaluation"]
        if reported["denominator"]!=values["denominator"]:
            raise ReplayGateError("DENOMINATOR_MISMATCH")
        if reported["regression_count"]!=len(values["regressed_cases"]):
            raise ReplayGateError("REGRESSION_COUNT_MISMATCH")
        delta=reported["observed_delta"]
        if (isinstance(delta,bool) or not isinstance(delta,(int,float))
             or not math.isfinite(delta) or abs(delta-values["accuracy_delta"])>1e-12):
            raise ReplayGateError("REPORTED_IMPROVEMENT_MISMATCH")
        admitted=admission.admit(source_attestation=source_attestation,
              holdout_attestation=holdout_attestation,
              evaluation_attestation=evaluation_attestation,
              proposal_id=proposal_id,proposer_id=proposer_id)
        # Both gates must agree; no attestation may upgrade a rejected result.
        is_candidate=(admitted["classification"]=="REVIEW_CANDIDATE"
             and values["classification"]=="PROMOTION_CANDIDATE")
        status="REVIEW_CANDIDATE" if is_candidate else "REJECTED"
        data={"schema":"mucaro-replayed-evaluation/1.0","proposal_id":proposal_id,
          "source_head":admitted["source_head"],
          "admission_receipt_digest":admitted["receipt_hash"],
          "signed_evaluator_receipt_digest":checks["evaluator_receipt_digest"],
          "manifest_digests":checks,"denominator":values["denominator"],
          "baseline_pass":values["baseline_pass"],
          "candidate_pass":values["candidate_pass"],
          "accuracy_delta":values["accuracy_delta"],
          "fixed_cases":values["fixed_cases"],
          "regressed_cases":values["regressed_cases"],
          "classification":status,"promoted":False,
          "limitations":"Input outputs are caller-supplied; no externally verified task run or independently proven key custody"}
        return {**data,"receipt_hash":digest(data)}
    except (EvidenceAdmissionError,SealedEvaluationError,EvaluationGateError,KeyError,TypeError,
            ValueError) as exc:
        raise ReplayGateError("REPLAY_PROVENANCE_DENIED") from exc
