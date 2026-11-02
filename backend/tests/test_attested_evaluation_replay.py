"""V11 reproducible arithmetic over sealed, signed synthetic evaluation inputs."""
import unittest
from backend.app.governed_learning_loop import digest
from backend.app.sealed_evaluation_gate import SealedHoldoutAuthority,VerifiedEvaluationGate
from backend.app.independent_evidence_admission import IndependentAttestor,EvidenceAdmissionGate
from backend.app.attested_evaluation_replay import (
    replay_attested_evaluation,benchmark_manifest,ReplayGateError)

class ReplayTests(unittest.TestCase):
    def setUp(self):
        roles=("SOURCE_AUDITOR","HOLDOUT_CUSTODIAN","EVALUATION_AUDITOR")
        self.signers={r:IndependentAttestor("fixture-"+r.lower(),
            (r+"-fixture-key-independent-at-least-32-bytes").encode(),r)
            for r in roles}
        self.admission=EvidenceAdmissionGate(attestors=self.signers)
        self.sealer=SealedHoldoutAuthority(authority_id="sealed-curator",
             authority_key=b"holdout-curator-key-independent-at-least-32")
        self.evaluator=VerifiedEvaluationGate(
            trusted_authority_id="sealed-curator",
            authority_key=b"holdout-curator-key-independent-at-least-32",
            evaluator_id="independent-evaluator",
            evaluator_key=b"isolated-evaluator-key-independent-at-least-32")
        self.truth={"a":"A","b":"B","c":"C","d":"D"}
        self.sealed=self.sealer.seal(benchmark_id="synthetic",revision="r1",
             truth=self.truth,reserved_case_ids=tuple(self.truth))
        self.baseline={"a":"A","b":"incorrect","c":"C","d":"incorrect"}
        self.candidate=self.truth.copy()
        self.training=("training-case-1",)
        self.proposal="p"
        self.proposer="candidate-author"
        self.set_attestations()

    def set_attestations(self):
        manifest=benchmark_manifest(self.sealed)
        evaluated=self.evaluator.evaluate(sealed=self.sealed,
             proposal_id=self.proposal,proposer_id=self.proposer,
             baseline=self.baseline,candidate=self.candidate,
             training_case_ids=self.training)
        ev=evaluated["evaluation"]
        source=dict(repository="jotaele44/mucaro-ai",source_head="a"*40,
            source_tree_digest=digest("fixture-tree"),
            baseline_manifest_digest=digest(self.baseline),
            candidate_manifest_digest=digest(self.candidate))
        holdout=dict(benchmark_id="synthetic",revision="r1",
            sealed_truth_digest=digest(self.truth),
            holdout_manifest_digest=digest(manifest),
            excluded_training_digest=digest(sorted(self.training)))
        result=dict(benchmark_id="synthetic",revision="r1",source_head="a"*40,
            baseline_manifest_digest=source["baseline_manifest_digest"],
            candidate_manifest_digest=source["candidate_manifest_digest"],
            holdout_manifest_digest=holdout["holdout_manifest_digest"],
            evaluator_receipt_digest=digest(evaluated),
            observed_delta=ev["accuracy_delta"],
            regression_count=len(ev["regressed_cases"]),
            denominator=ev["denominator"])
        self.source=self.signers["SOURCE_AUDITOR"].attest(source)
        self.holdout=self.signers["HOLDOUT_CUSTODIAN"].attest(holdout)
        self.result=self.signers["EVALUATION_AUDITOR"].attest(result)

    def check(self,**kw):
        args=dict(admission=self.admission,evaluator=self.evaluator,
            sealed=self.sealed,source_attestation=self.source,
            holdout_attestation=self.holdout,evaluation_attestation=self.result,
            baseline=self.baseline,candidate=self.candidate,
            training_case_ids=self.training,proposal_id=self.proposal,
            proposer_id=self.proposer)
        args.update(kw)
        return replay_attested_evaluation(**args)

    def test_recomputed_positive_improvement_review_only(self):
        report=self.check()
        self.assertEqual(report["classification"],"REVIEW_CANDIDATE")
        self.assertEqual(report["denominator"],4)
        self.assertEqual(report["accuracy_delta"],0.5)
        self.assertFalse(report["promoted"])

    def test_changed_candidate_without_manifest_rejected(self):
        changed=dict(self.candidate,b="incorrect")
        with self.assertRaises(ReplayGateError):
            self.check(candidate=changed)

    def test_forged_reported_delta_with_valid_signer_rejected(self):
        tamper=dict(self.result["payload"],observed_delta=0.9)
        self.result=self.signers["EVALUATION_AUDITOR"].attest(tamper)
        with self.assertRaises(ReplayGateError):
            self.check()

    def test_recomputed_regression_rejects_candidate(self):
        self.candidate={"a":"incorrect","b":"B","c":"C","d":"D"}
        self.set_attestations()
        self.assertEqual(self.check()["classification"],"REJECTED")

    def test_regression_count_lie_denied_even_if_signed(self):
        self.candidate={"a":"incorrect","b":"B","c":"C","d":"D"}
        self.set_attestations()
        self.result=self.signers["EVALUATION_AUDITOR"].attest(
            dict(self.result["payload"],regression_count=0))
        with self.assertRaises(ReplayGateError):
            self.check()

    def test_holdout_mutation_denied(self):
        sealed=dict(self.sealed,truth=dict(self.truth,a="changed"))
        with self.assertRaises(ReplayGateError):
            self.check(sealed=sealed)

    def test_training_leakage_denied(self):
        with self.assertRaises(ReplayGateError):
            self.check(training_case_ids=("a",))

    def test_proposer_cannot_impersonate_evaluator(self):
        with self.assertRaises(ReplayGateError):
            self.check(proposer_id="independent-evaluator")

    def test_missing_case_denied(self):
        with self.assertRaises(ReplayGateError):
            self.check(candidate={"a":"A"})

    def test_signed_evaluator_receipt_digest_conflict_denied(self):
        self.result=self.signers["EVALUATION_AUDITOR"].attest(
            dict(self.result["payload"],evaluator_receipt_digest=digest("different")))
        with self.assertRaises(ReplayGateError):
            self.check()

    def test_training_exclusion_digest_conflict_denied(self):
        self.holdout=self.signers["HOLDOUT_CUSTODIAN"].attest(
            dict(self.holdout["payload"],excluded_training_digest=digest("different")))
        with self.assertRaises(ReplayGateError):
            self.check()

    def test_mismatched_source_revision_denied(self):
        self.result=self.signers["EVALUATION_AUDITOR"].attest(
            dict(self.result["payload"],source_head="b"*40))
        with self.assertRaises(ReplayGateError):
            self.check()

if __name__=="__main__":
    unittest.main()
