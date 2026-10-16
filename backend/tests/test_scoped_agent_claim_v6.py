"""V6 signed workspace/source/principal/budget scope and real local skill pilot.

Source HEAD is an isolated fixture Git marker, not externally attested by
these tests. No secret, production authority, training or deployment is created.
"""
from __future__ import annotations
import secrets
import tempfile
import unittest
from pathlib import Path

from backend.app.agent_claim_authority import ClaimIssuer, policy_digest
from backend.app.capability_policy import CapabilityPolicy
from backend.app.config import Settings
from backend.app.governed_learning_loop import digest
from backend.app.scoped_agent_claim import (
    ExecutionScope, ScopedClaimIssuer, ScopedClaimVerifier,
    ClaimAuthorityError,
)
from backend.app.leased_agent_queue import LeasedAgentQueue
from backend.app.governed_skill_bridge import GovernedSkillBridge, DispatchGateError
from backend.app.goal_reactor import GoalReactor
from backend.app.bounded_self_propulsion_pilot import drive_approved_goal
from backend.app.skill_runtime import (
    SKILL_REGISTRY, mount_skill, activate_skill, list_skill_executions,
)

class ScopedClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.git=self.root/".git"
        self.git.mkdir()
        self.head="a"*40
        (self.git/"HEAD").write_text(self.head,encoding="utf-8")
        self.key=secrets.token_bytes(32)
        self.skill="mucaro/intelligence-plane-health"
        self.skill_digest=SKILL_REGISTRY[self.skill].digest
        self.reviewed={self.skill:self.skill_digest}
        self.policy=CapabilityPolicy.build(policy_id="v6-fixture",allowed={"runtime.in_process"})
        self.scope=ExecutionScope.for_workspace(project_root=self.root,source_head=self.head,
            principal_id="operator-fixture",skill_id=self.skill,max_resource_units=2)
        self.issuer=ScopedClaimIssuer("v6-fixture",self.key,self.policy,self.reviewed,self.scope)
        self.verifier=ScopedClaimVerifier("v6-fixture",self.key,self.policy,self.reviewed,
                                          self.scope,self.root)

    def args(self):
        return dict(job_id="job-1",run_id="run-1",phase="EXECUTE",
            input_digest=digest("input"),policy_hash=policy_digest(self.policy),
            owner="worker-1",skill_id=self.skill,skill_digest=self.skill_digest,
            issued_at=100,expires_at=145)

    def verify(self, token, **changes):
        args=dict(job_id="job-1",run_id="run-1",phase="EXECUTE",
                  input_digest=digest("input"),policy_hash=policy_digest(self.policy),
                  owner="worker-1",now=105,lease_seconds=10)
        args.update(changes)
        return self.verifier.verify(token,**args)

    def test_valid_context_signed(self):
        self.assertTrue(self.verify(self.issuer.issue(**self.args())))

    def test_signature_binds_workspace_revision_principal_and_budget(self):
        token=self.issuer.issue(**self.args())
        for field,value in [
            ("workspace_digest",digest("other-workspace")),
            ("source_head","b"*40),
            ("principal_id","another-principal"),
            ("max_resource_units",9999),
            ("skill_id","another-skill"),
            ("runtime","FEDERATION_WORKER"),
        ]:
            modified={**token,"scope":{**token["scope"],field:value}}
            with self.subTest(field=field):
                self.assertFalse(self.verify(modified))

    def test_stale_head_rejected_without_new_lease(self):
        token=self.issuer.issue(**self.args())
        (self.git/"HEAD").write_text("b"*40,encoding="utf-8")
        self.assertFalse(self.verify(token))

    def test_wrong_workspace_rejected(self):
        other=Path(self.tmp.name)/"elsewhere"
        other.mkdir()
        (other/".git").mkdir()
        (other/".git"/"HEAD").write_text(self.head,encoding="utf-8")
        verifier=ScopedClaimVerifier("v6-fixture",self.key,self.policy,self.reviewed,
                                     self.scope,other)
        t=self.issuer.issue(**self.args())
        self.assertFalse(verifier.verify(t,job_id="job-1",run_id="run-1",
                phase="EXECUTE",input_digest=digest("input"),
                policy_hash=policy_digest(self.policy),owner="worker-1",
                now=105,lease_seconds=10))

    def test_v5_unsigned_scope_not_accepted(self):
        legacy=ClaimIssuer("v6-fixture",self.key,self.policy,self.reviewed)
        self.assertFalse(self.verify(legacy.issue(**self.args())))

    def test_invalid_resource_scope_denied(self):
        for value in (0,-1,True,float("nan")):
            with self.subTest(value=str(value)),self.assertRaises(ClaimAuthorityError):
                ExecutionScope.for_workspace(project_root=self.root,source_head=self.head,
                    principal_id="p",skill_id=self.skill,max_resource_units=value)

    def test_unverified_git_head_denies_runtime(self):
        self.assertTrue(self.scope.matches_runtime(self.root))
        (self.git/"HEAD").unlink()
        self.assertFalse(self.scope.matches_runtime(self.root))

    def test_worker_fence_and_budget_scope_with_real_skill(self):
        data=self.root/"data"
        cfg=Settings(project_root=self.root,data_dir=data,
            input_dir=data/"input",processed_dir=data/"processed",
            export_dir=data/"exports",index_dir=data/"indexes",
            sqlite_path=data/"processed"/"app.db",
            default_model_provider="mock",default_model_name="mock")
        cfg.ensure_dirs()
        mount_skill(self.skill,cfg=cfg,mounted_by="fixture")
        activate_skill(self.skill,cfg=cfg,policy=self.policy,activated_by="fixture")
        queue=LeasedAgentQueue(self.root/"queue.db",claim_verifier=self.verifier)
        goals=GoalReactor(self.root/"goals.db")
        try:
            bridge=GovernedSkillBridge(queue=queue,cfg=cfg,policy=self.policy,
                                       claim_issuer=self.issuer)
            goals.add_goal(goal_id="g",description="authorized bounded check",
                            authority_digest=bridge.policy_digest,limit_cost=2)
            source=digest({"skill_id":self.skill,"skill_digest":self.skill_digest,"arguments":{}})
            for task,deps in (("one",()),("two",("one",))):
                goals.add_task(task_id=task,goal_id="g",phase="EXECUTE",
                    dependencies=deps,source_digest=source,skill_id=self.skill,
                    requested_capabilities=("runtime.in_process",),
                    expected_value=1,confidence=1,relevance=1,cost=1,risk=0)
            done=drive_approved_goal(reactor=goals,bridge=bridge,goal_id="g",
                task_arguments={"one":{},"two":{}},worker_id="worker-fixture",
                max_actions=2)
            self.assertEqual(done["status"],"PASS")
            self.assertEqual(len(list_skill_executions(cfg)["executions"]),2)
            second=drive_approved_goal(reactor=goals,bridge=bridge,goal_id="g",
                task_arguments={"one":{},"two":{}},worker_id="worker-fixture",
                max_actions=2)
            self.assertEqual(second["status"],"PASS")
            self.assertEqual(len(list_skill_executions(cfg)["executions"]),2)
            with self.assertRaises(DispatchGateError):
                bridge.execute_readonly(job_id="job3",run_id="run3",
                    skill_id=self.skill,arguments={},worker_id="worker-fixture",
                    resource_units=3)
            self.assertIsNone(queue.state("job3"))
        finally:
            goals.close()
            queue.close()

if __name__=="__main__":
    unittest.main()
