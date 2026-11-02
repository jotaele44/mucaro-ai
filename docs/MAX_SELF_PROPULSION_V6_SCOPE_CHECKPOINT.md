# Mucaro MAX Self-Propulsion V6 — Source-Bound Claim Scope (OPEN Verification)

Date: 2026-10-16
Branch: `feature/agent-authority-v6`
Pre-documentation source head: `e891093e194407682483e93afc531ef6eb34ebb9`
Parent V5 checkpoint: `c761adf44dd5ca4e8153c7efc6cab137825df8fc`

## Scope
V6 adds an opt-in stronger execution-claim contract without modifying the frozen
V5 branch, main, deployments, credentials or model weights.

Implemented and committed:
- `backend/app/scoped_agent_claim.py`: V6 HMAC-signed payload binds declared
  workspace digest, 40-hex source revision, principal, reviewed skill identity,
  IN_PROCESS runtime and positive finite per-dispatch resource ceiling.
- `ScopedClaimVerifier`: authenticates V6 envelope and re-checks configured
  workspace/source identity during each worker authorization decision. Rejects
  legacy V5 tokens missing the scope, as well as field changes and expired claims.
- `governed_skill_bridge.py`: confirms compatible trusted issuer/verifier
  V6 scope, checks workspace revision before dispatch, enforces task-resource
  ceiling for V6 issuance, and retains existing restricted pilot allowlist.
- `bounded_self_propulsion_pilot.py`: forwards the declared task resource
  amount to the V6 bridge; V5 behavior remains separately available.
- `backend/tests/test_scoped_agent_claim_v6.py`: positive and adversarial
  tests for full field binding, wrong workspace, source drift, legacy token
  rejection, invalid cost, queue refusal, plus two-task real health skill pilot.

## Provenance / identity caveats
A 40-character Git SHA supplied to a trusted initializer does not itself prove
that deployed bytes correspond to that SHA. The existing
`runtime_identity.current_git_head` resolver recognizes .git/build/env metadata;
a trusted admission procedure must validate precedence and repository identity.
V6 test fixtures use a synthetic local .git/HEAD marker, not production
independent source admission. Binding must include a signed immutable source
manifest and verified workspace identity before release.

The signed max_resource_units value is a **per-dispatch ceiling**. Aggregate
resource metering, reservations, settlement, cancellation, and independent
cost accounting remain OPEN. The principal is a configured trusted-issuer
attribute, not cryptographic human/service authentication. Existing in-flight
external effects are not undone by revocation.

## Verification disposition
- Source and new regression fixtures committed on a separate feature branch.
- **V6 tests NOT EXECUTED**, not PASS. Floot runtime refused further executions
  because daily build-action quota was reached.
- The separate isolated container could not retrieve codeload ZIP over DNS.
- No pinned V6 source archive SHA256 was acquired. Do not invent one.
- V5 prior checkpoint: 41 unittest + 13 queue checks PASS against its own
  previously pinned source. That evidence is retained without promotion to V6.
- Branch compare at pre-documentation HEAD: V6 7 commits ahead of V5, zero behind.
  V6 vs current main: DIVERGED, 51 ahead / 3 behind.
- No merge, push to main, production publication, training, or autonomous promotion.

## Next gate (highest priority)
1. Acquire exact immutable V6 source manifestation and verify local source HEAD
   with trusted repository/root/build identity.
2. Run V6 pytest/unittest including the actual registered-skill pilot, source-
   drift negatives, test ordering, restart/reconciliation, and full inherited
   tests. Observe and repair failures before declaring PASS.
3. Close low-level trusted authority initialization, key provisioning,
   revocation/rotation, principal authentication and aggregate budget ledger.
4. Implement transactional worker outbox, idempotent external actions, watchdog
   heartbeat/dead-letter, robust cancellation and crash-window reconciliation.
5. Introduce separately authenticated evaluator with sealed holdouts and
   real-world before/after gains; preserve no-promotion on missing evidence.
6. Rebase/adjudicate main's independent release/OCR evolution after both
   histories have source/CI receipts. Do not overwrite either.
7. Carry independent producer verification, TheHub contracts and LOCKSTEP
   only after each authority plane passes.
8. Certify MAX SELF-PROPULSION only with zero material unresolved residue.

V6_STATUS=OPEN_NOT_TESTED
MAIN_MUTATION=NO
PRODUCTION_MUTATION=NO
SELF_IMPROVEMENT_CERTIFICATION=OPEN
