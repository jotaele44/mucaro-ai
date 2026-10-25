"""V8 bounded, outbox-admitted real Mucaro skill execution.

The outbox and budget use separate durable SQLite databases; this controller
fails closed across ambiguous crash boundaries. It does NOT guarantee atomic
cross-database commit or exactly-once external side effects.
"""
from __future__ import annotations

from .aggregate_agent_budget import AggregateBudgetLedger, BudgetGateError
from .bounded_self_propulsion_pilot import execute_next_approved_task
from .durable_dispatch_outbox import DurableDispatchOutbox, OutboxGateError
from .goal_reactor import GoalReactor
from .governed_skill_bridge import GovernedSkillBridge
from .governed_learning_loop import digest

def _action_identity(*, goal_id, task_id, binding, policy_digest):
    return digest(["budgeted-execution", goal_id, task_id, binding, policy_digest])

def execute_one_durable_task(*, reactor: GoalReactor, bridge: GovernedSkillBridge,
                             ledger: AggregateBudgetLedger,
                             outbox: DurableDispatchOutbox,
                             goal_id: str, task_arguments: dict,
                             worker_id: str) -> dict:
    chosen=reactor.select(goal_id)
    if chosen.get("status")!="READY":
        return chosen
    task=chosen["candidate"]
    task_id=task["task_id"]
    if task["phase"]!="EXECUTE" or task["requested_capabilities"]!=["runtime.in_process"]:
        return {"status":"BLOCKED","reason":"UNAPPROVED_EXECUTION_CLASS","task_id":task_id}
    arguments=task_arguments.get(task_id)
    if not isinstance(arguments,dict):
        return {"status":"BLOCKED","reason":"MISSING_DECLARED_ARGUMENTS","task_id":task_id}
    skill=bridge._authorize(task["skill_id"])
    binding=digest({"skill_id":task["skill_id"],"skill_digest":skill.digest,
                    "arguments":arguments})
    if binding!=task["source_digest"]:
        return {"status":"BLOCKED","reason":"SOURCE_BINDING_MISMATCH","task_id":task_id}
    if reactor.summary(goal_id)["goal"][1]!=bridge.policy_digest:
        return {"status":"BLOCKED","reason":"GOAL_POLICY_MISMATCH","task_id":task_id}

    action=_action_identity(goal_id=goal_id,task_id=task_id,
                            binding=binding,policy_digest=bridge.policy_digest)
    reservation_id=digest(["budget-reservation",goal_id,task_id])
    intent_id=digest(["dispatch-intent",goal_id,task_id])
    idempotency_key=digest(["idempotency",action])
    units=task["cost"]

    # Reserve before registering work and before touching any executor.
    try:
        if not ledger.reserve(account_id=goal_id,reservation_id=reservation_id,
                              action_digest=action,units=units):
            return {"status":"BLOCKED","reason":"AGGREGATE_BUDGET_EXHAUSTED",
                    "task_id":task_id}
        outbox.declare(intent_id=intent_id,action_digest=action,
                       policy_digest=bridge.policy_digest,
                       reservation_id=reservation_id,
                       idempotency_key=idempotency_key)
        journal=outbox.reconcile(intent_id)
    except (BudgetGateError,OutboxGateError) as error:
        return {"status":"BLOCKED","reason":type(error).__name__+":"+str(error),
                "task_id":task_id}
    if (journal["action_digest"]!=action or journal["policy_digest"]!=bridge.policy_digest
        or journal["reservation_id"]!=reservation_id
        or journal["idempotency_key"]!=idempotency_key):
        return {"status":"BLOCKED","reason":"INTENT_IDENTITY_CONFLICT","task_id":task_id}

    completion=bridge.queue.verified_completion(task_id)
    if journal["status"]=="VERIFIED":
        if not completion or journal["result_digest"]!=completion["output_digest"]:
            return {"status":"BLOCKED","reason":"OUTBOX_WORKER_RECEIPT_MISMATCH",
                    "task_id":task_id}
    elif completion is None and journal["reconcile_count"]!=1:
        # Never infer nonexecution from an expired lease or a missing receipt.
        return {"status":"OPEN","reason":"AMBIGUOUS_DISPATCH_REQUIRES_RECONCILIATION",
                "task_id":task_id,"reservation_status":"RETAINED"}
    if completion is not None:
        expected_run=digest(["goal-task",goal_id,task_id])
        if (completion["run_id"]!=expected_run or completion["phase"]!="EXECUTE" or
            completion["input_digest"]!=binding or
            completion["policy_digest"]!=bridge.policy_digest):
            return {"status":"BLOCKED","reason":"COMPLETION_IDENTITY_CONFLICT","task_id":task_id}

    # On an initial authorized call, execute a reviewed health skill; on restart
    # recover an already-completed worker with NO duplicate invocation.
    outcome=execute_next_approved_task(
        reactor=reactor,bridge=bridge,goal_id=goal_id,
        task_arguments=task_arguments,worker_id=worker_id)
    if outcome.get("status")!="PASS" or outcome.get("task_id")!=task_id:
        return {**outcome,"reservation_status":"RETAINED"}

    # The underlying worker receipt already existed or has been committed.
    completed=bridge.queue.verified_completion(task_id)
    if not completed or completed["output_digest"]!=outcome["verified_output_digest"]:
        return {"status":"BLOCKED","reason":"WORKER_RECEIPT_MISSING_OR_CONFLICT",
                "task_id":task_id,"reservation_status":"RETAINED"}
    try:
        outbox.record_verified_effect(intent_id=intent_id,action_digest=action,
             result_digest=completed["output_digest"],idempotency_key=idempotency_key)
        ledger.settle(reservation_id,actual_units=units,action_digest=action)
    except (BudgetGateError,OutboxGateError) as error:
        return {"status":"BLOCKED","reason":"SETTLEMENT_RECONCILIATION:"+str(error),
                "task_id":task_id,"reservation_status":"RETAINED"}
    return {**outcome,"outbox_intent_id":intent_id,
            "budget_reservation_id":reservation_id,"budget_units_type":"DECLARED"}

