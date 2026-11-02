"""V7 budget-admitted, bounded goal execution adapter.

The adapter is deliberately fail-closed, limited to the existing reviewed
health skill. It reserves declared units BEFORE any skill dispatch and never
releases an ambiguous reservation after possible external execution. Budget,
queue and reactor remain distinct SQLite databases; reconciliation is durable
but cross-database atomicity and external side effects are not certified.
"""
from __future__ import annotations
from .aggregate_agent_budget import AggregateBudgetLedger, BudgetGateError
from .bounded_self_propulsion_pilot import execute_next_approved_task
from .goal_reactor import GoalReactor
from .governed_skill_bridge import GovernedSkillBridge
from .governed_learning_loop import digest

def execute_budget_admitted_task(*, reactor: GoalReactor,
                                 bridge: GovernedSkillBridge,
                                 ledger: AggregateBudgetLedger,
                                 goal_id: str, task_arguments: dict,
                                 worker_id: str) -> dict:
    selection=reactor.select(goal_id)
    if selection.get("status")!="READY":
        return selection
    candidate=selection["candidate"]
    task=candidate["task_id"]
    if candidate["phase"]!="EXECUTE":
        return {"status":"BLOCKED","reason":"UNSUPPORTED_BUDGETED_PHASE","task_id":task}
    arguments=task_arguments.get(task)
    if not isinstance(arguments,dict):
        return {"status":"BLOCKED","reason":"MISSING_DECLARED_ARGUMENTS","task_id":task}
    skill=bridge._authorize(candidate["skill_id"])
    binding=digest({"skill_id":candidate["skill_id"],
                    "skill_digest":skill.digest,"arguments":arguments})
    if candidate["source_digest"]!=binding:
        return {"status":"BLOCKED","reason":"SOURCE_BINDING_MISMATCH","task_id":task}
    if reactor.summary(goal_id)["goal"][1]!=bridge.policy_digest:
        return {"status":"BLOCKED","reason":"GOAL_POLICY_MISMATCH","task_id":task}
    action=digest(["budgeted-execution",goal_id,task,binding,bridge.policy_digest])
    reservation=digest(["budget-reservation",goal_id,task])
    amount=candidate["cost"]
    try:
        if not ledger.reserve(account_id=goal_id,reservation_id=reservation,
                              action_digest=action,units=amount):
            return {"status":"BLOCKED","reason":"AGGREGATE_BUDGET_EXHAUSTED","task_id":task}
    except BudgetGateError as exc:
        return {"status":"BLOCKED","reason":str(exc),"task_id":task}
    # A previously completed worker can be reconciled without a second effect.
    # A stale RUNNING job is NOT released here; the lease/outbox authority
    # must independently establish whether the original external action ran.
    outcome=execute_next_approved_task(reactor=reactor,bridge=bridge,
                         goal_id=goal_id,task_arguments=task_arguments,
                         worker_id=worker_id)
    if outcome.get("status")!="PASS" or outcome.get("task_id")!=task:
        return {**outcome,"reservation_status":"RETAINED_FOR_RECONCILIATION"}
    # This is a declared-unit charge, not independently observed usage.
    ledger.settle(reservation,actual_units=amount,action_digest=action)
    return {**outcome,"budget_reservation":reservation,
            "budget_charge_type":"DECLARED_UNITS"}
