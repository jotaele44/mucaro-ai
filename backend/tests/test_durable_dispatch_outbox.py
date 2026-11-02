import tempfile
import unittest
from pathlib import Path
from backend.app.durable_dispatch_outbox import DurableDispatchOutbox,OutboxGateError
from backend.app.governed_learning_loop import digest

class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"outbox.db"
        self.box=DurableDispatchOutbox(self.path)
        self.addCleanup(self.box.close)
        self.args={"intent_id":"intent","action_digest":digest("action"),
                   "policy_digest":digest("policy"),"reservation_id":"reservation",
                   "idempotency_key":digest(["action","idempotent"])}
        self.box.declare(**self.args)

    def test_pending_reconciliation_never_implies_effect(self):
        a=self.box.reconcile("intent")
        self.assertEqual(a["status"],"REQUIRES_EXTERNAL_RECONCILIATION")
        self.assertIsNone(a["result_digest"])

    def test_restart_preserves_intent_and_reconciliation_count(self):
        self.box.reconcile("intent")
        self.box.close()
        self.box=DurableDispatchOutbox(self.path)
        self.assertEqual(self.box.reconcile("intent")["reconcile_count"],2)

    def test_verified_effect_readable_after_restart(self):
        self.box.record_verified_effect(intent_id="intent",action_digest=self.args["action_digest"],
            idempotency_key=self.args["idempotency_key"],result_digest=digest("verified"))
        self.box.close()
        self.box=DurableDispatchOutbox(self.path)
        self.assertEqual(self.box.reconcile("intent")["status"],"VERIFIED")
        self.assertEqual(self.box.reconcile("intent")["result_digest"],digest("verified"))

    def test_duplicate_verified_effect_does_not_change_identity(self):
        values=dict(intent_id="intent",action_digest=self.args["action_digest"],
                    idempotency_key=self.args["idempotency_key"],result_digest=digest("receipt"))
        self.box.record_verified_effect(**values)
        self.box.record_verified_effect(**values)
        with self.assertRaises(OutboxGateError):
            self.box.record_verified_effect(**{**values,"result_digest":digest("different")})

    def test_intent_identity_conflict_denied(self):
        with self.assertRaises(OutboxGateError):
            self.box.declare(**{**self.args,"policy_digest":digest("altered")})

    def test_idempotency_key_collision_denied(self):
        with self.assertRaises(Exception):
            self.box.declare(**{**self.args,"intent_id":"different"})

    def test_forged_effect_denied(self):
        with self.assertRaises(OutboxGateError):
            self.box.record_verified_effect(intent_id="intent",action_digest=digest("forgery"),
                  idempotency_key=self.args["idempotency_key"],result_digest=digest("result"))
        self.assertEqual(self.box.reconcile("intent")["status"],"REQUIRES_EXTERNAL_RECONCILIATION")

if __name__=="__main__":
    unittest.main()
