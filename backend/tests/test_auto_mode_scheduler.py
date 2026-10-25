"""Auto Mode scheduler negative and deterministic tests."""
import unittest
from backend.app.auto_mode_scheduler import AutoModeError, Vector, schedule

class TestAutoModeScheduler(unittest.TestCase):
    def test_dependency_wave(self):
        v = (Vector("audit"), Vector("test", ("audit",)))
        self.assertEqual(schedule(v, {}).ready, ("audit",))
        self.assertEqual(schedule(v, {"audit":"PASS"}).ready, ("test",))
    def test_parallel_isolation(self):
        v = (Vector("a", isolation_key="repo"), Vector("b", isolation_key="repo"), Vector("c", isolation_key="other"))
        p = schedule(v, {})
        self.assertEqual(p.ready, ("a", "c"))
        self.assertEqual(p.blocked, ("b",))
    def test_authorization_fail_closed(self):
        v = (Vector("deploy", requires_authorization=True),)
        self.assertEqual(schedule(v, {}).blocked, ("deploy",))
        self.assertEqual(schedule(v, {}, frozenset({"deploy"})).ready, ("deploy",))
    def test_failure_blocks_downstream(self):
        v = (Vector("test"), Vector("certify", ("test",)))
        self.assertEqual(schedule(v, {"test":"FAILED"}).blocked, ("certify",))
    def test_cycle_denied(self):
        with self.assertRaisesRegex(AutoModeError, "DEPENDENCY_CYCLE"):
            schedule((Vector("a", ("b",)), Vector("b", ("a",))), {})
    def test_unknown_dependency_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_DEPENDENCY"):
            schedule((Vector("a", ("missing",)),), {})
    def test_duplicate_denied(self):
        with self.assertRaisesRegex(AutoModeError, "DUPLICATE_OR_EMPTY_VECTOR"):
            schedule((Vector("a"), Vector("a")), {})
    def test_invalid_state_denied(self):
        with self.assertRaisesRegex(AutoModeError, "INVALID_STATE"):
            schedule((Vector("a"),), {"a":"SUCCESS"})
    def test_unknown_approval_denied(self):
        with self.assertRaisesRegex(AutoModeError, "UNKNOWN_AUTHORIZATION"):
            schedule((Vector("a"),), {}, frozenset({"missing"}))
    def test_terminal_only_on_full_pass(self):
        v = (Vector("a"), Vector("b", ("a",)))
        self.assertFalse(schedule(v, {"a":"PASS"}).terminal)
        self.assertTrue(schedule(v, {"a":"PASS","b":"PASS"}).terminal)

if __name__ == "__main__": unittest.main()
