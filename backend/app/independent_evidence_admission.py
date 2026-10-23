"""V10 external-evidence admission gate: verification, never promotion.

This only authenticates configured HMAC keys and binds supplied attestations.
Actual independent key custody, original bytes, and genuine out-of-sample
provenance must be separately proven by an authorized trust administrator.
"""
from __future__ import annotations

from dataclasses import dataclass
import hmac
from hashlib import sha256
import re
from typing import Any, Mapping
from .governed_learning_loop import canonical_bytes, digest

SCHEMA="mucaro-independent-evidence-admission/1.0"
_HEX64=re.compile(r"^sha256:[a-f0-9]{64}$")
_SHA40=re.compile(r"^[a-f0-9]{40}$")

class EvidenceAdmissionError(RuntimeError):
    pass

def _key(key:bytes):
    if not isinstance(key,bytes) or len(key)<32:
        raise EvidenceAdmissionError("TRUSTED_ATTESTOR_KEY_REQUIRED")
    return key

def _signature(key,payload):
    return hmac.new(_key(key),canonical_bytes(payload),sha256).hexdigest()

@dataclass(frozen=True)
class IndependentAttestor:
    principal_id: str
    key: bytes
    role: str

    def __post_init__(self):
        if not self.principal_id or self.role not in {
            "SOURCE_AUDITOR","HOLDOUT_CUSTODIAN","EVALUATION_AUDITOR"
        }:
            raise EvidenceAdmissionError("UNTRUSTED_ATTESTOR_ROLE")
        _key(self.key)

    def attest(self,payload:dict[str,Any])->dict[str,Any]:
        if not isinstance(payload,dict):
            raise EvidenceAdmissionError("INVALID_ATTESTATION_BODY")
        base={"schema":SCHEMA,"principal_id":self.principal_id,
              "role":self.role,"payload":payload}
        return {**base,"signature":_signature(self.key,base)}

class EvidenceAdmissionGate:
    def __init__(self,*,attestors:Mapping[str,IndependentAttestor]):
        needed={"SOURCE_AUDITOR","HOLDOUT_CUSTODIAN","EVALUATION_AUDITOR"}
        if set(attestors)!=needed or any(attestors[r].role!=r for r in needed):
            raise EvidenceAdmissionError("THREE_DISTINCT_AUTHORITY_ROLES_REQUIRED")
        principals=[attestors[r].principal_id for r in sorted(needed)]
        secrets=[attestors[r].key for r in sorted(needed)]
        if len(set(principals))!=3 or len(set(secrets))!=3:
            raise EvidenceAdmissionError("ATTESTOR_COLLUSION_GUARD")
        self.attestors=dict(attestors)

    def _validate(self,attestation,role):
        if not isinstance(attestation,dict) or set(attestation)!={
            "schema","principal_id","role","payload","signature"}:
            raise EvidenceAdmissionError("INVALID_ATTESTATION_ENVELOPE")
        expected=self.attestors[role]
        if (attestation["schema"]!=SCHEMA or attestation["role"]!=role
            or attestation["principal_id"]!=expected.principal_id
            or not isinstance(attestation["signature"],str)
            or not isinstance(attestation["payload"],dict)):
            raise EvidenceAdmissionError("ATTESTOR_IDENTITY_MISMATCH")
        try:
            base={k:v for k,v in attestation.items() if k!="signature"}
            ok=hmac.compare_digest(attestation["signature"],
                                   _signature(expected.key,base))
        except (ValueError,TypeError,OverflowError):
            ok=False
        if not ok:
            raise EvidenceAdmissionError("ATTESTATION_SIGNATURE_INVALID")
        return attestation["payload"]

    def admit(self,*,source_attestation:dict,holdout_attestation:dict,
              evaluation_attestation:dict,proposal_id:str,proposer_id:str)->dict:
        if not proposal_id or not proposer_id or proposer_id in {
            a.principal_id for a in self.attestors.values()
        }:
            raise EvidenceAdmissionError("SEPARATION_OF_DUTIES_DENIED")
        source=self._validate(source_attestation,"SOURCE_AUDITOR")
        holdout=self._validate(holdout_attestation,"HOLDOUT_CUSTODIAN")
        evaluation=self._validate(evaluation_attestation,"EVALUATION_AUDITOR")
        required_source={"repository","source_head","source_tree_digest",
                         "baseline_manifest_digest","candidate_manifest_digest"}
        required_holdout={"benchmark_id","revision","sealed_truth_digest",
                          "holdout_manifest_digest","excluded_training_digest"}
        required_eval={"benchmark_id","revision","source_head","baseline_manifest_digest",
                       "candidate_manifest_digest","holdout_manifest_digest",
                       "evaluator_receipt_digest","observed_delta",
                       "regression_count","denominator"}
        if (set(source)!=required_source or set(holdout)!=required_holdout
            or set(evaluation)!=required_eval):
            raise EvidenceAdmissionError("MISSING_OR_EXTRA_EVIDENCE_FIELD")
        if not _SHA40.fullmatch(source["source_head"]):
            raise EvidenceAdmissionError("INVALID_SOURCE_HEAD")
        for item in (source,holdout,evaluation):
            for name,value in item.items():
                if name.endswith("_digest") and (not isinstance(value,str) or
                    not _HEX64.fullmatch(value)):
                    raise EvidenceAdmissionError("INVALID_MANIFEST_DIGEST")
        for name in ("benchmark_id","revision"):
            if not holdout[name] or holdout[name]!=evaluation[name]:
                raise EvidenceAdmissionError("BENCHMARK_IDENTITY_MISMATCH")
        for name in ("source_head","baseline_manifest_digest",
                     "candidate_manifest_digest"):
            if source[name]!=evaluation[name]:
                raise EvidenceAdmissionError("SOURCE_OR_CANDIDATE_MISMATCH")
        if holdout["holdout_manifest_digest"]!=evaluation["holdout_manifest_digest"]:
            raise EvidenceAdmissionError("HOLDOUT_MANIFEST_MISMATCH")
        delta=evaluation["observed_delta"]
        denominator=evaluation["denominator"]
        regressions=evaluation["regression_count"]
        if (isinstance(delta,bool) or not isinstance(delta,(int,float))
            or not -1<=delta<=1 or isinstance(denominator,bool)
            or not isinstance(denominator,int) or denominator<4
            or isinstance(regressions,bool) or not isinstance(regressions,int)
            or regressions<0 or regressions>denominator):
            raise EvidenceAdmissionError("INVALID_EVALUATION_ARITHMETIC")
        qualified=delta>0 and regressions==0
        evidence={"schema":SCHEMA,"proposal_id":proposal_id,
           "proposer_id":proposer_id,"attestation_digests":{
               "SOURCE_AUDITOR":digest(source_attestation),
               "HOLDOUT_CUSTODIAN":digest(holdout_attestation),
               "EVALUATION_AUDITOR":digest(evaluation_attestation)},
           "source_head":source["source_head"],
           "benchmark_id":holdout["benchmark_id"],"benchmark_revision":holdout["revision"],
           "denominator":denominator,"observed_delta":delta,
           "regression_count":regressions,
           "classification":"REVIEW_CANDIDATE" if qualified else "REJECTED",
           "promoted":False,
           "limitations":"Configured keys do not independently establish real custody, true source bytes, or real-task outcomes"}
        return {**evidence,"receipt_hash":digest(evidence)}
