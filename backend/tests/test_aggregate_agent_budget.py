"""Aggregate budget ledger: identity, exhaustion, restart and fail-closed tests."""
import tempfile
import unittest
from pathlib import Path
from backend.app.aggregate_agent_budget import AggregateBudgetLedger, BudgetGateError
from backend.app.governed_learning_loop import digest

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"budget.sqlite"
        self.ledger=AggregateBudgetLedger(self.path)
        self.addCleanup(self.ledger.close)
        self.ledger.open_account("approved-goal",5)
        self.source=digest({"source":"approved","action":"health"})

    def reserve(self, name="one", units=3, source=None):
        return self.ledger.reserve(account_id="approved-goal",reservation_id=name,
              action_digest=source or self.source,units=units)

    def test_reserve_limits_aggregate_across_jobs(self):
        self.assertTrue(self.reserve("a",3))
        self.assertFalse(self.reserve("b",3))
        self.assertTrue(self.reserve("b",2))
        self.assertEqual(self.ledger.summary("approved-goal"),{
            "limit_units":5,"reserved_units":5,"spent_units":0})

    def test_settle_releases_unused_capacity(self):
        self.assertTrue(self.reserve())
        self.assertTrue(self.ledger.settle("one",actual_units=1,action_digest=self.source))
        self.assertEqual(self.ledger.summary("approved-goal")["spent_units"],1)
        self.assertTrue(self.reserve("two",4))
        self.assertFalse(self.reserve("three",1))

    def test_restart_preserves_outstanding_reservation(self):
        self.reserve()
        self.ledger.close()
        self.ledger=AggregateBudgetLedger(self.path)
        self.assertFalse(self.reserve("other",3))
        self.assertTrue(self.reserve())
        self.assertEqual(self.ledger.summary("approved-goal")["reserved_units"],3)

    def test_duplicate_settlement_does_not_double_charge(self):
        self.reserve()
        self.ledger.settle("one",actual_units=2,action_digest=self.source)
        self.ledger.settle("one",actual_units=2,action_digest=self.source)
        self.assertEqual(self.ledger.summary("approved-goal")["spent_units"],2)

    def test_contradictory_settlement_denied(self):
        self.reserve()
        self.ledger.settle("one",actual_units=2,action_digest=self.source)
        with self.assertRaises(BudgetGateError):
            self.ledger.settle("one",actual_units=1,action_digest=self.source)

    def test_overcharge_denied(self):
        self.reserve()
        with self.assertRaises(BudgetGateError):
            self.ledger.settle("one",actual_units=4,action_digest=self.source)
        self.assertEqual(self.ledger.summary("approved-goal")["reserved_units"],3)

    def test_action_substitution_denied(self):
        self.reserve()
        with self.assertRaises(BudgetGateError):
            self.ledger.settle("one",actual_units=1,action_digest=digest("forged"))
        with self.assertRaises(BudgetGateError):
            self.reserve(source=digest("changed"))

    def test_unexecuted_release_and_double_release(self):
        self.reserve()
        self.assertTrue(self.ledger.release_unexecuted("one",action_digest=self.source))
        self.assertTrue(self.ledger.release_unexecuted("one",action_digest=self.source))
        self.assertEqual(self.ledger.summary("approved-goal")["reserved_units"],0)
        with self.assertRaises(BudgetGateError):
            self.ledger.settle("one",actual_units=0,action_digest=self.source)

    def test_invalid_units_and_boolean_denied(self):
        for amount in (-1,0,float("nan"),float("inf"),True):
            with self.subTest(amount=str(amount)),self.assertRaises(BudgetGateError):
                self.reserve(units=amount)

    def test_conflicting_budget_ceiling_denied(self):
        self.ledger.open_account("approved-goal",5)
        with self.assertRaises(BudgetGateError):
            self.ledger.open_account("approved-goal",100)

    def test_existing_spend_still_counts_after_restart(self):
        self.reserve()
        self.ledger.settle("one",actual_units=3,action_digest=self.source)
        self.ledger.close()
        self.ledger=AggregateBudgetLedger(self.path)
        self.assertFalse(self.reserve("four",3))
        self.assertTrue(self.reserve("four",2))

if __name__=="__main__":
    unittest.main()
