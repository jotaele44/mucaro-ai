from pathlib import Path
import pytest
from backend.app.governed_learning_loop import digest
from backend.app.leased_agent_queue import LeasedAgentQueue, WorkerGateError
from backend.app.capability_policy import CapabilityPolicy
from backend.app.agent_claim_authority import ClaimIssuer, ClaimVerifier, policy_digest

def init(tmp_path):
    key=b"queue-isolated-fixture-key-at-least-thirty-two-bytes"
    policy=CapabilityPolicy.build(policy_id="queue-fixture",allowed={"runtime.in_process"})
    reviewed={"mucaro/intelligence-plane-health":"sha256:"+"a"*64}
    verifier=ClaimVerifier("queue-fixture",key,policy,reviewed)
    q=LeasedAgentQueue(tmp_path/"queue.sqlite",claim_verifier=verifier,
                       clock=lambda: q.test_now[0])
    q.test_now=[100]
    q.test_issuer=ClaimIssuer("queue-fixture",key,policy,reviewed)
    q.test_policy=policy_digest(policy)
    q.submit("j","r","EXECUTE",digest("input"),q.test_policy,max_attempts=2)
    return q

def grant(q, owner="w", job_id="j", run_id="r", input_digest=None):
    return q.test_issuer.issue(job_id=job_id,run_id=run_id,phase="EXECUTE",
        input_digest=input_digest or digest("input"),policy_hash=q.test_policy,
        owner=owner,skill_id="mucaro/intelligence-plane-health",
        skill_digest="sha256:"+"a"*64,issued_at=q.test_now[0],
        expires_at=q.test_now[0]+60)


def test_claim_complete_and_restart(tmp_path):
    q=init(tmp_path)
    token=q.claim("j","w",10,authorization=grant(q))
    assert token["fence"] == 1
    assert q.finish("j","w",1,{"status":"PASS","value":2})
    verifier=q.claim_verifier
    issuer=q.test_issuer
    policy_hash=q.test_policy
    q.close()
    q=LeasedAgentQueue(tmp_path/"queue.sqlite",claim_verifier=verifier,
                       clock=lambda:120)
    assert q.state("j")["state"] == "SUCCEEDED"
    token2=issuer.issue(job_id="j",run_id="r",phase="EXECUTE",
        input_digest=digest("input"),policy_hash=policy_hash,owner="w2",
        skill_id="mucaro/intelligence-plane-health",
        skill_digest="sha256:"+"a"*64,issued_at=120,expires_at=180)
    assert q.claim("j","w2",10,authorization=token2) is None
    q.close()

def test_stale_fence_rejected_after_lease_expiry(tmp_path):
    q=init(tmp_path)
    a=q.claim("j","w1",10,authorization=grant(q,"w1"))
    q.test_now[0]=111
    b=q.claim("j","w2",10,authorization=grant(q,"w2"))
    assert b["fence"] > a["fence"]
    assert not q.finish("j","w1",a["fence"],{"status":"PASS"})
    assert q.finish("j","w2",b["fence"],{"status":"PASS"})
    q.close()

def test_live_lease_excludes_duplicate_claim(tmp_path):
    q=init(tmp_path)
    q.claim("j","w1",10,authorization=grant(q,"w1"))
    q.test_now[0]=105
    assert q.claim("j","w2",10,authorization=grant(q,"w2")) is None
    q.close()

def test_permission_denial_never_claims(tmp_path):
    q=init(tmp_path)
    with pytest.raises(WorkerGateError, match="CLAIM_AUTHORIZATION_DENIED"):
        q.claim("j","w",10,authorization=None)
    assert q.state("j")["state"] == "READY"
    q.close()

def test_cancel_fences_active_worker(tmp_path):
    q=init(tmp_path)
    t=q.claim("j","w",10,authorization=grant(q))
    q.cancel("j")
    assert not q.finish("j","w",t["fence"],{"status":"PASS"})
    assert q.state("j")["state"] == "CANCELLED"
    q.close()

def test_attempt_limit_fails_closed(tmp_path):
    q=init(tmp_path)
    q.claim("j","w1",10,authorization=grant(q,"w1"))
    q.test_now[0]=111
    q.claim("j","w2",10,authorization=grant(q,"w2"))
    q.test_now[0]=122
    assert q.claim("j","w3",10,authorization=grant(q,"w3")) is None
    assert q.state("j")["state"] == "FAILED"
    q.close()

def test_identity_conflict_and_duplicate_phase(tmp_path):
    q=init(tmp_path)
    q.submit("j","r","EXECUTE",digest("input"),q.test_policy,max_attempts=2)
    with pytest.raises(WorkerGateError):
        q.submit("j","other","EXECUTE",digest("input"),q.test_policy,max_attempts=2)
    with pytest.raises(WorkerGateError):
        q.submit("j2","r","EXECUTE",digest("input"),q.test_policy)
    q.close()

def test_bad_results_rejected(tmp_path):
    q=init(tmp_path)
    q.claim("j","w",10,authorization=grant(q))
    with pytest.raises(WorkerGateError):
        q.finish("j","w",1,{"status":"OPEN"})
    q.close()

def test_forbid_promotion_and_invalid_input(tmp_path):
    q=init(tmp_path)
    with pytest.raises(WorkerGateError):
        q.submit("x","r","PROMOTE",digest("x"),q.test_policy)
    with pytest.raises(WorkerGateError):
        q.submit("x","r","EXECUTE","unknown",q.test_policy)
    q.close()

def test_idempotency_key_stable_across_expiry(tmp_path):
    q=init(tmp_path)
    first=q.claim("j","w",10,authorization=grant(q))
    q.test_now[0]=111
    second=q.claim("j","w2",10,authorization=grant(q,"w2"))
    assert first["idempotency_key"] == second["idempotency_key"]
    q.close()

def test_boolean_permission_bypass_rejected(tmp_path):
    q=init(tmp_path)
    with pytest.raises(TypeError):
        q.claim("j","w",10,permitted=True)
    assert q.state("j")["state"]=="READY"
    q.close()

def test_forged_wrong_owner_and_expired_grants(tmp_path):
    q=init(tmp_path)
    valid=grant(q)
    wrong=dict(valid,owner="attacker")
    for forged in (wrong, dict(valid,signature="0"*64), None):
        with pytest.raises(WorkerGateError):
            q.claim("j","w",10,authorization=forged)
    with pytest.raises(WorkerGateError):
        q.claim("j","different-owner",10,authorization=valid)
    q.test_now[0]=170
    with pytest.raises(WorkerGateError):
        q.claim("j","w",10,authorization=valid)
    assert q.state("j")["state"]=="READY"
    q.close()

def test_unconfigured_verifier_fails_closed(tmp_path):
    q=LeasedAgentQueue(tmp_path/"no-verifier.db",clock=lambda:100)
    q.submit("j","r","EXECUTE",digest("input"),digest("policy"))
    with pytest.raises(WorkerGateError, match="CLAIM_AUTHORIZATION_DENIED"):
        q.claim("j","w",10,authorization={})
    assert q.state("j")["state"]=="READY"
    q.close()
