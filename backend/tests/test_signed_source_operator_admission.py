import hashlib
import subprocess
from pathlib import Path
from dataclasses import replace
import pytest
from backend.app.config import Settings
from backend.app.signed_source_operator_admission import (
    sign_source_admission,attest_authorized_source,OperatorAdmissionError)

KEY=b"fixture-issuer-key-32-bytes-minimum-value-0000"
def git(root,*args):
    return subprocess.run(["git","-C",str(root),*args],check=True,
         capture_output=True,text=True).stdout.strip()

@pytest.fixture
def context(tmp_path):
    git(tmp_path,"init","-q")
    git(tmp_path,"config","user.email","fixture@example.invalid")
    git(tmp_path,"config","user.name","Fixture")
    file=tmp_path/"model.py"
    file.write_text("CONST=1\n")
    git(tmp_path,"add","model.py")
    git(tmp_path,"commit","-qm","fixture")
    sha="sha256:"+hashlib.sha256(file.read_bytes()).hexdigest()
    cfg=Settings(project_root=tmp_path,data_dir=tmp_path/"data",
        input_dir=tmp_path/"data"/"input",processed_dir=tmp_path/"data"/"processed",
        export_dir=tmp_path/"data"/"exports",index_dir=tmp_path/"data"/"indexes",
        sqlite_path=tmp_path/"data"/"processed"/"db.sqlite",
        default_model_provider="mock",default_model_name="mock")
    claim=sign_source_admission(signing_key=KEY,issuer_id="trusted-external-idp",
        operator_id="named-fixture-operator",nonce="nonce-unique-1",
        issued_at=100,expires_at=180,source_head=git(tmp_path,"rev-parse","HEAD"),
        source_path="model.py",source_sha256=sha)
    return dict(authorization=claim,verifier_key=KEY,
       trusted_issuer_id="trusted-external-idp",
       expected_operator_id="named-fixture-operator",
       root=tmp_path,cfg=cfg,replay_db=tmp_path.parent/(tmp_path.name+"-consumed.sqlite"),now=120)

def test_scoped_one_use_operator_gate(context):
    result=attest_authorized_source(**context)
    assert result["nonce_consumed"] is True
    assert result["promoted"] is False
    assert result["external_operator_authentication_proven"] is False

def test_replay_denied_even_after_process_reentry(context):
    attest_authorized_source(**context)
    with pytest.raises(OperatorAdmissionError,match="REPLAY_OR_STORE_DENIED"):
        attest_authorized_source(**context)

def test_forged_operator_denied(context):
    with pytest.raises(OperatorAdmissionError,match="UNAUTHORIZED_OR_EXPIRED_SCOPE"):
        attest_authorized_source(**{**context,"expected_operator_id":"attacker"})

def test_signature_tampering_denied(context):
    claim={**context["authorization"],"source_head":"0"*40}
    with pytest.raises(OperatorAdmissionError,match="SIGNATURE_DENIED"):
        attest_authorized_source(**{**context,"authorization":claim})

def test_wrong_key_denied(context):
    with pytest.raises(OperatorAdmissionError,match="SIGNATURE_DENIED"):
        attest_authorized_source(**{**context,"verifier_key":b"not-the-issuer-fixture-key-32-bytes-00000"})

def test_expired_denied(context):
    with pytest.raises(OperatorAdmissionError,match="UNAUTHORIZED_OR_EXPIRED_SCOPE"):
        attest_authorized_source(**{**context,"now":181})

def test_before_issuance_denied(context):
    with pytest.raises(OperatorAdmissionError,match="UNAUTHORIZED_OR_EXPIRED_SCOPE"):
        attest_authorized_source(**{**context,"now":99})

def test_app_store_denied_and_nonce_is_consumed(context):
    cfg=replace(context["cfg"],distribution_channel="mac_app_store")
    with pytest.raises(OperatorAdmissionError,match="SOURCE_ATTESTATION_DENIED"):
        attest_authorized_source(**{**context,"cfg":cfg})
    with pytest.raises(OperatorAdmissionError,match="REPLAY_OR_STORE_DENIED"):
        attest_authorized_source(**context)

def test_source_byte_drift_denied(context):
    (context["root"]/"model.py").write_text("CONST=2\n")
    with pytest.raises(OperatorAdmissionError,match="SOURCE_ATTESTATION_DENIED"):
        attest_authorized_source(**context)

def test_unsupported_store_parent_denied(context):
    with pytest.raises(OperatorAdmissionError,match="REPLAY_STORE_UNAVAILABLE"):
        attest_authorized_source(**{**context,"replay_db":context["root"]/"missing"/"db"})

def test_missing_claim_denied(context):
    with pytest.raises(OperatorAdmissionError,match="INVALID_SIGNED_ENVELOPE"):
        attest_authorized_source(**{**context,"authorization":{}})

def test_oversized_authorization_lifetime_refused():
    with pytest.raises(OperatorAdmissionError,match="INVALID_EXPIRY"):
        sign_source_admission(signing_key=KEY,issuer_id="issuer",
            operator_id="person",nonce="n",issued_at=100,expires_at=401,
            source_head="a"*40,source_path="x.py",
            source_sha256="sha256:"+"a"*64)
