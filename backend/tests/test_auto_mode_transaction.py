"""Deterministic transaction tests; no command execution or network."""
import unittest
from backend.app.auto_mode_scheduler import AutoModeError
from backend.app.auto_mode_transaction import Transaction, transition

class TestAutoModeTransaction(unittest.TestCase):
    def test_authorize_and_run(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        b = transition(a, "RUNNING", token="approved", operation_id="op1")
        self.assertEqual(b.history, ("PENDING", "AUTHORIZED", "RUNNING"))

    def test_missing_authorization_denied(self):
        with self.assertRaisesRegex(AutoModeError, "AUTHORIZATION_REQUIRED"):
            transition(Transaction("job"), "AUTHORIZED")

    def test_unapproved_execution_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_TRANSITION"):
            transition(Transaction("job"), "RUNNING", operation_id="op1")

    def test_wrong_token_denied(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        with self.assertRaisesRegex(AutoModeError, "AUTHORIZATION_MISMATCH"):
            transition(a, "RUNNING", token="wrong", operation_id="op1")

    def test_missing_operation_id_denied(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        with self.assertRaisesRegex(AutoModeError, "OPERATION_ID_REQUIRED"):
            transition(a, "RUNNING", token="approved")

    def test_idempotent_retry(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        b = transition(a, "RUNNING", token="approved", operation_id="op1")
        self.assertEqual(transition(b, "RUNNING", operation_id="op1"), b)

    def test_idempotency_conflict_denied(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        b = transition(a, "RUNNING", token="approved", operation_id="op1")
        with self.assertRaisesRegex(AutoModeError, "IDEMPOTENCY_CONFLICT"):
            transition(b, "RUNNING", operation_id="op2")

    def test_cancel_before_execution(self):
        self.assertEqual(transition(Transaction("job"), "CANCELLED").state, "CANCELLED")

    def test_cancel_during_execution(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        b = transition(a, "RUNNING", token="approved", operation_id="op1")
        self.assertEqual(transition(b, "CANCELLED").state, "CANCELLED")

    def test_rollback_after_failure(self):
        a = transition(Transaction("job"), "AUTHORIZED", token="approved")
        b = transition(a, "RUNNING", token="approved", operation_id="op1")
        c = transition(b, "FAILED")
        self.assertEqual(transition(c, "ROLLED_BACK").state, "ROLLED_BACK")

    def test_terminal_cannot_restart(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_TRANSITION"):
            transition(transition(Transaction("job"), "CANCELLED"), "RUNNING")

    def test_history_tamper_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_TRANSACTION"):
            transition(Transaction("job", state="RUNNING"), "PASS")

if __name__ == "__main__":
    unittest.main()
