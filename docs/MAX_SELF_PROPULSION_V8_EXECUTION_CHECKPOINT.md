# Mucaro MAX Self-Propulsion V8 — Real Skill Budget + Outbox Execution

Date: 2026-10-22
Branch: feature/agent-dispatch-v8
Parent branch: feature/agent-budget-v7
Pinned exercised source: 4ad71cf6d8ffc9a7ff818bc7e612786cacec60d9
Pinned codeload source ZIP bytes: 5,495,506
Pinned ZIP SHA-256: d167e66dbd08215abee8af58d72fe0fe136c779195bbfcd479f1be2e9e7e44c2

## Evidence
Exact pinned source archive extracted into isolated test runtime with
bounded member-count/size and path-traversal checks.
Selected unittest modules:
- backend.tests.test_aggregate_agent_budget
- backend.tests.test_budget_admitted_agent
- backend.tests.test_durable_dispatch_outbox
- backend.tests.test_outbox_budgeted_agent
Result 30/30 PASS; no failed cases (selected tests, not full CI).

## New source
- backend/app/outbox_budgeted_agent.py: goal approval/source/policy checks,
  aggregate budget reservation, durable outbox intent, signed queue dispatch
  through actual preexisting Mucaro health skill, worker completion proof,
  durable outbox result, budget settlement; reject ambiguous repeat dispatch.
- reconcile_completed_budgeted_task: strictly receipt-only reconciliation
  of previously PASS task, exact queue completion, outbox and reserved units;
  no skill execution or retroactive authority expansion.
- backend/tests/test_outbox_budgeted_agent.py: actual registered Mucaro health
  probe two-step PASS, duplicate suppression, outbox conflict, no-budget
  denial, ambiguous worker retention, crash gap, receipt-only recovery,
  incomplete-task denial.

## Fail-closed boundaries
- Budget ledger, Goal Reactor, queue and outbox use distinct SQLite
  transactions. Cross-database atomicity is NOT achieved.
- Outbox stores authenticated-to-local-contract receipts; the actual
  side effect's independent attestation and remote executor-enforced
  idempotency are NOT established. A genuine external action may have
  occurred after an ambiguous worker failure; no automatic replay.
- Settlement charges DECLARED units, not independently metered provider usage.
- Health probe only, no arbitrary skill execution, training, code promotion,
  deployment, API credentials, production principal or federation mutation.
- Source HEAD authentication and current main reconciliation OPEN.
- No inference that new runtime can sustain unattended operation.

## Next gates
1. Hosted current-head pytest/CI, build/security and rendered checks.
2. Promote the exact evaluated branch through independent source ancestry
   reconciliation; no branch protection bypass.
3. Independently authenticated runtime principal, immutable source
   manifests, key rotation and revocation, transactionally coordinated
   dispatch/lease budgets and external action effect receipts.
4. Verified independent evaluator with sealed holdouts, real-task measured
   improvement, reproducible optimization and approved rollback.
5. Extended supervised operation, TheHub bounded consumer, LOCKSTEP and
   final zero material residue scope certification.

V8_STATUS=BOUNDED_ISOLATED_PASS_FULL_CI_OPEN
GENERAL_AUTONOMY=OPEN
INDEPENDENT_LEARNING=OPEN
PRODUCTION_AUTHORITY=UNCHANGED
