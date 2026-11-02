"""Real registered Mucaro health-skill pilot under scoped signed leases.

Isolated filesystem, ephemeral signing key, no model provider, remote access,
production state, or independent evaluator claims. This is a bounded smoke.
"""
from __future__ import annotations
import secrets
import tempfile
import unittest
from pathlib import Path

from backend.app.agent_claim_authority import ClaimIssuer, ClaimVerifier
from backend.app.capability_policy import CapabilityPolicy
from backend.app.config import Settings
from backend.app.governed_learning_loop import digest
from backend.app.leased_agent_queue import LeasedAgentQueue
from backend.app.goal_reactor import GoalReactor
from backend.app.governed_skill_bridge import GovernedSkillBridge, DispatchGateError
from backend.app.bounded_self_propulsion_pilot import drive_approved_goal
from backend.app.skill_runtime import (
    SKILL_REGISTRY, mount_skill, activate_skill, list_skill_executions,
)

class RealHealthSkillPilotTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        data=root/"data"
        self.cfg=Settings(
            project_root=root,data_dir=data,input_dir=data/"input",
            processed_dir=data/"processed",export_dir=data/"exports",
            index_dir=data/"indexes",sqlite_path=data/"processed"/"app.sqlite",
            default_model_provider="mock",default_model_name="mock")
        self.cfg.ensure_dirs()
        self.skill_id="mucaro/intelligence-plane-health"
        self.skill=SKILL_REGISTRY[self.skill_id]
        self.policy=CapabilityPolicy.build(policy_id="v5-local-readonly",
                                           allowed={"runtime.in_process"})
        reviewed={self.skill_id:self.skill.digest}
        secret=secrets.token_bytes(32)
        self.issuer=ClaimIssuer("v5-local-fixture",secret,self.policy,reviewed)
        self.verifier=ClaimVerifier("v5-local-fixture",secret,self.policy,reviewed)
        self.queue_path=root/"jobs.sqlite"
        self.goal_path=root/"goals.sqlite"
        self.queue=LeasedAgentQueue(self.queue_path,claim_verifier=self.verifier)
        self.reactor=GoalReactor(self.goal_path)
        self.addCleanup(self.queue.close)
        self.addCleanup(self.reactor.close)
        mount_skill(self.skill_id,cfg=self.cfg,mounted_by="local-test")
        activate_skill(self.skill_id,cfg=self.cfg,policy=self.policy,
                       activated_by="local-test")
        self.bridge=GovernedSkillBridge(queue=self.queue,cfg=self.cfg,
                                        policy=self.policy,claim_issuer=self.issuer)

    def test_two_dependency_steps_real_skill_and_restart(self):
        self.reactor.add_goal(goal_id="g",description="bounded observed health",
                              authority_digest=self.bridge.policy_digest,limit_cost=2)
        bound=digest({"skill_id":self.skill_id,"skill_digest":self.skill.digest,
                      "arguments":{}})
        for task,parents in (("first",()),("second",("first",))):
            self.reactor.add_task(task_id=task,goal_id="g",phase="EXECUTE",
                dependencies=parents,source_digest=bound,skill_id=self.skill_id,
                requested_capabilities=("runtime.in_process",),expected_value=1,
                confidence=1,relevance=1,cost=1,risk=0)
        selected={"first":{},"second":{}}
        result=drive_approved_goal(reactor=self.reactor,bridge=self.bridge,
                                  goal_id="g",task_arguments=selected,
                                  worker_id="v5-fixture-worker",max_actions=2)
        self.assertEqual(result["status"],"PASS")
        self.assertEqual([r["task_id"] for r in result["receipts"]],
                         ["first","second"])
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),2)
        self.assertEqual(self.reactor.summary("g")["goal"][3],2)
        saved=self.reactor.summary("g")
        self.reactor.close()
        self.queue.close()
        self.reactor=GoalReactor(self.goal_path)
        self.queue=LeasedAgentQueue(self.queue_path,
                                   claim_verifier=self.verifier)
        bridge=GovernedSkillBridge(queue=self.queue,cfg=self.cfg,
                                   policy=self.policy,claim_issuer=self.issuer)
        repeat=drive_approved_goal(reactor=self.reactor,bridge=bridge,
                                  goal_id="g",task_arguments=selected,
                                  worker_id="v5-fixture-worker",max_actions=2)
        self.assertEqual(repeat["status"],"PASS")
        self.assertEqual(self.reactor.summary("g"),saved)
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),2)

    def test_policy_rotated_verifier_blocks_bridge(self):
        removed=CapabilityPolicy.build(policy_id="v5-revoked",allowed=())
        revoked_issuer=ClaimIssuer("v5-local-fixture",self.issuer.secret,
                                  removed,self.issuer.reviewed_skills)
        with self.assertRaises(DispatchGateError):
            GovernedSkillBridge(queue=self.queue,cfg=self.cfg,
                                policy=removed,claim_issuer=revoked_issuer)
        self.assertIsNone(self.queue.state("unseen"))

if __name__=="__main__":
    unittest.main()
