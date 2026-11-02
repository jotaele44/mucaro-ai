"""One bounded self-propulsion iteration using authoritative contracts.

The pilot processes one already-declared eligible task, without creating goals,
changing policies, promoting skills, or performing unbounded retries.
"""
from __future__ import annotations
from typing import Any
from .governed_learning_loop import digest
from .goal_reactor import GoalReactor, GoalReactorError
from .governed_skill_bridge import GovernedSkillBridge
from .scoped_agent_claim import ScopedClaimIssuer

def execute_next_approved_task(*, reactor: GoalReactor, bridge: GovernedSkillBridge,
        goal_id: str, task_arguments: dict[str,dict[str,Any]],
        worker_id: str) -> dict[str,Any]:
    selected=reactor.select(goal_id)
    if selected["status"]!="READY":
        return selected
    candidate=selected["candidate"]
    task_id=candidate["task_id"]
    arguments=task_arguments.get(task_id)
    if not isinstance(arguments,dict):
        return {"status":"BLOCKED","reason":"MISSING_DECLARED_ARGUMENTS","task_id":task_id}
    skill=bridge._authorize(candidate["skill_id"])
    binding=digest({"skill_id":candidate["skill_id"],
                    "skill_digest":skill.digest,"arguments":arguments})
    if candidate["source_digest"]!=binding:
        return {"status":"BLOCKED","reason":"SOURCE_BINDING_MISMATCH","task_id":task_id}
    if candidate["requested_capabilities"]!=["runtime.in_process"]:
        return {"status":"BLOCKED","reason":"CAPABILITY_BINDING_MISMATCH","task_id":task_id}
    # Read exact current goal authority before execution. No permission inherited
    # from a task label or the existence of a previously authorized skill.
    goal=reactor.summary(goal_id)
    if goal["goal"][1]!=bridge.policy_digest:
        return {"status":"BLOCKED","reason":"GOAL_POLICY_MISMATCH","task_id":task_id}
    task_run_id=digest(["goal-task",goal_id,task_id])
    completed=bridge.queue.verified_completion(task_id)
    if completed is not None:
        if (completed["run_id"]!=task_run_id or completed["phase"]!="EXECUTE" or
            completed["input_digest"]!=binding or
            completed["policy_digest"]!=bridge.policy_digest):
            return {"status":"BLOCKED","reason":"COMPLETION_IDENTITY_CONFLICT","task_id":task_id}
        reactor.record_result(task_id=task_id,state="PASS",
            result_digest=completed["output_digest"],
            policy_digest=bridge.policy_digest,cost_observed=candidate["cost"])
        return {"status":"PASS","task_id":task_id,
                "recovered_from_completed_worker":True,
                "verified_output_digest":completed["output_digest"],
                "next":reactor.select(goal_id)}
    opts={"resource_units":candidate["cost"]} if isinstance(getattr(bridge,"claim_issuer",None),ScopedClaimIssuer) else {}
    result=bridge.execute_readonly(job_id=task_id,run_id=task_run_id,
        skill_id=candidate["skill_id"],arguments=arguments,
        worker_id=worker_id,**opts)
    if result["status"]!="PASS":
        return {"status":result["status"],"reason":result.get("reason"),
                "task_id":task_id}
    # Reserve/charge declared ceiling rather than inferring real infrastructure
    # cost from an unverified model estimate.
    reactor.record_result(task_id=task_id,state="PASS",
        result_digest=result["output_digest"],
        policy_digest=bridge.policy_digest,cost_observed=candidate["cost"])
    return {"status":"PASS","task_id":task_id,
            "verified_output_digest":result["output_digest"],
            "next":reactor.select(goal_id)}

def drive_approved_goal(*, reactor: GoalReactor, bridge: GovernedSkillBridge,
        goal_id: str, task_arguments: dict[str,dict[str,Any]],
        worker_id: str, max_actions: int = 8) -> dict[str,Any]:
    """Run only a finite, previously approved DAG; never create new goals."""
    if isinstance(max_actions,bool) or not isinstance(max_actions,int) or not 1<=max_actions<=32:
        return {"status":"BLOCKED","reason":"INVALID_ACTION_BUDGET"}
    receipts=[]
    for _ in range(max_actions):
        outcome=execute_next_approved_task(reactor=reactor,bridge=bridge,
             goal_id=goal_id,task_arguments=task_arguments,worker_id=worker_id)
        if outcome.get("status")!="PASS":
            return {"status":outcome.get("status"),"reason":outcome.get("reason"),
                    "receipts":receipts}
        if "task_id" not in outcome:
            return {"status":"PASS","receipts":receipts,
                    "goal_receipt":reactor.summary(goal_id)}
        receipts.append({"task_id":outcome["task_id"],
                         "verified_output_digest":outcome["verified_output_digest"],
                         "recovered":outcome.get("recovered_from_completed_worker",False)})
        if outcome.get("next",{}).get("status")=="PASS":
            return {"status":"PASS","receipts":receipts,
                    "goal_receipt":reactor.summary(goal_id)}
    return {"status":"OPEN","reason":"MAX_ACTIONS_REACHED","receipts":receipts}
