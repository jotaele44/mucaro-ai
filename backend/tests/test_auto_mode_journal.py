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
        self.key = b'isolated-test-only-hmac-key-32-bytes-minimum'

    def append(self, path, tx, state, **kwargs):
        return append_transition(path, tx, state, key=self.key, **kwargs)

    def recover(self, path, tx):
        return replay(path, tx, key=self.key)

    def test_authorized_self.recover(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.append(self.path, "job", "RUNNING", token="permit", operation_id="op1")
        self.assertEqual(self.recover(self.path, "job").state, "RUNNING")

    def test_idempotent_append(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        first = self.path.read_bytes()
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.assertEqual(self.path.read_bytes(), first)

    def test_unauthorized_run_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_TRANSITION"):
            self.append(self.path, "job", "RUNNING", operation_id="op1")

    def test_tamper_denied(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.path.write_bytes(self.path.read_bytes().replace(b'AUTHORIZED', b'CANCELLED'))
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL"):
            self.recover(self.path, "job")

    def test_torn_write_denied(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL"):
            self.recover(self.path, "job")

    def test_transaction_mismatch_denied(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        with self.assertRaisesRegex(AutoModeError, "TRANSACTION_MISMATCH"):
            self.recover(self.path, "other")

    def test_recovery_and_rollback_state(self):
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.append(self.path, "job", "RUNNING", token="permit", operation_id="op1")
        self.append(self.path, "job", "FAILED")
        self.append(self.path, "job", "ROLLED_BACK")
        self.assertEqual(self.recover(self.path, "job").history[-2:], ("FAILED", "ROLLED_BACK"))

    def test_concurrent_writers_do_not_lose_records(self):
        from concurrent.futures import ThreadPoolExecutor
        self.append(self.path, "job", "AUTHORIZED", token="permit")
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(
                lambda _: self.append(self.path, "job", "AUTHORIZED", token="permit"),
                range(40),
            ))
        self.assertEqual(len(outcomes), 40)
        self.assertEqual(self.recover(self.path, "job").state, "AUTHORIZED")
        self.assertEqual(len(self.path.read_bytes().splitlines()), 1)

    def test_symlink_lockfile_denied(self):
        lock = Path(str(self.path) + ".lock")
        target = Path(self.tmp.name) / "protected"
        target.write_text("untouched")
        lock.symlink_to(target)
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL_PATH"):
            self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.assertEqual(target.read_text(), "untouched")

    def test_token_not_persisted_in_plaintext(self):
        self.append(self.path, "job", "AUTHORIZED", token="sensitive-authorization-secret")
        self.assertNotIn(b"sensitive-authorization-secret", self.path.read_bytes())
        self.assertEqual(self.recover(self.path, "job").state, "AUTHORIZED")

    def test_missing_key_denied(self):
        with self.assertRaisesRegex(AutoModeError, "JOURNAL_KEY_REQUIRED"):
            append_transition(self.path, "job", "AUTHORIZED", token="permit", key=b"short")

    def test_symlink_denied(self):
        target = Path(self.tmp.name) / "target"
        target.write_text("protected")
        self.path.symlink_to(target)
        with self.assertRaisesRegex(AutoModeError, "INVALID_JOURNAL_PATH"):
            self.append(self.path, "job", "AUTHORIZED", token="permit")
        self.assertEqual(target.read_text(), "protected")

if __name__ == "__main__":
    unittest.main()
