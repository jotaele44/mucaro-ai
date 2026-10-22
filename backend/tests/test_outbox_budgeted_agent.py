"""Real bounded health-skill execution with signed claims, budget and outbox."""
from __future__ import annotations
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.agent_claim_authority import ClaimIssuer,ClaimVerifier
from backend.app.aggregate_agent_budget import AggregateBudgetLedger
from backend.app.durable_dispatch_outbox import DurableDispatchOutbox
from backend.app.governed_learning_loop import digest
from backend.app.goal_reactor import GoalReactor
from backend.app.capability_policy import CapabilityPolicy
from backend.app.config import Settings
from backend.app.leased_agent_queue import LeasedAgentQueue
from backend.app.governed_skill_bridge import GovernedSkillBridge
from backend.app.outbox_budgeted_agent import execute_one_durable_task,reconcile_completed_budgeted_task
from backend.app.bounded_self_propulsion_pilot import execute_next_approved_task
from backend.app.skill_runtime import (
    SKILL_REGISTRY,mount_skill,activate_skill,list_skill_executions,
)

class OutboxBudgetedRealSkillTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        data=root/"data"
        cfg=Settings(project_root=root,data_dir=data,
            input_dir=data/"input",processed_dir=data/"processed",
            export_dir=data/"exports",index_dir=data/"indexes",
            sqlite_path=data/"processed"/"app.db",
            default_model_provider="mock",default_model_name="mock")
        cfg.ensure_dirs()
        skill="mucaro/intelligence-plane-health"
        policy=CapabilityPolicy.build(policy_id="outbox-test-readonly",
             allowed={"runtime.in_process"})
        reviewed={skill:SKILL_REGISTRY[skill].digest}
        key=secrets.token_bytes(32)
        issuer=ClaimIssuer("isolated-outbox",key,policy,reviewed)
        verifier=ClaimVerifier("isolated-outbox",key,policy,reviewed)
        queue=LeasedAgentQueue(root/"queue.sqlite",claim_verifier=verifier)
        goals=GoalReactor(root/"goals.sqlite")
        budget=AggregateBudgetLedger(root/"budget.sqlite")
        outbox=DurableDispatchOutbox(root/"outbox.sqlite")
        for item in (queue,goals,budget,outbox):
            self.addCleanup(item.close)
        mount_skill(skill,cfg=cfg,mounted_by="test")
        activate_skill(skill,cfg=cfg,policy=policy,activated_by="test")
        bridge=GovernedSkillBridge(queue=queue,cfg=cfg,policy=policy,
                                   claim_issuer=issuer)
        goals.add_goal(goal_id="approved",description="signed bounded health",
                       authority_digest=bridge.policy_digest,limit_cost=2)
        budget.open_account("approved",2)
        source=digest({"skill_id":skill,"skill_digest":SKILL_REGISTRY[skill].digest,
                       "arguments":{}})
        for task,parents in (("one",()),("two",("one",))):
            goals.add_task(task_id=task,goal_id="approved",phase="EXECUTE",
               dependencies=parents,source_digest=source,
               skill_id=skill,requested_capabilities=("runtime.in_process",),
               expected_value=1,confidence=1,relevance=1,cost=1,risk=0)
        self.bridge=bridge
        self.cfg=cfg
        self.goals=goals
        self.queue=queue
        self.budget=budget
        self.outbox=outbox
        self.arguments={"one":{},"two":{}}
        self.worker="isolated-worker"

    def run_once(self):
        return execute_one_durable_task(reactor=self.goals,bridge=self.bridge,
                ledger=self.budget,outbox=self.outbox,goal_id="approved",
                task_arguments=self.arguments,worker_id=self.worker)

    def test_two_real_tasks_have_verified_outbox_and_budget(self):
        first=self.run_once()
        second=self.run_once()
        again=self.run_once()
        self.assertEqual(first["status"],"PASS")
        self.assertEqual(second["status"],"PASS")
        self.assertEqual(again["status"],"PASS")
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),2)
        self.assertEqual(self.budget.summary("approved")["spent_units"],2)
        self.assertEqual(self.budget.summary("approved")["reserved_units"],0)
        for receipt in (first,second):
            journal=self.outbox.reconcile(receipt["outbox_intent_id"])
            self.assertEqual(journal["status"],"VERIFIED")
            self.assertEqual(journal["result_digest"],receipt["verified_output_digest"])

    def test_worker_success_then_crash_before_outbox_recovered(self):
        original=self.bridge
        first_task=self.goals.select("approved")["candidate"]
        action=digest(["budgeted-execution","approved","one",
                       first_task["source_digest"],original.policy_digest])
        reservation=digest(["budget-reservation","approved","one"])
        intent=digest(["dispatch-intent","approved","one"])
        key=digest(["idempotency",action])
        self.assertTrue(self.budget.reserve(account_id="approved",
            reservation_id=reservation,action_digest=action,units=1))
        self.outbox.declare(intent_id=intent,action_digest=action,
            policy_digest=original.policy_digest,reservation_id=reservation,
            idempotency_key=key)
        self.outbox.reconcile(intent) # registered intent before dispatch
        worker=execute_next_approved_task(reactor=self.goals,bridge=original,
            goal_id="approved",task_arguments=self.arguments,worker_id=self.worker)
        self.assertEqual(worker["status"],"PASS")
        # Goal reactor completed; emulate a crash before recording journal/settlement
        self.assertEqual(self.budget.summary("approved")["reserved_units"],1)
        self.assertEqual(self.outbox.reconcile(intent)["status"],
                         "REQUIRES_EXTERNAL_RECONCILIATION")
        # On a restarted scheduler, next eligible task is 'two' — original
        # reservation is intentionally retained pending explicit recovery.
        self.assertEqual(self.goals.select("approved")["candidate"]["task_id"],"two")
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),1)
        repaired=reconcile_completed_budgeted_task(
            reactor=self.goals,bridge=self.bridge,ledger=self.budget,
            outbox=self.outbox,goal_id="approved",task_id="one")
        self.assertEqual(repaired["status"],"PASS")
        self.assertTrue(repaired["reconciled_without_execution"])
        self.assertEqual(self.budget.summary("approved")["reserved_units"],0)
        self.assertEqual(self.budget.summary("approved")["spent_units"],1)
        self.assertEqual(self.outbox.reconcile(intent)["status"],"VERIFIED")
        again=reconcile_completed_budgeted_task(
            reactor=self.goals,bridge=self.bridge,ledger=self.budget,
            outbox=self.outbox,goal_id="approved",task_id="one")
        self.assertEqual(again["status"],"PASS")
        self.assertEqual(self.budget.summary("approved")["spent_units"],1)
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),1)

    def test_unfinished_task_cannot_be_settled_as_complete(self):
        outcome=reconcile_completed_budgeted_task(
            reactor=self.goals,bridge=self.bridge,ledger=self.budget,
            outbox=self.outbox,goal_id="approved",task_id="one")
        self.assertEqual(outcome["status"],"OPEN")
        self.assertEqual(self.budget.summary("approved")["spent_units"],0)

    def test_ambiguous_dispatch_does_not_retry(self):
        with patch("backend.app.outbox_budgeted_agent.execute_next_approved_task",
                   return_value={"status":"FAILED","reason":"WORKER_LOST"}) as run:
            initial=self.run_once()
            again=self.run_once()
        self.assertEqual(initial["reservation_status"],"RETAINED")
        self.assertEqual(again["status"],"OPEN")
        self.assertEqual(again["reason"],
                         "AMBIGUOUS_DISPATCH_REQUIRES_RECONCILIATION")
        self.assertEqual(run.call_count,1)
        self.assertEqual(self.budget.summary("approved")["reserved_units"],1)
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),0)

    def test_outbox_conflict_blocks_without_skill_execution(self):
        source=self.goals.select("approved")["candidate"]["source_digest"]
        action=digest(["budgeted-execution","approved","one",
                       source,self.bridge.policy_digest])
        intent=digest(["dispatch-intent","approved","one"])
        self.outbox.declare(intent_id=intent,action_digest=digest("wrong"),
            policy_digest=self.bridge.policy_digest,
            reservation_id=digest(["budget-reservation","approved","one"]),
            idempotency_key=digest(["idempotency",action]))
        outcome=self.run_once()
        self.assertEqual(outcome["status"],"BLOCKED")
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),0)
        self.assertEqual(self.budget.summary("approved")["reserved_units"],1)

    def test_missing_account_never_executes(self):
        self.budget.close()
        self.budget=AggregateBudgetLedger(Path(self.cfg.project_root)/"empty.sqlite")
        self.addCleanup(self.budget.close)
        output=self.run_once()
        self.assertEqual(output["status"],"BLOCKED")
        self.assertEqual(len(list_skill_executions(self.cfg)["executions"]),0)

if __name__=="__main__":
    unittest.main()
