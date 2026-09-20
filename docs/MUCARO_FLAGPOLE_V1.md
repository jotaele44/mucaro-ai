# MUCARO_FLAGPOLE_V1

Status: FROZEN DEVELOPMENT DENOMINATOR
Baseline main SHA: `f8fb2e9fbf6ed2675b3ff4f3eb51e858d394c586`
Frozen: 2026-09-20

## Goal

Mucaro is a provider-independent, persistent, provenance-aware agent system capable of planning, researching, retrieving, reasoning, acting, validating, and certifying complex multi-step work across local data, repositories, external tools, GIS, APIs, web resources, and connected services while preserving evidence, contradictions, permissions, checkpoints, and resumability throughout the task lifecycle.

## Binding invariants

1. Mucaro is not the model provider.
2. Capability is not established by tool presence alone.
3. Session, model context, and agent run are separate identities and lifecycles.
4. Planning never implies execution authorization.
5. Discovery never proves identity.
6. Context compaction never means deletion of canonical state.
7. Parallel execution may not synthesize conflicting state; contradictions survive until adjudicated.
8. Provider failure pauses affected work; it does not destroy session state.
9. Script success is not certification.
10. UNKNOWN remains UNKNOWN until evidence supports promotion.
11. Passed artifacts are reused unless a concrete falsifier reopens them.
12. Certification receipts bind to an exact source SHA and declared scope.

## Certification states

PASS | FAIL | OPEN | BLOCKED | PROVISIONAL | AUDIT_ONLY | NONCANONICAL | CANDIDATE_NOT_IDENTITY | UNRESOLVED | SUPERSEDED

## Required domains V00-V23

V00 flagpole freeze; V01 baseline inventory; V02 core state; V03 context engine; V04 model abstraction; V05 tool runtime; V06 permission engine; V07 agent runtime; V08 autopilot; V09 skills; V10 MCP/connectors; V11 subagents; V12 parallel/fleet; V13 Git/GitHub; V14 research engine; V15 local corpus/RAG; V16 GIS; V17 memory; V18 provenance; V19 validation; V20 certification; V21 UX surfaces; V22 packaging/distribution; V23 flagpole torture test.

## Flagpole torture test

Mucaro must be able, under explicit authority, to accept an objective equivalent to:

> Audit all seven federation repositories; identify every failing applicable CI gate; fix only independently evidenced defects; preserve unresolved contradictions; run applicable regression gates; open or update PRs where authorized; continue until the certification vector is exhausted.

PASS requires execution rather than instruction generation, exact-SHA provenance, resumability across interruption, preserved contradictions, permission enforcement, and zero unexplained required-domain residue inside the declared certification scope.

## Closure rule

MUCARO_FLAGPOLE_V1 may be certified PASS only when every required domain is PASS for the same declared release scope and exact SHA. Aggregate readiness percentages cannot conceal a required-domain FAIL, OPEN, BLOCKED, or UNRESOLVED.
