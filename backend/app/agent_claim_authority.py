"""Exact-work, time-bound claim authorization for the bounded agent worker.

A trusted runtime must supply the issuer/verifier secret out of band.
No hard-coded production key, implicit trust from a boolean, or privilege creation.
These signatures authenticate an issuer holding the secret; this alone does not
authenticate an independent process or authorize new Mucaro skill mounts.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from types import MappingProxyType
from typing import Any, Mapping
from .capability_policy import CapabilityPolicy, evaluate_capabilities
from .governed_learning_loop import digest

SCHEMA = "mucaro-agent-claim/1.0"

class ClaimAuthorityError(RuntimeError):
    pass

def _sign(secret: bytes, payload: dict[str, Any]) -> str:
    data=json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
    return hmac.new(secret,data,sha256).hexdigest()

def _secret(key: bytes) -> bytes:
    if not isinstance(key,bytes) or len(key)<32:
        raise ClaimAuthorityError("TRUSTED_SIGNING_KEY_REQUIRED")
    return key

def policy_digest(policy: CapabilityPolicy) -> str:
    return digest({"policy_id":policy.policy_id,
                   "allowed":sorted(policy.allowed),"denied":sorted(policy.denied)})

@dataclass(frozen=True)
class ClaimIssuer:
    issuer_id: str
    secret: bytes
    policy: CapabilityPolicy
    reviewed_skills: Mapping[str,str]

    def __post_init__(self):
        _secret(self.secret)
        if not self.issuer_id or not self.reviewed_skills:
            raise ClaimAuthorityError("ISSUER_SCOPE_REQUIRED")
        object.__setattr__(self,"reviewed_skills",
                           MappingProxyType(dict(self.reviewed_skills)))

    def issue(self, *, job_id: str, run_id: str, phase: str,
              input_digest: str, policy_hash: str, owner: str,
              skill_id: str, skill_digest: str,
              issued_at: int, expires_at: int) -> dict[str,Any]:
        if not (job_id and run_id and owner and phase=="EXECUTE"
                and isinstance(issued_at,int) and not isinstance(issued_at,bool)
                and isinstance(expires_at,int) and not isinstance(expires_at,bool)
                and 0 < expires_at-issued_at<=3600
                and self.reviewed_skills.get(skill_id)==skill_digest
                and policy_hash==policy_digest(self.policy)
                and input_digest.startswith("sha256:")):
            raise ClaimAuthorityError("CLAIM_SCOPE_OR_IDENTITY_DENIED")
        required={"runtime.in_process"}
        decision=evaluate_capabilities(policy=self.policy,
                      declared_capabilities=required,requested_capabilities=required)
        if not decision.authorized:
            raise ClaimAuthorityError("CAPABILITY_POLICY_DENIED")
        payload={"schema":SCHEMA,"issuer_id":self.issuer_id,"job_id":job_id,
                 "run_id":run_id,"phase":phase,"input_digest":input_digest,
                 "policy_digest":policy_hash,"owner":owner,
                 "skill_id":skill_id,"skill_digest":skill_digest,
                 "capabilities":sorted(required),"issued_at":issued_at,
                 "expires_at":expires_at}
        return {**payload,"signature":_sign(self.secret,payload)}

@dataclass(frozen=True)
class ClaimVerifier:
    issuer_id: str
    secret: bytes
    policy: CapabilityPolicy
    reviewed_skills: Mapping[str,str]

    def __post_init__(self):
        _secret(self.secret)
        if not self.issuer_id or not self.reviewed_skills:
            raise ClaimAuthorityError("VERIFIER_SCOPE_REQUIRED")
        object.__setattr__(self,"reviewed_skills",
                           MappingProxyType(dict(self.reviewed_skills)))

    def verify(self, grant: object, *, job_id: str, run_id: str,
               phase: str, input_digest: str, policy_hash: str,
               owner: str, now: int, lease_seconds: int) -> bool:
        if not isinstance(grant,dict) or set(grant)!={
            "schema","issuer_id","job_id","run_id","phase","input_digest",
            "policy_digest","owner","skill_id","skill_digest",
            "capabilities","issued_at","expires_at","signature"}:
            return False
        try:
            signature=grant["signature"]
            if not isinstance(signature,str) or len(signature)!=64:
                return False
            payload={k:v for k,v in grant.items() if k!="signature"}
            if not hmac.compare_digest(signature,_sign(self.secret,payload)):
                return False
            if (policy_hash!=policy_digest(self.policy)
                or payload["schema"]!=SCHEMA or payload["issuer_id"]!=self.issuer_id
                or payload["job_id"]!=job_id or payload["run_id"]!=run_id
                or payload["phase"]!=phase or payload["input_digest"]!=input_digest
                or payload["policy_digest"]!=policy_hash or payload["owner"]!=owner
                or payload["capabilities"]!=["runtime.in_process"]
                or self.reviewed_skills.get(payload["skill_id"])!=payload["skill_digest"]
                or not isinstance(now,int) or isinstance(now,bool)
                or not isinstance(lease_seconds,int) or isinstance(lease_seconds,bool)
                or not isinstance(payload["issued_at"],int) or isinstance(payload["issued_at"],bool)
                or not isinstance(payload["expires_at"],int) or isinstance(payload["expires_at"],bool)
                or payload["issued_at"]>now
                or payload["expires_at"]<now+lease_seconds):
                return False
            return True
        except (KeyError,ValueError,TypeError,OverflowError):
            return False
