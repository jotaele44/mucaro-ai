import unittest
from backend.app.governed_learning_loop import digest
from backend.app.observed_benchmark_runs import (
 RunBinding,observed_execution,verify_observed_receipt,compare_observed_runs,
 ObservedRunError)

class ObservedRunsTests(unittest.TestCase):
    def setUp(self):
        self.cases={"case-a":"red", "case-b":"blue", "case-c":"green","case-d":"gold"}
        self.truth={"case-a":"RED","case-b":"BLUE","case-c":"GREEN","case-d":"GOLD"}
        self.base_binding=RunBinding("a"*40,digest("baseline-implementation"),
                                    "fixture-runner",digest("fixture-runtime"))
        self.new_binding=RunBinding("a"*40,digest("candidate-implementation"),
                                   "fixture-runner",digest("fixture-runtime"))
    def baseline(self,x):
        return x.upper() if x in ("red","green") else "UNKNOWN"
    def candidate(self,x):
        return x.upper()
    def execute(self,handler,binding,name):
        return observed_execution(run_id=name,benchmark_id="synthetic-fixture",
                 cases=self.cases,binding=binding,handler=handler,
                 expected_handler_digest=binding.implementation_digest)
    def test_actual_callable_execution_produces_bound_observed_improvement(self):
        base=self.execute(self.baseline,self.base_binding,"base")
        new=self.execute(self.candidate,self.new_binding,"candidate")
        self.assertTrue(verify_observed_receipt(base,cases=self.cases,
                                                binding=self.base_binding))
        outcome=compare_observed_runs(baseline=base,candidate=new,
              sealed_holdout=self.truth,cases=self.cases,
              baseline_binding=self.base_binding,candidate_binding=self.new_binding)
        self.assertEqual(outcome["denominator"],4)
        self.assertEqual(outcome["baseline_pass"],2)
        self.assertEqual(outcome["candidate_pass"],4)
        self.assertEqual(outcome["accuracy_delta"],0.5)
        self.assertEqual(outcome["classification"],"FIXTURE_REVIEW_ONLY")
        self.assertFalse(outcome["promoted"])
        self.assertFalse(outcome["independence_confirmed"])
        self.assertEqual(base["cost_units_kind"],"UNMETERED")
    def test_observation_tampering_is_rejected(self):
        receipt=self.execute(self.candidate,self.new_binding,"candidate")
        receipt["observed_outputs"]["case-a"]="FORGED"
        self.assertFalse(verify_observed_receipt(receipt,cases=self.cases,
                                                binding=self.new_binding))
    def test_unbound_handler_is_denied_before_execution(self):
        calls=[]
        with self.assertRaises(ObservedRunError):
            observed_execution(run_id="x",benchmark_id="f",
               cases=self.cases,binding=self.new_binding,
               handler=lambda x:(calls.append(x) or x),
               expected_handler_digest=digest("wrong"))
        self.assertEqual(calls,[])
    def test_missing_input_refused(self):
        with self.assertRaises(ObservedRunError):
            observed_execution(run_id="x",benchmark_id="f",
                cases={"a":"A"},binding=self.new_binding,
                handler=self.candidate,
                expected_handler_digest=self.new_binding.implementation_digest)
    def test_handler_raises_is_not_silently_classified(self):
        with self.assertRaisesRegex(ObservedRunError,"HANDLER_EXECUTION_FAILED"):
            self.execute(lambda x: 1/0,self.new_binding,"crash")
    def test_non_string_handler_result_denied(self):
        with self.assertRaises(ObservedRunError):
            self.execute(lambda x:1,self.new_binding,"bad")
    def test_regression_fails_review(self):
        base=self.execute(self.candidate,self.base_binding,"base")
        new=self.execute(lambda x:"WRONG" if x=="red" else x.upper(),
                         self.new_binding,"candidate")
        result=compare_observed_runs(baseline=base,candidate=new,
                sealed_holdout=self.truth,cases=self.cases,
                baseline_binding=self.base_binding,candidate_binding=self.new_binding)
        self.assertEqual(result["classification"],"REJECTED")
        self.assertEqual(result["regressed_cases"],["case-a"])
    def test_unknown_ground_truth_denied(self):
        base=self.execute(self.baseline,self.base_binding,"base")
        new=self.execute(self.candidate,self.new_binding,"candidate")
        with self.assertRaises(ObservedRunError):
            compare_observed_runs(baseline=base,candidate=new,
              sealed_holdout={"different":"x"},cases=self.cases,
              baseline_binding=self.base_binding,candidate_binding=self.new_binding)
    def test_same_outputs_are_not_improvement(self):
        b=self.execute(self.candidate,self.base_binding,"base")
        n=self.execute(self.candidate,self.new_binding,"new")
        x=compare_observed_runs(baseline=b,candidate=n,sealed_holdout=self.truth,
             cases=self.cases,baseline_binding=self.base_binding,
             candidate_binding=self.new_binding)
        self.assertEqual(x["accuracy_delta"],0)
        self.assertEqual(x["classification"],"REJECTED")
    def test_source_binding_mismatch_denied(self):
        r=self.execute(self.candidate,self.new_binding,"new")
        other=RunBinding("b"*40,self.new_binding.implementation_digest,
                  "fixture-runner",digest("fixture-runtime"))
        self.assertFalse(verify_observed_receipt(r,cases=self.cases,binding=other))
    def test_wrong_runtime_identity_denied(self):
        r=self.execute(self.candidate,self.new_binding,"new")
        other=RunBinding("a"*40,self.new_binding.implementation_digest,
                  "different-runner",digest("fixture-runtime"))
        self.assertFalse(verify_observed_receipt(r,cases=self.cases,binding=other))
    def test_case_count_bound_and_bad_source(self):
        with self.assertRaises(ObservedRunError):
            RunBinding("not-real",digest("impl"),"principal",digest("runtime"))
        with self.assertRaises(ObservedRunError):
            observed_execution(run_id="x",benchmark_id="f",cases=self.cases,
              binding=self.new_binding,handler=self.candidate,
              expected_handler_digest=self.new_binding.implementation_digest,
              declared_max_cases=3)

if __name__=="__main__":
    unittest.main()
