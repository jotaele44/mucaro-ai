"""V4 bounded goal autonomy, evaluator and skill bridge regression.

Standard-library unittest cases so isolated credential-free verification does not
require pytest. Full repository CI remains a distinct certification gate.
"""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.governed_learning_loop import digest
from backend.app.goal_reactor import GoalReactor, GoalReactorError
from backend.app.independent_evaluation_gate import assess_holdout, EvaluationGateError
from backend.app.leased_agent_queue import LeasedAgentQueue
from backend.app.capability_policy import CapabilityPolicy
from backend.app.agent_claim_authority import ClaimIssuer, ClaimVerifier


class ReactorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.r = GoalReactor(self.path / "goals.sqlite")
        self.addCleanup(self.r.close)
        self.authority = digest({"policy": "read-only"})
        self.r.add_goal(goal_id="g", description="bounded fixture pilot",
                        authority_digest=self.authority, limit_cost=4)

    def task(self, key="t", dependencies=(), cost=1, value=2, risk=0, source=None):
        self.r.add_task(task_id=key, goal_id="g", phase="EXECUTE",
            dependencies=tuple(dependencies), source_digest=source or digest(key),
            skill_id="mucaro/intelligence-plane-health",
            requested_capabilities=("runtime.in_process",),
            expected_value=value, confidence=1, relevance=1, cost=cost, risk=risk)

    def test_priority_prefers_value_and_preserves_tie_order(self):
        self.task("b", value=2)
        self.task("a", value=2)
        self.assertEqual(self.r.select("g")["candidate"]["task_id"], "a")
        self.task("z", value=20, cost=2)
        self.assertEqual(self.r.select("g")["candidate"]["task_id"], "z")

    def test_dependency_needs_explicit_pass(self):
        self.task("first")
        self.task("second", dependencies=("first",), value=30)
        self.assertEqual(self.r.select("g")["candidate"]["task_id"], "first")
        self.r.record_result(task_id="first", state="PASS", result_digest=digest("pass"),
                             policy_digest=self.authority, cost_observed=1)
        self.assertEqual(self.r.select("g")["candidate"]["task_id"], "second")

    def test_budget_blocks_without_phantom_spending(self):
        self.task("expensive", cost=10)
        self.assertEqual(self.r.select("g")["status"], "BLOCKED")
        self.assertEqual(self.r.summary("g")["goal"][3], 0)

    def test_final_pass_and_idempotent_result(self):
        self.task()
        args=dict(task_id="t",state="PASS",result_digest=digest("receipt"),
                  policy_digest=self.authority,cost_observed=1)
        self.r.record_result(**args)
        self.r.record_result(**args)
        self.assertEqual(self.r.select("g")["status"], "PASS")
        self.assertEqual(self.r.summary("g")["goal"][3], 1)

    def test_authority_mismatch_rejected(self):
        self.task()
        with self.assertRaises(GoalReactorError):
            self.r.record_result(task_id="t",state="PASS",result_digest=digest("x"),
                 policy_digest=digest("forged"),cost_observed=1)
        self.assertEqual(self.r.select("g")["status"], "READY")

    def test_rejects_synthesized_cross_goal_dependencies(self):
        self.task("first")
        self.r.add_goal(goal_id="other",description="another",
                        authority_digest=self.authority,limit_cost=1)
        with self.assertRaises(GoalReactorError):
            self.r.add_task(task_id="other-task",goal_id="other",phase="EXECUTE",
                dependencies=("first",),source_digest=digest("x"),skill_id="health",
                requested_capabilities=(),expected_value=1,confidence=1,relevance=1,
                cost=1,risk=0)

    def test_conflicting_task_identity_fails_closed(self):
        self.task()
        self.task()
        with self.assertRaises(GoalReactorError):
            self.task(value=9)

    def test_invalid_probability_nan_and_self_dependency(self):
        with self.assertRaises(GoalReactorError):
            self.task(cost=float("nan"))
        with self.assertRaises(GoalReactorError):
            self.task("same",dependencies=("same",))
        with self.assertRaises(GoalReactorError):
            self.r.add_task(task_id="bad",goal_id="g",phase="EXECUTE",
                dependencies=(),source_digest=digest("x"),skill_id="health",
                requested_capabilities=(),expected_value=2,confidence=1.2,
                relevance=1,cost=1,risk=0)

    def test_restart_preserves_complete_goal_manifest(self):
        self.task()
        baseline=self.r.summary("g")
        self.r.close()
        self.r=GoalReactor(self.path / "goals.sqlite")
        self.assertEqual(baseline,self.r.summary("g"))


