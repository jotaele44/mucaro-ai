import hashlib
import subprocess
from pathlib import Path
from dataclasses import replace
import pytest
from backend.app.config import Settings
from backend.app.issuer_trust_policy import IssuerTrustRecord,TrustedIssuerPolicy,IssuerTrustError
from backend.app.signed_source_operator_admission import sign_source_admission

KEY=b"test-only-issuer-key-with-32-or-more-bytes-2026"
def git(root,*args):
    return subprocess.run(["git","-C",str(root),*args],
        capture_output=True,text=True,check=True).stdout.strip()

@pytest.fixture
def context(tmp_path):
    git(tmp_path,"init","-q")
    git(tmp_path,"config","user.name","Fixture")
    git(tmp_path,"config","user.email","fixture@example.invalid")
    file=tmp_path/"model.py"
    file.write_text("source=1\n")
    git(tmp_path,"add","model.py")
    git(tmp_path,"commit","-qm","frozen")
    cfg=Settings(project_root=tmp_path,data_dir=tmp_path/"data",
        input_dir=tmp_path/"data"/"input",processed_dir=tmp_path/"data"/"processed",
        export_dir=tmp_path/"data"/"exports",index_dir=tmp_path/"data"/"indexes",
        sqlite_path=tmp_path/"data"/"processed"/"db.sqlite",
        default_model_provider="mock",default_model_name="mock")
    record=IssuerTrustRecord(issuer_id="fixture-issuer",key_id="k1",
        verifier_key=KEY,allowed_operators=frozenset({"operator-1"}),
        authorized_source_paths=frozenset({"model.py"}),
        not_before=50,not_after=250,
        trust_manifest_sha256="sha256:"+"1"*64)
    auth=sign_source_admission(signing_key=KEY,issuer_id="fixture-issuer",
        operator_id="operator-1",nonce="fresh-nonce",issued_at=100,
        expires_at=160,source_head=git(tmp_path,"rev-parse","HEAD"),
        source_path="model.py",
        source_sha256="sha256:"+hashlib.sha256(file.read_bytes()).hexdigest())
    args=dict(authorization=auth,key_id="k1",operator_id="operator-1",
        now=120,root=tmp_path,cfg=cfg,
        replay_db=tmp_path.parent/(tmp_path.name+"-replay.sqlite"))
    return record,args

def policy(record):
    return TrustedIssuerPolicy(records=(record,),authority_version="fixture-version-1")

def test_signed_scoped_trust_accepts_only_review(context):
    record,args=context
    result=policy(record).admit_and_execute(**args)
    assert result["issuer_key_id"]=="k1"
    assert result["promotion_authorized"] is False
    assert result["external_key_custody_attested"] is False

def test_revoked_issuer_denied_before_execution(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="ISSUER_KEY_REVOKED"):
        policy(replace(record,revoked=True)).admit_and_execute(**args)

def test_unrecognized_key_id_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="UNKNOWN_ISSUER_OR_KEY"):
        policy(record).admit_and_execute(**{**args,"key_id":"k2"})

def test_expired_trust_record_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="KEY_OUTSIDE_VALIDITY_WINDOW"):
        policy(replace(record,not_after=110)).admit_and_execute(**args)

def test_future_trust_record_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="KEY_OUTSIDE_VALIDITY_WINDOW"):
        policy(replace(record,not_before=121)).admit_and_execute(**args)

def test_operator_not_in_allowlist_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="OPERATOR_NOT_AUTHORIZED"):
        policy(replace(record,allowed_operators=frozenset({"different"}))).admit_and_execute(**args)

def test_source_path_not_in_allowlist_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="PATH_NOT_AUTHORIZED"):
        policy(replace(record,authorized_source_paths=frozenset({"another.py"}))).admit_and_execute(**args)

def test_valid_key_wrong_signature_denied(context):
    record,args=context
    claim={**args["authorization"],"source_head":"0"*40}
    with pytest.raises(IssuerTrustError,match="SIGNED_SOURCE_ADMISSION_DENIED"):
        policy(record).admit_and_execute(**{**args,"authorization":claim})

def test_nonce_reuse_denied(context):
    record,args=context
    policy(record).admit_and_execute(**args)
    with pytest.raises(IssuerTrustError,match="SIGNED_SOURCE_ADMISSION_DENIED"):
        policy(record).admit_and_execute(**args)

def test_app_store_distribution_denied(context):
    record,args=context
    with pytest.raises(IssuerTrustError,match="SIGNED_SOURCE_ADMISSION_DENIED"):
        policy(record).admit_and_execute(
            **{**args,"cfg":replace(args["cfg"],distribution_channel="mac_app_store")})

def test_duplicate_issuer_key_denied(context):
    record,_=context
    with pytest.raises(IssuerTrustError,match="DUPLICATE_ISSUER_KEY_BINDING"):
        TrustedIssuerPolicy((record,record),"v1")

def test_unbound_manifest_version_denied(context):
    record,_=context
    with pytest.raises(IssuerTrustError,match="MISSING_AUTHORITY_VERSION"):
        TrustedIssuerPolicy((record,),"")
