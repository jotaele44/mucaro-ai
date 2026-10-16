from pathlib import Path
import pytest
from backend.app.governed_learning_loop import digest
from backend.app.leased_agent_queue import LeasedAgentQueue, WorkerGateError

def init(tmp_path):
    q=LeasedAgentQueue(tmp_path/"queue.sqlite")
    q.submit("j","r","EXECUTE",digest("input"),digest("policy"),max_attempts=2)
    return q

def test_claim_complete_and_restart(tmp_path):
    q=init(tmp_path)
    token=q.claim("j","w",100,10,permitted=True)
    assert token["fence"] == 1
    assert q.finish("j","w",1,{"status":"PASS","value":2},105)
    q.close()
    q=LeasedAgentQueue(tmp_path/"queue.sqlite")
    assert q.state("j")["state"] == "SUCCEEDED"
    assert q.claim("j","w2",120,10,permitted=True) is None
    q.close()

def test_stale_fence_rejected_after_lease_expiry(tmp_path):
    q=init(tmp_path)
    a=q.claim("j","w1",100,10,permitted=True)
    b=q.claim("j","w2",111,10,permitted=True)
    assert b["fence"] > a["fence"]
    assert not q.finish("j","w1",a["fence"],{"status":"PASS"},112)
    assert q.finish("j","w2",b["fence"],{"status":"PASS"},112)
    q.close()

def test_live_lease_excludes_duplicate_claim(tmp_path):
    q=init(tmp_path)
    q.claim("j","w1",100,10,permitted=True)
    assert q.claim("j","w2",105,10,permitted=True) is None
    q.close()

def test_permission_denial_never_claims(tmp_path):
    q=init(tmp_path)
    assert q.claim("j","w",100,10,permitted=False) is None
    assert q.state("j")["state"] == "BLOCKED"
    q.close()

def test_cancel_fences_active_worker(tmp_path):
    q=init(tmp_path)
    t=q.claim("j","w",100,10,permitted=True)
    q.cancel("j")
    assert not q.finish("j","w",t["fence"],{"status":"PASS"},102)
    assert q.state("j")["state"] == "CANCELLED"
    q.close()

def test_attempt_limit_fails_closed(tmp_path):
    q=init(tmp_path)
    q.claim("j","w1",100,10,permitted=True)
    q.claim("j","w2",111,10,permitted=True)
    assert q.claim("j","w3",122,10,permitted=True) is None
    assert q.state("j")["state"] == "FAILED"
    q.close()

def test_identity_conflict_and_duplicate_phase(tmp_path):
    q=init(tmp_path)
    q.submit("j","r","EXECUTE",digest("input"),digest("policy"),max_attempts=2)
    with pytest.raises(WorkerGateError):
        q.submit("j","other","EXECUTE",digest("input"),digest("policy"),max_attempts=2)
    with pytest.raises(WorkerGateError):
        q.submit("j2","r","EXECUTE",digest("input"),digest("policy"))
    q.close()

def test_bad_results_rejected(tmp_path):
    q=init(tmp_path)
    q.claim("j","w",100,10,permitted=True)
    with pytest.raises(WorkerGateError):
        q.finish("j","w",1,{"status":"OPEN"},102)
    q.close()

def test_forbid_promotion_and_invalid_input(tmp_path):
    q=LeasedAgentQueue(tmp_path/"queue.sqlite")
    with pytest.raises(WorkerGateError):
        q.submit("x","r","PROMOTE",digest("x"),digest("policy"))
    with pytest.raises(WorkerGateError):
        q.submit("x","r","EXECUTE","unknown",digest("policy"))
    q.close()

def test_idempotency_key_stable_across_expiry(tmp_path):
    q=init(tmp_path)
    first=q.claim("j","w",100,10,permitted=True)
    second=q.claim("j","w2",111,10,permitted=True)
    assert first["idempotency_key"] == second["idempotency_key"]
    q.close()