class IndependentEvaluationTests(unittest.TestCase):
    truth={"c1":"A","c2":"B","c3":"C","c4":"D"}
    base={"c1":"A","c2":"B","c3":"wrong","c4":"wrong"}
    better={"c1":"A","c2":"B","c3":"C","c4":"D"}

    def eval(self, **overrides):
        args=dict(held_out_truth=self.truth,baseline_outputs=self.base,
            candidate_outputs=self.better,training_case_ids=("train-x","train-y"),
            proposal_id="candidate-1",proposer_id="agent-one",
            evaluator_id="evaluation-principal",independent_evaluator_authorized=True)
        args.update(overrides)
        return assess_holdout(**args)

    def test_measured_improvement_without_regression_is_candidate_only(self):
        r=self.eval()
        self.assertEqual(r["denominator"],4)
        self.assertEqual(r["baseline_pass"],2)
        self.assertEqual(r["candidate_pass"],4)
        self.assertEqual(r["classification"],"PROMOTION_CANDIDATE")
        self.assertFalse(r["promoted"])
        self.assertEqual(r,self.eval())

    def test_same_model_evaluator_denied(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(evaluator_id="agent-one")

    def test_missing_external_authorization_denied(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(independent_evaluator_authorized=False)

    def test_training_contamination_denied(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(training_case_ids=("c1",))

    def test_partial_holdout_denied(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(candidate_outputs={"c1":"A","c2":"B"})

    def test_negative_regression_blocks_promotion(self):
        candidate={"c1":"wrong","c2":"B","c3":"C","c4":"D"}
        r=self.eval(candidate_outputs=candidate)
        self.assertEqual(r["classification"],"REJECTED")
        self.assertEqual(r["regressed_cases"],["c1"])

    def test_tie_is_not_improvement(self):
        self.assertEqual(self.eval(candidate_outputs=self.base)["classification"],"REJECTED")

    def test_invalid_score_threshold_denied(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(improvement_floor=float("nan"))

    def test_case_count_gate(self):
        with self.assertRaises(EvaluationGateError):
            self.eval(held_out_truth={"c1":"A"})


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.policy=CapabilityPolicy.build(policy_id="fixture-readonly",
                            allowed={"runtime.in_process"})
        self.skill_id="mucaro/intelligence-plane-health"
        self.skill=SimpleNamespace(skill_id=self.skill_id, runtime="IN_PROCESS",
                                   capabilities=(),digest="sha256:"+"a"*64)
        key=b"v4-isolated-fixture-claim-key-at-least-32-bytes"
        reviewed={self.skill_id:self.skill.digest}
        self.issuer=ClaimIssuer("isolated-test",key,self.policy,reviewed)
        self.q=LeasedAgentQueue(self.path/"queue.sqlite",
            claim_verifier=ClaimVerifier("isolated-test",key,reviewed))
        self.addCleanup(self.q.close)

    def _execute(self, skill=None, arguments=None, goal=True):
        from backend.app import governed_skill_bridge as bridge_mod
        from backend.app.bounded_self_propulsion_pilot import execute_next_approved_task
        skill=skill or self.skill
        active={"skill_id":self.skill_id,"state":"ACTIVE","digest":skill.digest,
                "policy_decision":{"status":"AUTHORIZED","granted":["runtime.in_process"]}}
        executed={"status":"SKILL_EXECUTED",
                  "execution":{"status":"PASS","skill_digest":skill.digest,
                               "execution_id":"fixture-e1",
                               "runtime_identity":{"schema":"fixture","sha":"abc"}},
                  "result":{"test":"ok"}}
        with patch.dict(bridge_mod.SKILL_REGISTRY,{self.skill_id:skill}):
          with patch.object(bridge_mod,"list_skill_mounts",return_value={"mounts":[active]}):
           with patch.object(bridge_mod,"execute_skill",return_value=executed) as run:
            b=bridge_mod.GovernedSkillBridge(queue=self.q,cfg=SimpleNamespace(),
                                              policy=self.policy,claim_issuer=self.issuer)
            if not goal:
                result=b.execute_readonly(job_id="job",run_id="goal",
                    skill_id=self.skill_id,arguments=arguments or {},
                    worker_id="fixture-worker")
                return result,run
            reactor=GoalReactor(self.path/"goal.sqlite")
            try:
                reactor.add_goal(goal_id="goal",description="health fixture",
                    authority_digest=b.policy_digest,limit_cost=2)
                reactor.add_task(task_id="job",goal_id="goal",phase="EXECUTE",
                    dependencies=(),source_digest=digest({"skill_id":self.skill_id,
                            "skill_digest":skill.digest,"arguments":arguments or {}}),
                    skill_id=self.skill_id,requested_capabilities=("runtime.in_process",),
                    expected_value=1,confidence=1,relevance=1,cost=1,risk=0)
                result=execute_next_approved_task(reactor=reactor,bridge=b,goal_id="goal",
                    task_arguments={"job":arguments or {}},worker_id="fixture-worker")
                return result,run,reactor.summary("goal")
            finally:
                reactor.close()

    def test_bounded_self_propulsion_completes_one_task(self):
        r,call,receipt=self._execute()
        self.assertEqual(r["status"],"PASS")
        self.assertEqual(r["next"]["status"],"PASS")
        self.assertEqual(receipt["goal"][3],1)
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.q.state("job")["state"],"SUCCEEDED")

    def test_skill_executor_receipt_required(self):
        r,_,_=self._execute()
        self.assertTrue(r["verified_output_digest"].startswith("sha256:"))

    def test_remote_skill_never_reaches_handler(self):
        from backend.app import governed_skill_bridge as mod
        remote=SimpleNamespace(skill_id=self.skill_id,runtime="SERVICE",
                               capabilities=(),digest=self.skill.digest)
        with patch.dict(mod.SKILL_REGISTRY,{self.skill_id:remote}):
            bridge=mod.GovernedSkillBridge(queue=self.q,cfg=SimpleNamespace(),
                                            policy=self.policy,claim_issuer=self.issuer)
            with self.assertRaises(mod.DispatchGateError):
                bridge.execute_readonly(job_id="job",run_id="goal",skill_id=self.skill_id,
                        arguments={},worker_id="worker")
            self.assertIsNone(self.q.state("job"))

    def test_unauthorized_policy_never_dispatches(self):
        from backend.app import governed_skill_bridge as mod
        denied=CapabilityPolicy.build(policy_id="deny",allowed=())
        denied_issuer=ClaimIssuer("isolated-test",self.issuer.secret,
                                 denied,self.issuer.reviewed_skills)
        b=mod.GovernedSkillBridge(queue=self.q,cfg=SimpleNamespace(),
            policy=denied,claim_issuer=denied_issuer)
        with patch.dict(mod.SKILL_REGISTRY,{self.skill_id:self.skill}):
            with self.assertRaises(mod.DispatchGateError):
                b.execute_readonly(job_id="job",run_id="goal",
                    skill_id=self.skill_id,arguments={},worker_id="worker")
        self.assertIsNone(self.q.state("job"))


    def test_arbitrary_zero_capability_skill_is_not_eligible(self):
        from backend.app import governed_skill_bridge as mod
        skill_id="unreviewed/declared-zero-capabilities"
        unreviewed=SimpleNamespace(skill_id=skill_id,runtime="IN_PROCESS",
                                   capabilities=(),digest="sha256:"+"b"*64)
        with patch.dict(mod.SKILL_REGISTRY,{skill_id:unreviewed}):
            b=mod.GovernedSkillBridge(queue=self.q,cfg=SimpleNamespace(),
                                      policy=self.policy,claim_issuer=self.issuer)
            with self.assertRaises(mod.DispatchGateError):
                b.execute_readonly(job_id="job",run_id="goal",skill_id=skill_id,
                                   arguments={},worker_id="worker")
        self.assertIsNone(self.q.state("job"))


if __name__=="__main__":
    unittest.main()
