"""Permission-engine-preserving Git source attestation tests."""
import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
import pytest
from backend.app.config import Settings
from backend.app.permission_engine import PermissionRequest
from backend.app.gated_git_source_attestation import attest_source,SourceAttestationError,_git as _permission_gated_git

def _git(root,*args):
    return subprocess.run(["git","-C",str(root),*args],check=True,
        capture_output=True,text=True).stdout.strip()

@pytest.fixture
def site(tmp_path):
    _git(tmp_path,"init","-q")
    _git(tmp_path,"config","user.email","ci@example.invalid")
    _git(tmp_path,"config","user.name","CI Source")
    source=tmp_path/"model.py"
    source.write_text("ANSWER = 1\n")
    _git(tmp_path,"add","model.py")
    _git(tmp_path,"commit","-qm","fixture")
    cfg=Settings(project_root=tmp_path,data_dir=tmp_path/"data",
        input_dir=tmp_path/"data"/"input",processed_dir=tmp_path/"data"/"processed",
        export_dir=tmp_path/"data"/"exports",index_dir=tmp_path/"data"/"indexes",
        sqlite_path=tmp_path/"data"/"processed"/"db.sqlite",
        default_model_provider="mock",default_model_name="mock")
    return dict(root=tmp_path,source_path="model.py",
        expected_head=_git(tmp_path,"rev-parse","HEAD"),
        expected_sha256="sha256:"+hashlib.sha256(source.read_bytes()).hexdigest(),
        cfg=cfg,request=PermissionRequest(capability="git.read",
                           subject="fixture-attestor",origin="operator"))

def test_operator_gated_exact_tree(site):
    result=attest_source(**site)
    assert result["head"]==site["expected_head"]
    assert result["clean"] and result["tracked_blob_equivalent"]
    assert result["permission_capability"]=="git.read"
    assert not result["external_source_attestation"]
    assert not result["operator_authentication_attested"]

def test_agent_request_requires_authorized_approval(site):
    with pytest.raises(SourceAttestationError,match="GIT_READ_PERMISSION_DENIED"):
        attest_source(**{**site,"request":PermissionRequest(
            capability="git.read",subject="unapproved-agent",origin="agent")})

def test_planning_never_authorizes_subprocess(site):
    with pytest.raises(SourceAttestationError,match="GIT_READ_PERMISSION_DENIED"):
        attest_source(**{**site,"request":PermissionRequest(
            capability="git.read",subject="plan",origin="planning",approved=True)})

def test_app_store_channel_blocks_git_subprocess(site):
    cfg=replace(site["cfg"],distribution_channel="mac_app_store")
    with pytest.raises(SourceAttestationError,match="GIT_READ_PERMISSION_DENIED"):
        attest_source(**{**site,"cfg":cfg})

def test_wrong_capability_not_accepted(site):
    with pytest.raises(SourceAttestationError,match="GIT_READ_CAPABILITY_REQUIRED"):
        attest_source(**{**site,"request":PermissionRequest(
            capability="subprocess.execute",subject="fixture",origin="operator")})

def test_head_substitution_denied(site):
    with pytest.raises(SourceAttestationError,match="HEAD_MISMATCH"):
        attest_source(**{**site,"expected_head":"a"*40})

def test_source_mutation_fails_closed(site):
    (site["root"]/"model.py").write_text("ANSWER = 2\n")
    with pytest.raises(SourceAttestationError,match="WORKTREE_NOT_CLEAN"):
        attest_source(**site)

def test_untracked_side_effect_fails_closed(site):
    (site["root"]/"untracked").write_text("x")
    with pytest.raises(SourceAttestationError,match="WORKTREE_NOT_CLEAN"):
        attest_source(**site)

def test_fake_digest_denied(site):
    with pytest.raises(SourceAttestationError,match="SOURCE_BYTES_HASH_MISMATCH"):
        attest_source(**{**site,"expected_sha256":"sha256:"+"f"*64})

def test_missing_and_parent_escape_rejected(site):
    with pytest.raises(SourceAttestationError,match="SOURCE_OUTSIDE_OR_MISSING"):
        attest_source(**{**site,"source_path":"../missing"})
    with pytest.raises(SourceAttestationError,match="SOURCE_OUTSIDE_OR_MISSING"):
        attest_source(**{**site,"source_path":"missing.py"})

def test_git_mutation_is_rejected_before_permission_or_process(site):
    with pytest.raises(SourceAttestationError,
                       match="GIT_ARGUMENTS_OUTSIDE_READ_ONLY_ALLOWLIST"):
        _permission_gated_git(site["root"],("reset","--hard","HEAD"),
             cfg=site["cfg"],request=site["request"])
