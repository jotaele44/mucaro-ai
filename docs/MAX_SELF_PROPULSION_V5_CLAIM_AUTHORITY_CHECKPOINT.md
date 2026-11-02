# Mucaro MAX Self-Propulsion V5 — Signed Claim Authority Checkpoint

Date: 2026-10-16
Scope: isolated governed execution foundation, NOT general autonomous agent certification
Branch: `feature/governed-learning-loop-20261016`
Disposition: **SIGNED CLAIM AUTHORITY BOUNDED PASS / PRODUCTION OPEN**

## Preserved foundation
V1 evidence-driven improvement ledger, V2 durable phases, V3 leased queue,
V4 goal reactor/evaluation gate, V4.1 crash-gap reconciliation, and V4.2
reviewed-skill-only real health pilot remain preserved on the same isolated
feature branch. `main` was not merged or mutated.

## Exercised source manifestation
- Pinned source commit: `193aafeffceb50247ebc89a1f9b7993edd464887`
- GitHub ZIP manifestation size: 5,462,729 bytes.
- ZIP SHA-256: `66a3072d13766f571a13422422c24fb7613532a29b3f14be6cc7c815cda7b5ee`.
- Archive member/path/size safety validated before clean extraction.
- This digest binds the retrieved ZIP bytes, NOT a Git tree or signed release.

## New source
`backend/app/agent_claim_authority.py`:
- Trusted runtime must provision a distinct HMAC-SHA-256 signing key at least
  32 bytes long, without committing secrets.
- Claim issuer evaluates `CapabilityPolicy` and limits issuance to pinned
  reviewed skill identities and declared `runtime.in_process` capability.
- Signed claims bind issuer, job ID, run ID, phase, input digest, policy digest,
  worker ID, reviewed skill ID/digest, grant issue time and expiration.
- Verifier checks signature with constant-time comparison, scope, exact stored
  job identity, permitted skill digest, trusted active policy, and lease horizon.
- Frozen issuer/verifier reviewed-skill maps prevent mutation of the supplied
  map after instantiation.
- A policy configuration change requires the verifier to be reconfigured with
  the new trusted policy; stale-policy claims fail verification.

`backend/app/leased_agent_queue.py`:
- Replaces caller-controlled `claim(..., permitted=True)` with
  `claim(..., authorization=<signed claim>)` and a trusted verifier.
- Default construction without a verifier fails closed for claims.
- Queue itself validates authorization within an immediate SQLite transaction.
- Claim and completion timestamps derive from a queue-owned clock; the
  production initializer must control its clock and verifier configuration.
- Existing fencing, cancellation, attempts, idempotency keys, durable receipts,
  and crash-gap reconciliation retained.

`backend/app/governed_skill_bridge.py`:
- Requires an explicit issuer and compatible policy-bound verifier.
- Continues enforcing the *specific* built-in health-skill allowlist,
  activated skill digest, local runtime and no-declared-capabilities contract.
- Does not grant arbitrary tool execution, remote workers, training or promotion.

## Pinned execution
- `python3 -m unittest -q backend.tests.test_agent_claim_authority
  backend.tests.test_max_self_propulsion_v4
  backend.tests.test_max_self_propulsion_recovery
  backend.tests.test_max_self_propulsion_v5_live_skill`
  -> **41/41 PASS**, zero failures.
- `backend/tests/test_leased_agent_queue.py` executed via an isolated minimal
  `pytest.raises`-compatible harness because this runtime does not have
  pytest installed -> **13/13 PASS**. This harness is NOT the project's
  official pytest runner.
- Aggregate at one pinned source manifestation: **54 selected checks PASS**.
- Committed real `mucaro/intelligence-plane-health` integration fixture:
  two explicitly approved dependent task executions, no extra executions
  on repeated run, durable goal/queue state preserved on reopen.
- Strong negatives: unsigned/forged/expired/wrong-owner/wrong-job grants;
  wrong signing key; stale policy; mutable skill allowlist; unsupported
  destination; unconfigured verifier; old caller boolean.
- Test key is generated ephemerally or isolated in test code. No production
  secret is stored in the repository.

## Historical test observations (preserve, do not erase)
- The first V5 test migration exposed remaining legacy `permitted=True`
  and caller-supplied finish-time arguments. Those fixtures were repaired.
- Pinned retest at `193aafe...` supersedes intermediate local failures,
  which remain historical observations rather than a claim they never occurred.

## Strict limits
- This does NOT authenticate an independent issuer principal across operating
  systems or independent services. Secret distribution, secure storage,
  process isolation, revocation delivery and rotation remain OPEN.
- A compromised trusted runtime or stolen signing key can issue valid grants.
- Existing signed leases cannot retroactively undo already executed external
  actions; idempotent external receipts and compensation remain OPEN.
- The queue constructor's trusted clock and verification authority must be
  provisioned by a protected supervisor before production deployment.
- Source HEAD/workspace principal are not yet included in the signed claim;
  exact output receipt binds skill digest/runtime evidence only.
- No sealed independent evaluator process; V4 evaluation remains synthetic
  holdout data, with caller-declared evaluator identity.
- No RL model training, autonomous promotion, ongoing monitoring, real external
  tool execution, production deployment, TheHub federation transfer or LOCKSTEP.
- Full repository pytest, type/build/security checks and host CI remain OPEN;
  selected tests do not imply complete CI.

## Next governed vector
1. Runtime identity: pin workspace/source revision, exact external action,
   runtime class, principal and budget in signed grants; update both worker
   and actual tool adapters to reject stale or mismatched bindings.
2. Configuration: secret escrow/rotation/revocation; signed authority origin,
   protected clock, replay-window control, and audit of issuer keys.
3. Transactional outbox: durable dispatch intent, per-external-action
   idempotency, heartbeat, watchdog, crash/lease recapture and compensation.
4. Independent evaluator: authenticated evaluator principal, sealed holdout,
   leakage/reward gaming tests, real-task before/after effectiveness.
5. Learning: versioned task experience, classification, eligible training data,
   reviewed promotion and independently measured rollback.
6. Fully approved multi-agent task graph, source-backed research/coding skills,
   partial offline autonomy, budgets and supervisor stop rules.
7. Complete pinned-source repository CI/security, cross-platform and device
   gates, followed by TheHub producer-consumer verification and LOCKSTEP.
8. Issue MAX SELF-PROPULSION CERTIFIED only when declared current scope is
   zero unresolved material FAIL/OPEN/BLOCKED/UNRESOLVED.

No fabricated progress. No unauthorized authority expansion.
No promotion without demonstrated improvement.
