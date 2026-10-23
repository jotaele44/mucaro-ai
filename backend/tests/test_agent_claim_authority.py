"""Exact HMAC claim scope and policy-rotation adversarial tests."""
from __future__ import annotations
import unittest
from backend.app.capability_policy import CapabilityPolicy
from backend.app.agent_claim_authority import (
    ClaimIssuer, ClaimVerifier, ClaimAuthorityError, policy_digest
)
from backend.app.governed_learning_loop import digest

class ClaimAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.key=b"v5-claim-authority-test-key-at-least-32-bytes"
        self.policy=CapabilityPolicy.build(policy_id="v5-reviewed",
            allowed={"runtime.in_process"})
        self.skills={"mucaro/intelligence-plane-health":"sha256:"+"a"*64}
        self.issuer=ClaimIssuer("reviewed-v5",self.key,self.policy,self.skills)
        self.verifier=ClaimVerifier("reviewed-v5",self.key,self.policy,self.skills)
        self.work={
            "job_id":"job-1","run_id":"run-1","phase":"EXECUTE",
            "input_digest":digest("input"),"policy_hash":policy_digest(self.policy),
            "owner":"worker-A","skill_id":"mucaro/intelligence-plane-health",
            "skill_digest":"sha256:"+"a"*64,
            "issued_at":100,"expires_at":140,
        }

    def issue(self, **overrides):
        args={**self.work,**overrides}
        return self.issuer.issue(**args)

    def verify(self, grant, **overrides):
        args={"job_id":self.work["job_id"],"run_id":self.work["run_id"],
              "phase":self.work["phase"],"input_digest":self.work["input_digest"],
              "policy_hash":self.work["policy_hash"],"owner":self.work["owner"],
              "now":105,"lease_seconds":10}
        args.update(overrides)
        return self.verifier.verify(grant,**args)

    def test_valid_exact_binding(self):
        self.assertTrue(self.verify(self.issue()))

    def test_tamper_rejected(self):
        token=self.issue()
        for name,new in (("owner","worker-B"),("phase","PROMOTE"),
                         ("skill_digest","sha256:"+"b"*64),
                         ("input_digest",digest("altered"))):
            altered={**token,name:new}
            with self.subTest(name=name):
                self.assertFalse(self.verify(altered))

    def test_wrong_worker_and_job_rejected(self):
        grant=self.issue()
        self.assertFalse(self.verify(grant,owner="different-worker"))
        self.assertFalse(self.verify(grant,job_id="different-job"))

    def test_grant_expiration_and_lease_ceiling(self):
        grant=self.issue()
        self.assertFalse(self.verify(grant,now=141))
        self.assertFalse(self.verify(grant,now=130,lease_seconds=11))
        self.assertFalse(self.verify(grant,now=99))

    def test_policy_rotation_rejects_old_grant(self):
        new_policy=CapabilityPolicy.build(policy_id="v5-reviewed-revoked",
            allowed=())
        verifier=ClaimVerifier("reviewed-v5",self.key,new_policy,self.skills)
        args={"job_id":"job-1","run_id":"run-1","phase":"EXECUTE",
              "input_digest":digest("input"),"policy_hash":policy_digest(self.policy),
              "owner":"worker-A","now":105,"lease_seconds":10}
        self.assertFalse(verifier.verify(self.issue(),**args))

    def test_different_verifier_secret_rejected(self):
        wrong=ClaimVerifier("reviewed-v5",b"other-unrelated-key-with-at-least-32-bytes",
                            self.policy,self.skills)
        self.assertFalse(wrong.verify(self.issue(),job_id="job-1",run_id="run-1",
                phase="EXECUTE",input_digest=digest("input"),
                policy_hash=policy_digest(self.policy),owner="worker-A",
                now=105,lease_seconds=10))

    def test_unreviewed_skill_denied_at_issuer(self):
        with self.assertRaises(ClaimAuthorityError):
            self.issue(skill_id="unreviewed/other")

    def test_revoked_capability_denied_at_issuer(self):
        denied=CapabilityPolicy.build(policy_id="deny",allowed=())
        issuer=ClaimIssuer("reviewed-v5",self.key,denied,self.skills)
        with self.assertRaises(ClaimAuthorityError):
            issuer.issue(**{**self.work,"policy_hash":policy_digest(denied)})

    def test_scope_copy_not_mutable_after_creation(self):
        self.skills["unreviewed/other"]="sha256:"+"f"*64
        with self.assertRaises(ClaimAuthorityError):
            self.issue(skill_id="unreviewed/other",skill_digest="sha256:"+"f"*64)
        self.assertNotIn("unreviewed/other",self.verifier.reviewed_skills)

    def test_no_weak_or_missing_key(self):
        with self.assertRaises(ClaimAuthorityError):
            ClaimVerifier("x",b"short",self.policy,self.skills)
        with self.assertRaises(ClaimAuthorityError):
            ClaimIssuer("x",b"short",self.policy,self.skills)

if __name__=="__main__":
    unittest.main()
