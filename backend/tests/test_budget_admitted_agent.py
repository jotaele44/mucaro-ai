"""V7 fail-closed budget admission, recovery and exhaustion tests."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.aggregate_agent_budget import AggregateBudgetLedger
from backend.app.budget_admitted_agent import execute_budget_admitted_task
from backend.app.goal_reactor import GoalReactor
from backend.app.governed_learning_loop import digest

class BudgetAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.ledger=AggregateBudgetLedger(self.root/"budget.sqlite")
        self.reactor=GoalReactor(self.root/"goal.sqlite")
        self.addCleanup(self.ledger.close)
        self.addCleanup(self.reactor.close)
        self.policy=digest("fixture-policy")
        self.skill="mucaro/intelligence-plane-health"
        self.version="sha256:"+"a"*64
        self.binding=digest({"skill_id":self.skill,"skill_digest":self.version,"arguments":{}})
        self.reactor.add_goal(goal_id="g",description="bounded",authority_digest=self.policy,limit_cost=5)
        self.reactor.add_task(task_id="t",goal_id="g",phase="EXECUTE",
          dependencies=(),source_digest=self.binding,skill_id=self.skill,
          requested_capabilities=("runtime.in_process",),expected_value=1,
          confidence=1,relevance=1,cost=3,risk=0)
        self.bridge=SimpleNamespace(
          policy_digest=self.policy,
          _authorize=lambda _:SimpleNamespace(digest=self.version),
        )
        self.ledger.open_account("g",5)

    def call(self):
        return execute_budget_admitted_task(reactor=self.reactor,bridge=self.bridge,
            ledger=self.ledger,goal_id="g",task_arguments={"t":{}},worker_id="worker")

    def test_denied_budget_never_calls_executor(self):
        self.ledger.open_account("other",1)
        self.ledger.reserve(account_id="g",reservation_id="other",action_digest=digest("other"),units=4)
        with patch("backend.app.budget_admitted_agent.execute_next_approved_task") as run:
            result=self.call()
        self.assertEqual(result["reason"],"AGGREGATE_BUDGET_EXHAUSTED")
        run.assert_not_called()

    def test_success_settles_exactly_once(self):
        def completed(**_):
            self.reactor.record_result(task_id="t",state="PASS",
                result_digest=digest("output"),policy_digest=self.policy,cost_observed=3)
            return {"status":"PASS","task_id":"t","verified_output_digest":digest("output")}
        with patch("backend.app.budget_admitted_agent.execute_next_approved_task",side_effect=completed) as run:
            first=self.call()
            second=self.call()
        self.assertEqual(first["status"],"PASS")
        self.assertEqual(second["status"],"PASS")
        self.assertEqual(run.call_count,1)
        self.assertEqual(self.ledger.summary("g"),{"limit_units":5,"reserved_units":0,"spent_units":3})

    def test_ambiguous_failure_keeps_reservation(self):
        with patch("backend.app.budget_admitted_agent.execute_next_approved_task",
                   return_value={"status":"FAILED","reason":"WORKER_EXIT"}) as run:
            result=self.call()
        self.assertEqual(result["reservation_status"],"RETAINED_FOR_RECONCILIATION")
        self.assertEqual(self.ledger.summary("g")["reserved_units"],3)
        run.assert_called_once()

    def test_reopen_preserves_inflight_reservation(self):
        with patch("backend.app.budget_admitted_agent.execute_next_approved_task",
                   return_value={"status":"OPEN","reason":"LEASE_ACTIVE"}):
            self.call()
        self.ledger.close()
        self.ledger=AggregateBudgetLedger(self.root/"budget.sqlite")
        self.assertEqual(self.ledger.summary("g")["reserved_units"],3)

    def test_source_mismatch_refuses_admission(self):
        with patch.object(self.bridge,"_authorize",return_value=SimpleNamespace(digest=digest("different"))):
            with patch("backend.app.budget_admitted_agent.execute_next_approved_task") as run:
                result=self.call()
        self.assertEqual(result["reason"],"SOURCE_BINDING_MISMATCH")
        self.assertEqual(self.ledger.summary("g")["reserved_units"],0)
        run.assert_not_called()

    def test_missing_ledger_account_fails_closed(self):
        self.ledger.close()
        self.ledger=AggregateBudgetLedger(self.root/"new-budget.sqlite")
        with patch("backend.app.budget_admitted_agent.execute_next_approved_task") as run:
            result=self.call()
        self.assertEqual(result["reason"],"ACCOUNT_NOT_FOUND")
        run.assert_not_called()

if __name__=="__main__":
    unittest.main()
