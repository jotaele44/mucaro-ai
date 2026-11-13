# Standalone independence upgrade — execution contract
Status: PROVISIONAL / AUDIT_ONLY
Base commit: e88038cd6f79ec43f1bce519e3a4ed16ad0e6997
Date: 2026-10-20

## Identity and scope
- Preserve original source commit, branches, historical certification, schema, provider routing and documented runtime independence baseline.
- No declaration of feature completion from a plan, test script success, historical passing status, or executable presence alone.
- Source setup offline-after-warm-cache is NOT equivalent to clean-host offline install.
- User permission to execute remote model/fetch operations is NOT general network authorization.
- The model-free path must remain usable when no local model, remote API, credentials, or network are present.
- Existing features are not to be reimplemented until an audit identifies their canonical owner.

## Ordered acceptance gates
| ID | Gate | Positive control | Negative/falsification control | State |
|---|---|---|---|---|
| IND-01 | Inventory | lockfiles, manifests, frontend/backend imports, subprocess paths, installers, env variables and network hosts enumerated | static inventory omissions or dynamic outbound traffic detected | OPEN |
| IND-02 | Model-free core | local ingest, search, memory, planning, deterministic tool and export workflows operate without model services | any undeclared provider request or silent mock-as-real success | OPEN |
| IND-03 | Orchestration | durable job IDs, idempotency, resumable states, approvals, tool allowlist, cancellation and error receipts | replay duplicates side effects, confused authority, lost state | OPEN |
| IND-04 | Local inference | optional signed/hashed engine and licensed compatible model pack detected and used entirely locally | missing engine prevents application launch, wrong fallback claimed as successful inference | OPEN |
| IND-05 | Isolation | default-deny outbound access; documented allowlist of explicitly authorized online features | DNS, updates, telemetry, crash report, fetch or remote model connection without authorization | OPEN |
| IND-06 | Persistence | SQLite integrity, migrations, atomic writes, recovery, backup/restore, concurrent worker ownership | corruption, duplicate IDs, lost updates, silent truncation | OPEN |
| IND-07 | macOS | prepared arm64 and x64 app installs and first-launches on clean offline hosts | host Python/Node/Git/npm/pip/compiler requirement, unsigned distribution claim | OPEN |
| IND-08 | Windows | prepared x64 package installs and first-launches on clean offline Windows hosts | undeclared WebView2, PATH or network requirement | OPEN |
| IND-09 | Mobile | native/sandboxed local-core capability matrix verified separately on iOS/Android | desktop-only functionality silently claimed mobile-supported | OPEN |
| IND-10 | Regression | positive + negative tests, source/manifest/hash linkage to exact release, cross-platform audit | checksum mismatch, stale CI, incorrect denominator, partial failure | OPEN |
| IND-11 | Release | exact-binary lifecycle and rollback passes; zero unresolved within declared release scope | certification on source tests only or unresolved mandatory dependency | BLOCKED |
| IND-12 | Reuse | one canonical skill/agent with executable tests and documented ownership | parallel orchestrator or duplicated mutable memory | BLOCKED |

## Dependency inventory schema (required)
Every dependency record: id, source_path, provider, purpose, build_or_runtime, mandatory_or_optional, platform, network_or_host, version, license, SHA256_if_materialized, evidence, last_verified_utc, test_ids, status.
Classify dependency as: BUNDLED_MANDATORY | BUNDLED_OPTIONAL | BUILD_ONLY | EXPLICIT_NETWORK | HOST_REQUIRED_OPEN | UNRESOLVED.
Network denials must be captured, not treated as an empty log.

## Certification arithmetic
source_items = classified_items + unresolved_items. Mandatory runtime requirements must have zero unresolved. Each platform forms its own denominator; do not collapse unsupported mobile features into desktop PASS. Certification requires verified artifact bytes, native startup, functional checks, isolation trace, persistence/restart, rollback, and hashes.

## Immediate execution queue
1. Generate source manifest and dependency census against exact commit.
2. Implement missing model-free and durable orchestration pieces in their existing canonical modules, with atomic migration and regression tests.
3. Build optional local runtime adapter with explicit no-model behavior.
4. Add connection-attempt logging and deny-by-default OS/network tests.
5. Package clean-host offline distributions and certify each OS/architecture independently.
6. Promote reusable skill/agent only from passing tests and observed behavior.

No release promotion until IND-01 through IND-12 pass within explicit scope.


## October 26, 2026 — live integration gate (AUDIT_ONLY)
- Reacquired `main` head: `8e66f535f6ea7bb0a7f710678a5d764467717a1f`.
- PR #241 head before this audit: `ad27db17096b4797ce545b445e53c26f1fe0680b`.
- Compare current main -> PR head: DIVERGED; PR 8 commits ahead and 130 behind, source compare includes six PR paths.
- Historical CI lookup for `ad27db17096b4797ce545b445e53c26f1fe0680b` returned zero runs, NOT a PASS or a proof that CI is disabled.
- Existing canonical execution areas include `backend/app/work_runs.py`, `backend/app/execution_gate.py`, `backend/app/model_router.py`, `backend/app/offline.py`, and `backend/app/db.py`.
- STOP-GATE: no rebasing, cherry-picking, API binding, GUI work, merge or promotion until source differences and canonical ownership are resolved against frozen current `main` and independently tested.
- Do not project historical CI, old release receipts or branch source tests onto exact binary certification.
- PR remains draft, documentation-and-prototype only; native packaging, mobile validation, rollback and production certification remain OPEN/BLOCKED.
