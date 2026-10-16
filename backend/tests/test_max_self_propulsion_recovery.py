"""Crash-gap and finite multi-task reactor regression tests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.governed_learning_loop import digest
from backend.app.leased_agent_queue import LeasedAgentQueue, WorkerGateError
from backend.app.goal_reactor import GoalReactor
from backend.app.bounded_self_propulsion_pilot import (
    drive_approved_goal, execute_next_approved_task,
)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder=Path(self.temp.name)
        self.q=LeasedAgentQueue(folder/"queue.sqlite")
        self.g=GoalReactor(folder/"goal.sqlite")
        self.addCleanup(self.q.close)
        self.addCleanup(self.g.close)
        self.skill="mucaro/intelligence-plane-health"
        self.version="sha256:"+"f"*64
        self.policy=digest({"policy":"test"})
        self.g.add_goal(goal_id="g",description="approved limited fixture",
                        authority_digest=self.policy,limit_cost=3)
        self.calls=0
        self.args={}

    def add(self, task_id, dependencies=()):
        self.g.add_task(task_id=task_id,goal_id="g",phase="EXECUTE",
            dependencies=dependencies,source_digest=digest({
                "skill_id":self.skill,"skill_digest":self.version,"arguments":{}}),
            skill_id=self.skill,requested_capabilities=("runtime.in_process",),
            expected_value=1,confidence=1,relevance=1,cost=1,risk=0)
        self.args[task_id]={}

    def bridge(self, execute=True):
        skill=SimpleNamespace(digest=self.version)
        bridge=SimpleNamespace(queue=self.q,policy_digest=self.policy,
                               _authorize=lambda skill_id: skill)
        def run(*,job_id,run_id,skill_id,arguments,worker_id):
            self.calls+=1
            assert execute, "worker should not be invoked during recovery"
            inp=digest({"skill_id":skill_id,"skill_digest":self.version,"arguments":arguments})
            self.q.submit(job_id,run_id,"EXECUTE",inp,self.policy)
            claim=self.q.claim(job_id,worker_id,100,10,permitted=True)
            assert claim is not None
            output={"status":"PASS","result":self.calls}
            assert self.q.finish(job_id,worker_id,claim["fence"],output,101)
            return {"status":"PASS","output_digest":digest(output)}
        bridge.execute_readonly=run
        return bridge

    def test_crash_after_worker_completion_recovers_without_rerun(self):
        self.add("first")
        inp=digest({"skill_id":self.skill,"skill_digest":self.version,"arguments":{}})
        self.q.submit("first","g","EXECUTE",inp,self.policy)
        token=self.q.claim("first","worker-1",100,10,permitted=True)
        output={"status":"PASS","result":"already-completed"}
        self.assertTrue(self.q.finish("first","worker-1",token["fence"],output,101))
        result=execute_next_approved_task(reactor=self.g,bridge=self.bridge(execute=False),
            goal_id="g",task_arguments=self.args,worker_id="worker-2")
        self.assertTrue(result["recovered_from_completed_worker"])
        self.assertEqual(result["status"],"PASS")
        self.assertEqual(self.calls,0)
        self.assertEqual(self.g.select("g")["status"],"PASS")

    def test_completed_receipt_mismatch_blocks_recovery(self):
        self.add("first")
        self.q.submit("first","different-run","EXECUTE",digest("other"),self.policy)
        t=self.q.claim("first","worker",100,10,permitted=True)
        self.q.finish("first","worker",t["fence"],{"status":"PASS"},101)
        r=execute_next_approved_task(reactor=self.g,bridge=self.bridge(execute=False),
            goal_id="g",task_arguments=self.args,worker_id="worker")
        self.assertEqual(r["reason"],"COMPLETION_IDENTITY_CONFLICT")
        self.assertEqual(self.g.select("g")["status"],"READY")

    def test_receipt_corruption_fails_closed(self):
        self.add("first")
        inp=digest({"skill_id":self.skill,"skill_digest":self.version,"arguments":{}})
        self.q.submit("first","g","EXECUTE",inp,self.policy)
        t=self.q.claim("first","worker",100,10,permitted=True)
        self.q.finish("first","worker",t["fence"],{"status":"PASS"},101)
        self.q.db.execute("UPDATE receipts SET result_digest=? WHERE job_id=?",
                          (digest("altered"),"first"))
        with self.assertRaises(WorkerGateError):
            self.q.verified_completion("first")
        self.assertEqual(self.g.select("g")["status"],"READY")

    def test_two_step_dependency_self_propulsion(self):
        self.add("first")
        self.add("second",dependencies=("first",))
        r=drive_approved_goal(reactor=self.g,bridge=self.bridge(),
               goal_id="g",task_arguments=self.args,worker_id="w",max_actions=3)
        self.assertEqual(r["status"],"PASS")
        self.assertEqual([x["task_id"] for x in r["receipts"]],["first","second"])
        self.assertEqual(self.calls,2)
        self.assertEqual(self.g.summary("g")["goal"][3],2)
        again=drive_approved_goal(reactor=self.g,bridge=self.bridge(execute=False),
               goal_id="g",task_arguments=self.args,worker_id="w",max_actions=3)
        self.assertEqual(again["status"],"PASS")
        self.assertEqual(self.calls,2)

    def test_action_budget_forces_open_not_fabricated_pass(self):
        self.add("first")
        self.add("second",dependencies=("first",))
        r=drive_approved_goal(reactor=self.g,bridge=self.bridge(),
               goal_id="g",task_arguments=self.args,worker_id="w",max_actions=1)
        self.assertEqual(r["status"],"OPEN")
        self.assertEqual(r["reason"],"MAX_ACTIONS_REACHED")
        self.assertEqual(self.calls,1)
        next_run=drive_approved_goal(reactor=self.g,bridge=self.bridge(),
               goal_id="g",task_arguments=self.args,worker_id="w",max_actions=1)
        self.assertEqual(next_run["status"],"PASS")
        self.assertEqual(self.calls,2)

    def test_invalid_action_budget_rejected(self):
        self.add("first")
        for value in (0,False,33):
            r=drive_approved_goal(reactor=self.g,bridge=self.bridge(),
                goal_id="g",task_arguments=self.args,worker_id="w",max_actions=value)
            self.assertEqual(r["status"],"BLOCKED")
        self.assertEqual(self.calls,0)

if __name__=="__main__":
    unittest.main()
