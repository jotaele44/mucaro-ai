import unittest
from backend.app.governed_learning_loop import digest
from backend.app.independent_evidence_admission import (
    IndependentAttestor,EvidenceAdmissionGate,EvidenceAdmissionError)

class EvidenceAdmissionTests(unittest.TestCase):
    def setUp(self):
        roles=("SOURCE_AUDITOR","HOLDOUT_CUSTODIAN","EVALUATION_AUDITOR")
        self.signers={r:IndependentAttestor(r.lower(),
            (r+"-independent-fixture-key-2026-xxxxxxxx").encode(),r) for r in roles}
        self.gate=EvidenceAdmissionGate(attestors=self.signers)
        h=lambda s:digest(s)
        self.source=dict(repository="jotaele44/mucaro-ai",source_head="a"*40,
            source_tree_digest=h("tree"),baseline_manifest_digest=h("baseline"),
            candidate_manifest_digest=h("candidate"))
        self.holdout=dict(benchmark_id="sealed-task-benchmark",revision="r1",
            sealed_truth_digest=h("truth"),holdout_manifest_digest=h("manifest"),
            excluded_training_digest=h("training-exclusions"))
        self.evaluation=dict(benchmark_id="sealed-task-benchmark",revision="r1",
            source_head="a"*40,baseline_manifest_digest=h("baseline"),
            candidate_manifest_digest=h("candidate"),
            holdout_manifest_digest=h("manifest"),
            evaluator_receipt_digest=h("eval"),observed_delta=0.25,
            regression_count=0,denominator=8)

    def attest(self):
        return dict(source_attestation=self.signers["SOURCE_AUDITOR"].attest(self.source),
            holdout_attestation=self.signers["HOLDOUT_CUSTODIAN"].attest(self.holdout),
            evaluation_attestation=self.signers["EVALUATION_AUDITOR"].attest(self.evaluation),
            proposal_id="proposal-fixture",proposer_id="proposer-fixture")

    def test_complete_distinct_signature_fixture_admits_review_only(self):
        r=self.gate.admit(**self.attest())
        self.assertEqual(r["classification"],"REVIEW_CANDIDATE")
        self.assertFalse(r["promoted"])

    def test_negative_delta_is_rejected(self):
        self.evaluation["observed_delta"]=-0.1
        self.assertEqual(self.gate.admit(**self.attest())["classification"],"REJECTED")

    def test_positive_delta_with_regressions_rejected(self):
        self.evaluation["regression_count"]=1
        self.assertEqual(self.gate.admit(**self.attest())["classification"],"REJECTED")

    def test_missing_external_attestation_fails(self):
        args=self.attest()
        args["source_attestation"]={}
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**args)

    def test_forged_evaluator_receipt_digest_denied(self):
        args=self.attest()
        args["evaluation_attestation"]["payload"]["evaluator_receipt_digest"]=digest("forged")
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**args)

    def test_mismatched_realization_denied(self):
        self.evaluation["candidate_manifest_digest"]=digest("other")
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**self.attest())

    def test_training_and_holdout_manifest_mismatch_denied(self):
        self.evaluation["holdout_manifest_digest"]=digest("other")
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**self.attest())

    def test_same_key_cannot_be_two_role_authority(self):
        a=dict(self.signers)
        src=a["SOURCE_AUDITOR"]
        a["HOLDOUT_CUSTODIAN"]=IndependentAttestor("new-custodian",src.key,
            "HOLDOUT_CUSTODIAN")
        with self.assertRaises(EvidenceAdmissionError):
            EvidenceAdmissionGate(attestors=a)

    def test_proposer_cannot_use_attestor_identity(self):
        a=self.attest()
        a["proposer_id"]="source_auditor"
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**a)

    def test_boolean_improvement_rejected(self):
        self.evaluation["observed_delta"]=True
        with self.assertRaises(EvidenceAdmissionError):
            self.gate.admit(**self.attest())

    def test_no_implicit_attested_key_creation(self):
        with self.assertRaises(EvidenceAdmissionError):
            IndependentAttestor("x",b"weak","SOURCE_AUDITOR")

if __name__=="__main__":
    unittest.main()
