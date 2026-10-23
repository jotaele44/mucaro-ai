"""V16 review-only issuer trust and revocation admission.

Trust policy must be provisioned by an independent administrator. It cannot
prove any external human identity, IdP operation, or secure key custody.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet
from .config import Settings
from .signed_source_operator_admission import (
    attest_authorized_source,OperatorAdmissionError)

class IssuerTrustError(RuntimeError): pass

@dataclass(frozen=True)
class IssuerTrustRecord:
    issuer_id: str
    key_id: str
    verifier_key: bytes
    allowed_operators: FrozenSet[str]
    authorized_source_paths: FrozenSet[str]
    not_before: int
    not_after: int
    revoked: bool = False
    trust_manifest_sha256: str = ""

    def __post_init__(self):
        if (not self.issuer_id or not self.key_id
            or not isinstance(self.verifier_key,bytes)
            or len(self.verifier_key)<32
            or not self.allowed_operators
            or not self.authorized_source_paths
            or self.not_before>=self.not_after
            or not self.trust_manifest_sha256.startswith("sha256:")
            or len(self.trust_manifest_sha256)!=71):
            raise IssuerTrustError("INVALID_TRUST_RECORD")

@dataclass(frozen=True)
class TrustedIssuerPolicy:
    records: tuple[IssuerTrustRecord,...]
    authority_version: str

    def __post_init__(self):
        if not self.records or not self.authority_version:
            raise IssuerTrustError("MISSING_AUTHORITY_VERSION")
        if len(set((r.issuer_id,r.key_id) for r in self.records))!=len(self.records):
            raise IssuerTrustError("DUPLICATE_ISSUER_KEY_BINDING")

    def admit_and_execute(self,*,authorization:dict,key_id:str,
                          operator_id:str,now:int,root:Path,cfg:Settings,
                          replay_db:Path)->dict:
        if not isinstance(authorization,dict):
            raise IssuerTrustError("SIGNED_AUTHORIZATION_REQUIRED")
        candidates=[r for r in self.records if
                    r.issuer_id==authorization.get("issuer_id") and r.key_id==key_id]
        if len(candidates)!=1:
            raise IssuerTrustError("UNKNOWN_ISSUER_OR_KEY")
        record=candidates[0]
        if record.revoked:
            raise IssuerTrustError("ISSUER_KEY_REVOKED")
        if (not isinstance(now,int) or isinstance(now,bool)
            or now<record.not_before or now>=record.not_after
            or authorization.get("issued_at",0)<record.not_before
            or authorization.get("expires_at",0)>record.not_after):
            raise IssuerTrustError("KEY_OUTSIDE_VALIDITY_WINDOW")
        if (operator_id not in record.allowed_operators
            or authorization.get("operator_id")!=operator_id):
            raise IssuerTrustError("OPERATOR_NOT_AUTHORIZED")
        if authorization.get("source_path") not in record.authorized_source_paths:
            raise IssuerTrustError("PATH_NOT_AUTHORIZED")
        try:
            result=attest_authorized_source(
                authorization=authorization,
                verifier_key=record.verifier_key,
                trusted_issuer_id=record.issuer_id,
                expected_operator_id=operator_id,
                root=root,cfg=cfg,replay_db=replay_db,now=now)
        except OperatorAdmissionError as exc:
            raise IssuerTrustError("SIGNED_SOURCE_ADMISSION_DENIED") from exc
        return {**result,
            "issuer_key_id":record.key_id,
            "trust_manifest_sha256":record.trust_manifest_sha256,
            "authority_version":self.authority_version,
            "external_key_custody_attested":False,
            "trusted_provider_session_attested":False,
            "promotion_authorized":False}
