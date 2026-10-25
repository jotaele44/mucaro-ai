import unittest
from backend.app.sealed_evaluation_gate import (
    SealedHoldoutAuthority,VerifiedEvaluationGate,SealedEvaluationError
)

class SealedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.authority=SealedHoldoutAuthority(authority_id="fixture-curator",
             authority_key=b"curator-isolated-test-key-at-least-32-bytes")
        self.evaluator=VerifiedEvaluationGate(
             trusted_authority_id="fixture-curator",
             authority_key=b"curator-isolated-test-key-at-least-32-bytes",
             evaluator_id="fixture-evaluator",
             evaluator_key=b"evaluator-independent-test-key-at-least-32-bytes")
        self.truth={"a":"A","b":"B","c":"C","d":"D"}
        self.sealed=self.authority.seal(benchmark_id="holdout-v1",
             truth=self.truth,reserved_case_ids=("a","b","c","d"),revision="r1")
        self.base={"a":"A","b":"WRONG","c":"C","d":"WRONG"}
        self.candidate=dict(self.truth)
    def run_eval(self,**override):
        args=dict(sealed=self.sealed,proposal_id="candidate-1",
                  proposer_id="proposer-1",baseline=self.base,
                  candidate=self.candidate,training_case_ids=("training-1",))
        args.update(override)
        return self.evaluator.evaluate(**args)
    def test_sealed_candidate_improvement_is_signed_not_promoted(self):
        r=self.run_eval()
        self.assertEqual(r["evaluation"]["classification"],"PROMOTION_CANDIDATE")
        self.assertEqual(r["evaluation"]["accuracy_delta"],0.5)
        self.assertFalse(r["promoted"])
        self.assertTrue(self.evaluator.verify_receipt(r))
    def test_holdout_tampering_denied(self):
        altered={**self.sealed,"truth":{**self.truth,"a":"TAMPERED"}}
        with self.assertRaises(SealedEvaluationError):
            self.run_eval(sealed=altered)
    def test_seal_signature_tampering_denied(self):
        with self.assertRaises(SealedEvaluationError):
            self.run_eval(sealed={**self.sealed,"seal":"0"*64})
    def test_proposer_cannot_self_evaluate(self):
        for who in ("fixture-evaluator","fixture-curator"):
            with self.subTest(who=who),self.assertRaises(SealedEvaluationError):
                self.run_eval(proposer_id=who)
    def test_training_holdout_leakage_denied(self):
        with self.assertRaises(Exception):
            self.run_eval(training_case_ids=("a",))
    def test_regression_rejects_candidate(self):
        candidate={"a":"WRONG","b":"B","c":"C","d":"D"}
        r=self.run_eval(candidate=candidate)
        self.assertEqual(r["evaluation"]["classification"],"REJECTED")
    def test_signature_tampering_receipt_denied(self):
        r=self.run_eval()
        self.assertFalse(self.evaluator.verify_receipt({**r,"signature":"0"*64}))
        self.assertFalse(self.evaluator.verify_receipt({**r,"promoted":True}))
    def test_same_key_is_not_independent(self):
        key=b"not-independent-key-at-least-32-bytes-000"
        with self.assertRaises(SealedEvaluationError):
            VerifiedEvaluationGate(trusted_authority_id="a",authority_key=key,
                evaluator_id="b",evaluator_key=key)
    def test_incomplete_held_out_prediction_denied(self):
        with self.assertRaises(Exception):
            self.run_eval(candidate={"a":"A"})
    def test_invalid_membership_denied(self):
        with self.assertRaises(SealedEvaluationError):
            self.authority.seal(benchmark_id="x",truth=self.truth,
                                reserved_case_ids=("a","b","c"),revision="r2")
if __name__=="__main__":
    unittest.main()
