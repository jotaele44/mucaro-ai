"""Offline transaction journal replay and tamper regressions."""
import tempfile
import unittest
from pathlib import Path
from backend.app.auto_mode_journal import append_transition, replay
from backend.app.auto_mode_scheduler import AutoModeError

class TestAutoModeJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "journal.jsonl"

    def test_authorized_replay(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        append_transition(self.path, "job", "RUNNING", token="permit", operation_id="op1")
        self.assertEqual(replay(self.path, "job").state, "RUNNING")

    def test_idempotent_append(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        first = self.path.read_bytes()
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        self.assertEqual(self.path.read_bytes(), first)

    def test_unauthorized_run_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_TRANSITION"):
            append_transition(self.path, "job", "RUNNING", operation_id="op1")

    def test_tamper_denied(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        self.path.write_bytes(self.path.read_bytes().replace(b'AUTHORIZED', b'CANCELLED'))
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL"):
            replay(self.path, "job")

    def test_torn_write_denied(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL"):
            replay(self.path, "job")

    def test_transaction_mismatch_denied(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        with self.assertRaisesRegex(AutoModeError, "TRANSACTION_MISMATCH"):
            replay(self.path, "other")

    def test_recovery_and_rollback_state(self):
        append_transition(self.path, "job", "AUTHORIZED", token="permit")
        append_transition(self.path, "job", "RUNNING", token="permit", operation_id="op1")
        append_transition(self.path, "job", "FAILED")
        append_transition(self.path, "job", "ROLLED_BACK")
        self.assertEqual(replay(self.path, "job").history[-2:], ("FAILED", "ROLLED_BACK"))

    def test_symlink_denied(self):
        target = Path(self.tmp.name) / "target"
        target.write_text("protected")
        self.path.symlink_to(target)
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL_PATH"):
            append_transition(self.path, "job", "AUTHORIZED", token="permit")
        self.assertEqual(target.read_text(), "protected")

if __name__ == "__main__":
    unittest.main()
