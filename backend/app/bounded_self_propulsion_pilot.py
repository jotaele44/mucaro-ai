"""One bounded self-propulsion iteration using authoritative contracts.

The pilot processes one already-declared eligible task, without creating goals,
changing policies, promoting skills, or performing unbounded retries.
"""
from __future__ import annotations
from typing import Any
from .governed_learning_loop import digest
from .goal_reactor import GoalReactor, GoalReactorError
from .governed_skill_bridge import GovernedSkillBridge

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
    result=bridge.execute_readonly(job_id=task_id,run_id=goal_id,
        skill_id=candidate["skill_id"],arguments=arguments,
        worker_id=worker_id)
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
