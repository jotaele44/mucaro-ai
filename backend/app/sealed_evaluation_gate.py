"""Credential-bound sealed fixture evaluation: no model training or promotion.

Separation of duties is enforced only for configured signing principals and
secrets. Independent key custody and sealed fixture acquisition MUST be proven
outside this library before claiming independent operational evaluation.
"""
from __future__ import annotations
import hmac
import json
from hashlib import sha256
from typing import Any

from .governed_learning_loop import canonical_bytes,digest
from .independent_evaluation_gate import assess_holdout,EvaluationGateError

class SealedEvaluationError(RuntimeError):
    pass

SCHEMA="mucaro-sealed-holdout/1.0"

def _key(value):
    if not isinstance(value,bytes) or len(value)<32:
        raise SealedEvaluationError("STRONG_EVALUATOR_KEY_REQUIRED")
    return value

def _sig(key,content):
    return hmac.new(_key(key),canonical_bytes(content),sha256).hexdigest()

class SealedHoldoutAuthority:
    def __init__(self,*,authority_id:str,authority_key:bytes):
        if not authority_id:
            raise SealedEvaluationError("MISSING_SEAL_AUTHORITY")
        self.authority_id=authority_id
        self.key=_key(authority_key)

    def seal(self,*,benchmark_id:str,truth:dict[str,str],
             reserved_case_ids:tuple[str,...],revision:str)->dict[str,Any]:
        if not benchmark_id or not revision or not isinstance(reserved_case_ids,tuple):
            raise SealedEvaluationError("INVALID_BENCHMARK_IDENTITY")
        if set(reserved_case_ids)!=set(truth) or len(reserved_case_ids)!=len(set(reserved_case_ids)):
            raise SealedEvaluationError("BENCHMARK_MEMBERSHIP_MISMATCH")
        payload={"schema":SCHEMA,"authority_id":self.authority_id,
                 "benchmark_id":benchmark_id,"revision":revision,
                 "truth":truth.copy(),"case_ids":sorted(reserved_case_ids),
                 "truth_digest":digest(truth)}
        return {**payload,"seal":_sig(self.key,payload)}

class VerifiedEvaluationGate:
    def __init__(self,*,trusted_authority_id:str,authority_key:bytes,
                 evaluator_id:str,evaluator_key:bytes):
        if not trusted_authority_id or not evaluator_id or evaluator_id==trusted_authority_id:
            raise SealedEvaluationError("EVALUATOR_TRUST_IDENTITY_INVALID")
        self.authority_id=trusted_authority_id
        self.authority_key=_key(authority_key)
        self.evaluator_id=evaluator_id
        self.evaluator_key=_key(evaluator_key)
        if hmac.compare_digest(self.authority_key,self.evaluator_key):
            raise SealedEvaluationError("EVALUATOR_KEY_NOT_INDEPENDENT")

    def evaluate(self,*,sealed:dict[str,Any],proposal_id:str,proposer_id:str,
                 baseline:dict[str,str],candidate:dict[str,str],
                 training_case_ids:tuple[str,...],improvement_floor:float=0.01):
        if not isinstance(sealed,dict) or set(sealed)!={
            "schema","authority_id","benchmark_id","revision","truth",
            "case_ids","truth_digest","seal"}:
            raise SealedEvaluationError("INVALID_SEALED_BENCHMARK")
        body={k:v for k,v in sealed.items() if k!="seal"}
        try:
            original=sealed["seal"]
            if (not isinstance(original,str) or len(original)!=64 or
                sealed["schema"]!=SCHEMA or sealed["authority_id"]!=self.authority_id or
                not hmac.compare_digest(original,_sig(self.authority_key,body)) or
                digest(sealed["truth"])!=sealed["truth_digest"] or
                set(sealed["truth"])!=set(sealed["case_ids"]) or
                len(sealed["case_ids"])!=len(set(sealed["case_ids"]))):
                raise SealedEvaluationError("HOLDOUT_SEAL_INVALID")
        except (TypeError,ValueError,KeyError):
            raise SealedEvaluationError("HOLDOUT_SEAL_INVALID") from None
        if proposer_id==self.evaluator_id or proposer_id==self.authority_id:
            raise SealedEvaluationError("NON_INDEPENDENT_PROPOSER")
        result=assess_holdout(held_out_truth=sealed["truth"],
            baseline_outputs=baseline,candidate_outputs=candidate,
            training_case_ids=training_case_ids,proposal_id=proposal_id,
            proposer_id=proposer_id,evaluator_id=self.evaluator_id,
            independent_evaluator_authorized=True,
            improvement_floor=improvement_floor)
        receipt={"schema":"mucaro-evaluator-receipt/1.0",
            "evaluator_id":self.evaluator_id,
            "benchmark_id":sealed["benchmark_id"],
            "benchmark_revision":sealed["revision"],
            "benchmark_seal":sealed["seal"],
            "evaluation":result,"promoted":False,
            "scope":"candidate-only; no independent service or real-task effectiveness proof"}
        return {**receipt,"signature":_sig(self.evaluator_key,receipt)}

    def verify_receipt(self,receipt:dict[str,Any])->bool:
        if not isinstance(receipt,dict) or "signature" not in receipt:
            return False
        try:
            body={k:v for k,v in receipt.items() if k!="signature"}
            return (receipt["schema"]=="mucaro-evaluator-receipt/1.0"
                and receipt["evaluator_id"]==self.evaluator_id
                and receipt["promoted"] is False
                and receipt["evaluation"]["promoted"] is False
                and receipt["evaluation"]["receipt_hash"]==digest({
                    k:v for k,v in receipt["evaluation"].items() if k!="receipt_hash"})
                and hmac.compare_digest(receipt["signature"],_sig(self.evaluator_key,body)))
        except (ValueError,TypeError,KeyError):
            return False