def reconcile_completed_budgeted_task(*, reactor: GoalReactor,
                                      bridge: GovernedSkillBridge,
                                      ledger: AggregateBudgetLedger,
                                      outbox: DurableDispatchOutbox,
                                      goal_id: str, task_id: str) -> dict:
    """Reconcile an ALREADY completed task; NEVER invoke an executor."""
    row=reactor.db.execute(
        "SELECT state,source_digest,skill_id,cost,result_digest FROM tasks "
        "WHERE goal_id=? AND task_id=?",(goal_id,task_id)).fetchone()
    if not row:
        return {"status":"BLOCKED","reason":"TASK_NOT_FOUND"}
    state,binding,skill_id,units,goal_receipt=row
    if state!="PASS" or not goal_receipt:
        return {"status":"OPEN","reason":"GOAL_NOT_PROVEN_COMPLETE"}
    try:
        bridge._authorize(skill_id)
    except Exception:
        return {"status":"BLOCKED","reason":"SKILL_AUTHORITY_NOT_CURRENT"}
    if reactor.summary(goal_id)["goal"][1]!=bridge.policy_digest:
        return {"status":"BLOCKED","reason":"POLICY_IDENTITY_CONFLICT"}
    completion=bridge.queue.verified_completion(task_id)
    task_run_id=digest(["goal-task",goal_id,task_id])
    if (not completion or completion["run_id"]!=task_run_id or
        completion["phase"]!="EXECUTE" or completion["input_digest"]!=binding or
        completion["policy_digest"]!=bridge.policy_digest or
        completion["output_digest"]!=goal_receipt):
        return {"status":"BLOCKED","reason":"WORKER_OR_GOAL_EVIDENCE_CONFLICT"}
    action=_action_identity(goal_id=goal_id,task_id=task_id,
                            binding=binding,policy_digest=bridge.policy_digest)
    reservation_id=digest(["budget-reservation",goal_id,task_id])
    intent_id=digest(["dispatch-intent",goal_id,task_id])
    key=digest(["idempotency",action])
    try:
        journal=outbox.reconcile(intent_id)
        if (journal["action_digest"]!=action or
            journal["policy_digest"]!=bridge.policy_digest or
            journal["reservation_id"]!=reservation_id or
            journal["idempotency_key"]!=key):
            return {"status":"BLOCKED","reason":"OUTBOX_IDENTITY_CONFLICT"}
        if journal["status"]=="VERIFIED" and journal["result_digest"]!=goal_receipt:
            return {"status":"BLOCKED","reason":"OUTBOX_RESULT_CONTRADICTION"}
        outbox.record_verified_effect(intent_id=intent_id,action_digest=action,
             result_digest=goal_receipt,idempotency_key=key)
        ledger.settle(reservation_id,actual_units=units,action_digest=action)
    except (BudgetGateError,OutboxGateError) as error:
        return {"status":"BLOCKED","reason":type(error).__name__+":"+str(error)}
    return {"status":"PASS","task_id":task_id,"reconciled_without_execution":True,
            "result_digest":goal_receipt}
