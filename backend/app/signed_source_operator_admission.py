"""V15 separately signed, replay-resistant admission for source inspection.

This library verifies integrity against a preprovisioned verifier key. A
signature is NOT proof of an independently authenticated human or key custody.
The caller/administrator must establish issuer identity and authorization.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path
from .gated_git_source_attestation import attest_source,SourceAttestationError
from .permission_engine import PermissionRequest
from .config import Settings

SHA40=re.compile(r"^[0-9a-f]{40}$")
DIGEST=re.compile(r"^sha256:[0-9a-f]{64}$")
class OperatorAdmissionError(RuntimeError): pass

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()

def _mac(key,payload):
    if not isinstance(key,bytes) or len(key)<32:
        raise OperatorAdmissionError("KEY_MATERIAL_UNTRUSTED")
    return hmac.new(key,canonical(payload),hashlib.sha256).hexdigest()

def sign_source_admission(*,signing_key:bytes,issuer_id:str,operator_id:str,
        nonce:str,issued_at:int,expires_at:int,source_head:str,
        source_path:str,source_sha256:str)->dict:
    if not all(isinstance(x,str) and x.strip() for x in
               (issuer_id,operator_id,nonce,source_path)):
        raise OperatorAdmissionError("MISSING_SCOPE_IDENTITY")
    if not (isinstance(issued_at,int) and not isinstance(issued_at,bool)
        and isinstance(expires_at,int) and not isinstance(expires_at,bool)
        and issued_at<expires_at and expires_at-issued_at<=300):
        raise OperatorAdmissionError("INVALID_EXPIRY")
    if not SHA40.fullmatch(source_head) or not DIGEST.fullmatch(source_sha256):
        raise OperatorAdmissionError("INVALID_SOURCE_IDENTITY")
    body={"schema":"mucaro-source-operator-admission/1.0",
          "issuer_id":issuer_id,"operator_id":operator_id,
          "nonce":nonce,"issued_at":issued_at,"expires_at":expires_at,
          "source_head":source_head,"source_path":source_path,
          "source_sha256":source_sha256,
          "capability":"git.read","origin":"operator"}
    return {**body,"signature":_mac(signing_key,body)}

def attest_authorized_source(*,authorization:dict,verifier_key:bytes,
        trusted_issuer_id:str,expected_operator_id:str,root:Path,
        cfg:Settings,replay_db:Path,now:int)->dict:
    if not isinstance(authorization,dict) or set(authorization)!={
        "schema","issuer_id","operator_id","nonce","issued_at","expires_at",
        "source_head","source_path","source_sha256","capability","origin","signature"}:
        raise OperatorAdmissionError("INVALID_SIGNED_ENVELOPE")
    body={k:v for k,v in authorization.items() if k!="signature"}
    try:
        signature=authorization["signature"]
        valid=(isinstance(signature,str) and len(signature)==64 and
            hmac.compare_digest(signature,_mac(verifier_key,body)))
    except (TypeError,ValueError,OverflowError):
        valid=False
    if not valid: raise OperatorAdmissionError("SIGNATURE_DENIED")
    if (authorization["schema"]!="mucaro-source-operator-admission/1.0"
        or authorization["issuer_id"]!=trusted_issuer_id
        or authorization["operator_id"]!=expected_operator_id
        or authorization["origin"]!="operator"
        or authorization["capability"]!="git.read"
        or not isinstance(now,int) or isinstance(now,bool)
        or not isinstance(authorization["issued_at"],int)
        or not isinstance(authorization["expires_at"],int)
        or not authorization["issued_at"]<=now<authorization["expires_at"]
        or authorization["expires_at"]-authorization["issued_at"]>300):
        raise OperatorAdmissionError("UNAUTHORIZED_OR_EXPIRED_SCOPE")
    if (not isinstance(authorization["nonce"],str)
        or not authorization["nonce"].strip()
        or not SHA40.fullmatch(authorization["source_head"])
        or not DIGEST.fullmatch(authorization["source_sha256"])):
        raise OperatorAdmissionError("INVALID_AUTHORIZED_SOURCE")
    # The replay register must be trusted persistent storage. Reserve before
    # probing source; subsequent failure still burns the nonce (fail closed).
    db=Path(replay_db)
    if not db.parent.is_dir():
        raise OperatorAdmissionError("REPLAY_STORE_UNAVAILABLE")
    try:
        with sqlite3.connect(db,timeout=5) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS consumed (issuer TEXT NOT NULL, nonce TEXT NOT NULL, PRIMARY KEY(issuer,nonce))")
            conn.execute("INSERT INTO consumed(issuer,nonce) VALUES (?,?)",
                         (trusted_issuer_id,authorization["nonce"]))
    except (sqlite3.Error, OSError) as exc:
        raise OperatorAdmissionError("REPLAY_OR_STORE_DENIED") from exc
    request=PermissionRequest(capability="git.read",
          subject="source-inspector:"+expected_operator_id,origin="operator")
    try:
        receipt=attest_source(root=root,source_path=authorization["source_path"],
            expected_head=authorization["source_head"],
            expected_sha256=authorization["source_sha256"],
            cfg=cfg,request=request)
    except SourceAttestationError as exc:
        raise OperatorAdmissionError("SOURCE_ATTESTATION_DENIED") from exc
    event={"schema":"mucaro-authorized-source-result/1.0",
           "operator_id":expected_operator_id,
           "issuer_id":trusted_issuer_id,
           "admission_digest":"sha256:"+hashlib.sha256(canonical(authorization)).hexdigest(),
           "source_receipt_digest":receipt["receipt_sha256"],
           "nonce_consumed":True,
           "external_operator_authentication_proven":False,
           "independent_key_custody_proven":False,"promoted":False}
    return {**event,"receipt_sha256":"sha256:"+hashlib.sha256(canonical(event)).hexdigest()}
