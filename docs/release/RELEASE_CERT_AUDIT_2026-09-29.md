# Release-certification audit: desktop and mobile (2026-09-29)

**Scope:** development completion and the blockers to final release
certification for the packaged desktop app and the mobile (iPhone-width) UI.
**Base:** the first pass was at `0ed1643`. Every finding was re-checked on `main`
at `f2ee905` (after #124, #134, #135 and #125).
**Method:**
- the release ledgers in `_architecture/release/`, re-validated with `mucaro release scope validate`
- the exact-head evidence under `_architecture/release/evidence/`
- code and workflow reads
- hosted Actions runs and open PRs

**Evidence tiers:** T1 is a repository record, code or measurement. T2 is an
operational record (Actions runs, PR state). T4 is platform behavior that was not
reproduced here.

This record supersedes the counts in `docs/BLOCKERS_AND_UNBLOCK_PLAN_2026-09-28.md`
(50 OPEN / 25 PROVISIONAL). Since then, MATRIX-007 has moved from OPEN to
PROVISIONAL. Scope decisions are in `docs/RELEASE_SCOPE_2026-09-29.md`.

## 1. Verdict

`MUCARO AI RELEASE STATUS = PROVISIONAL`, recomputed from the ledgers (T1).

| State | Rows | Share of 137 applicable rows |
| --- | ---: | ---: |
| PASS | 55 | 40.1% |
| OPEN | 49 | 35.8% |
| PROVISIONAL | 26 | 19.0% |
| BLOCKED | 7 | 5.1% |
| FAIL | 0 | 0% |
| NOT_APPLICABLE | 1 | n/a |

The percentage denominator is 137 applicable rows: 136 rows have `scope_state=IN_SCOPE` and TGT-027 is `EXTERNALLY_BLOCKED` but remains a required release row. LEARN-GATE-001 is the single `NOT_APPLICABLE` row and is excluded from the percentage denominator.

Four of the nine terminal-rule conditions hold. These five do not:
- bound specification
- zero open
- zero provisional
- zero blocked
- zero active blockers

The last release-grade run (`evidence/release_grade_0a806c1e8c98`) passed 15 of 21
required gates. The remaining six were BLOCKED:

| Gate | Status |
| --- | --- |
| `signed_notarized_installer` | BLOCKED |
| `portable_embedded_runtime` | BLOCKED |
| `portable_install_reinstall_smoke` | BLOCKED |
| `distinct_version_upgrade_rollback` | BLOCKED |
| `clean_machine_macos` | BLOCKED |
| `release_scope` | BLOCKED |

Hosted CI admits and runs jobs; `CI`, `macOS app`, `One-click setup` and
`Shadow QA` were green on `0ed1643` (T2). The hosted `startup_failure`
narrative in `docs/RELEASE_STATUS.md` is historical.

## 2. Desktop (release class: Apple Silicon, macOS 14+, Developer ID direct download)

**Done (T1):** `Mucaro AI.app` (PyInstaller, WKWebView window) was proven on a
hosted macOS 14 arm64 runner at `0a806c1`:
- install from zip and open through LaunchServices
- the window was shown and the GUI connected with the launch token
- ingest → cited answer → export
- relaunch persistence, single instance, SIGTERM
- reinstall keeps the data

Data lives outside the bundle, and database upgrades are atomic and
forward-only.

| Row | State | Blocker | Who clears it |
| --- | --- | --- | --- |
| TGT-027 signing and notarization | BLOCKED (external) | BLK-SIGN-001 | Operator: Developer ID certificate + notary profile |
| TGT-026 packaging, TGT-028 installation | BLOCKED | BLK-SIGN-001 | As above |
| TGT-001 executable app, MATRIX-018 | BLOCKED | BLK-PHYS-001 | User: physical double-click, or record acceptance of the LaunchServices evidence |
| TGT-029 upgrade/rollback, MATRIX-019 | PROVISIONAL | No arm64 portable-lane receipt (RC-009) | Engineering |
| MATRIX-020 final manifest | PROVISIONAL | Signing | Operator |
| TGT-036 and MATRIX-015 UI and accessibility | PROVISIONAL | No WKWebView or VoiceOver evidence | Engineering on macOS |
| GOAL-001 product outcome | PROVISIONAL | All of the above, plus the workbench rows still OPEN | All |

## 3. Mobile (responsive web UI at 390×844; native iOS is outside the release)

**Rows:** 16 of the 20 MOB rows PASS (80%).
- MOB-F01, F15 and F20 are PROVISIONAL: real iOS keyboard avoidance,
  narration-versus-receipt reconciliation, and copy/read-aloud activation.
- MOB-F19 (linked PR list and detail) is OPEN.

**Evidence:** the rendered QA now runs Chromium and Playwright WebKit on
`ubuntu-latest`.
- Both legs passed on PR #136 at `9422cd7` (run 36597649422, T2).
- A later run at `69bf1cb` failed in both legs within 2–36 s of starting. That is
  shorter than the QA step alone took in earlier runs, so it is not counted as
  rendering evidence either way.

This is not iOS Safari on a phone and not the packaged WKWebView window, so none
of the rows above can move to PASS on this evidence.

## 4. Findings and their status after re-checking on `f2ee905`

| ID | Finding | Tier | Confidence | Status |
| --- | --- | --- | --- | --- |
| F-1 | Release-grade and macOS evidence is bound to `0a806c1`, and the mobile rendered receipt to `a8515be`. Later commits changed the release control plane, the launcher and the frontend. | T1 | 99% | OPEN until the final commit is frozen and every gate is re-run on it (release gate in `docs/RELEASE_SCOPE_2026-09-29.md`) |
| F-2 | The portable runtime source is x86_64 only, but the release class is arm64 and both lanes are required. The current bounded arm64-preparation candidate is draft PR #206 head `526ad66ca1345bc901f4280bc1e1dbcd3bad84a7`, reconstructed directly on October main `7f104bf9114c85df92c24eeaecaf2260465b4568` as the same five-file prep surface. It binds immutable upstream `20260901` arm64 metadata, keeps `20260929` as a separate future two-architecture refresh, adds mode-sensitive logical archive identity and bounded archive inspection, but still lacks independently retrieved arm64 bytes, observed archive geometry, a dedicated Python 3.13 aarch64 wheel-only lock, runtime materialization and Apple-Silicon lifecycle evidence. Exact-head CI/Chromium/WebKit/macOS jobs all failed before step 1 under BLK-ACTIONS-001. | T1 | 100% | DECIDED: RC-009 still requires native arm64. #206 is CANDIDATE-RESOLVED for source/preflight mechanics only; arm64 byte identity, lock, runtime and lifecycle remain OPEN and no x86 evidence may be relabeled. |
| F-3 | The "distinct-version" upgrade compared two builds with the same version (`0.1.23`), and the API reported `0.1.21`. | T1 | 97% | CLOSED in code. #134 rejects same-version rollback (`ERROR_CLEAN_MACHINE_SAME_VERSION`) and aligns the API version (`test_api_version_contract.py`); PR #136 bumps to `0.1.24`. |
| F-4 | The "clean-machine" lifecycle ran on a runner that had just installed Python, Node and dev dependencies. | T1 | 90% | RELABELLED by #134 (`PACKAGED_LIFECYCLE_VERIFIED`, `clean_host_certified: false`). A separately provisioned host lifecycle is still OPEN. |
| F-5 | The packaged app binds loopback only and the backend has no TLS, so a phone cannot reach a released build. | T1 | 95% | DECIDED outside this release. Recorded as SD-012 (unadopted enhancement). *Update 2026-10-01: the project owner adopted SD-012 as a PROVISIONAL scope item for access through the source launcher (`docs/PHONE_ACCESS.md`). The packaged app is unchanged and still binds loopback only.* |
| F-6 | The rendered UI had no WebKit evidence. | T1 | 98% | PARTIAL. A Playwright WebKit leg on Linux was added and passed at `9422cd7`. macOS WKWebView and iOS Safari on a device are still unverified. |
| F-7 | `current_state_snapshot_v1.json` (2026-09-24, `0bf5bf8`) described the app as a source-bound bash wrapper, with no receipts. The replacement snapshot was actually captured at exact head `4a95ca820baf3156c75959069528fba57345b917` on 2026-09-29T16:55:34Z with 975 passed / 1 skipped and six valid recorded receipts. Subsequent audit/governance commits mean that snapshot is now historical rather than current. | T1 | 100% | CLOSED for the stale 2026-09-24 description; refresh `current_state_snapshot_v1.json` again at the final exact-SHA freeze instead of treating the 4a95ca8 capture as evidence for later heads. |
| F-8 | The packaged release artifact and source-bound launchers must not share the visible identity `Mucaro AI.app`. Draft PR #158 now has exact head `f27820252125e38fdc7b80db2c31a6c1413f6086` on current main `7f104bf9114c85df92c24eeaecaf2260465b4568`, with a 26-file identity/portable-payload scope. Both source-bound manifestations use `Mucaro AI Setup.app` / display name `Mucaro AI Setup` / bundle ID `ai.mucaro.source-launcher`; `Mucaro AI.app` / `ai.mucaro.app` remains reserved for the release lane. Portable packaging excludes tracked `.app` bundles case-insensitively, receipts exclusions, rejects manifest-declared nested app members, and now also has an explicit regression proving an undeclared physical nested `Injected.app` fails the recomputed application-member manifest. Historical v1 manifests without the new exclusion receipt remain verifiable. PR #160 is closed unmerged with no unique runtime behavior; PR #133 remains historical pre-#149 evidence only. | T1 | 100% | CANDIDATE-RESOLVED structurally on #158. Exact-head CI, Windows, Linux, macOS arm64/Intel, Chromium, WebKit, one-click and macOS package jobs still terminate with zero executed steps under BLK-ACTIONS-001, so F-8 remains OPEN until real executed gates pass and #158 merges. |
| F-9 | Windows and Linux have a source setup but no packaged or signed lane. | T1 | 99% | DECIDED outside this release. Recorded as SD-013 (unadopted enhancement). |
| F-10 | Legacy PR #114 is the historical V06 permission-engine aggregate and PR #117 is the historical mixed permission/checkpoint/V04 aggregate. Their formerly separate clean candidates (#161 V06, #145/#205 V04) and the stacked #163 integration have now been reconstructed together on current October main in draft PR #207, exact head `36d3c600d3fe1acffb4fbe7187489a24d067e73f`. #207 has exactly 37 changed files: the 26 non-overlapping V06/V04 files were transplanted by exact Git blob from #163 and the 11 files that independently changed on October main were semantically reconciled rather than overwritten. It preserves current replay-safe provider-attempt identity, later coding/OCR/container/offline behavior, settings-aware `model.invoke` readiness/inference permission, exact `cfg` propagation, and V04 bounded failover. #106/#108 remain separate historical work because their commits are not ancestral to #117. | T2 | 100% | CANDIDATE-RESOLVED structurally on #207 for the combined declared scope. Keep #114/#117/#161/#163/#205 open only as historical/intermediate evidence until #207 executes real nonzero-step gates and preserves the unique deltas; then retire superseded candidates. |
| F-11 | Canonical main previously bound active `BLK-ACTIONS-001` in the blocker ledger while the generated `docs/RELEASE_STATUS.md` block omitted it. PR #146 regenerated exactly that blocker line without changing the 138-row denominator and has now merged as main commit `1a64e90094624907fca059ba49725bc856e42d2a`. | T1 | 100% | CLOSED on canonical main. This closes generated-state drift only; `BLK-ACTIONS-001` itself remains ACTIVE until a later hosted job executes at least one workflow step and the affected lane is adjudicated from actual output. |
| F-12 | PR #146 merged as main commit `1a64e90094624907fca059ba49725bc856e42d2a` even though its exact-head CI run `36742837679` failed before step 1 under BLK-ACTIONS-001. The merged one-line generated-status repair is mechanically consistent with the active blocker ledger, so reverting it would reintroduce known generated-state drift; however the merge did not satisfy the declared executed-PASS admission rule. | T1 | 100% | PROCESS_EXCEPTION recorded. Do not treat the merge as test validation. Preserve the corrected generated document, require nonzero-step executed evidence for subsequent candidate merges, and keep BLK-ACTIONS-001 ACTIVE. |
| F-13 | The V06 permission boundary and V04 failover overlap in `model_router.py`, `services.py`, routing, provider recovery and related tests. Current integration candidate #207 head `36d3c600d3fe1acffb4fbe7187489a24d067e73f` resolves that overlap directly on October main. Required invariants are simultaneously present: permission denial stops after one attempt, never reaches mock, never reaches local transport, never pauses as provider outage; `ERROR_PERMISSION_APPROVAL_REQUIRED` is terminal; exact settings context reaches readiness/routing/every attempt; failover receipts use the canonical MODEL_ROUTING contract; multi-provider streaming is suppressed; provider-outage pause preserves run identity; and current replay-safe provider-attempt ACCEPTED/INTERRUPTED_UNKNOWN/reclaim semantics are preserved. A source-parity sweep closed at exactly 37 files. Exact-head CI `37182443250`, Rendered UI `37182443178` Chromium/WebKit, OCR runtime `37182443239`, and macOS `37182443183` all failed with zero executed steps. | T1 | 100% | CANDIDATE-RESOLVED structurally on #207; execution remains BLOCKED_EXTERNAL under BLK-ACTIONS-001. Do not merge or retire #161/#163/#205/#114/#117 until #207 receives actual executed PASS. |

## 5. OPEN residue (49 rows)

| Group | Rows | Count |
| --- | --- | ---: |
| Process and conduct: verified by auditing the record at certification | KERNEL-001/005/007–013/015–018/021/022, EXEC-DIRECTIVE-001 | 16 |
| Workbench vectors | TGT-003 (GIS/OCR/Code/Artifacts workspaces), TGT-004/005/006/007/011/012/013/015/038, MATRIX-005/006/009/013 | 14 |
| Invariants that mostly follow those vectors | INV-001/003/008–013/015/017/019–021, GOAL-003 | 14 |
| Performance: no frozen thresholds | TGT-020, MATRIX-017 | 2 |
| Security and recovery | TGT-021, MATRIX-016 | 2 |
| Linked PR list and detail | MOB-F19 | 1 |

The six group counts above are a partition of the 49 authoritative OPEN rows: `16 + 14 + 14 + 2 + 2 + 1 = 49`. The earlier draft labeled the Workbench group as 15 even though the row text enumerates 14 requirement IDs; the ledger denominator was correct and did not change.

## 6. Blockers by who can clear them

**User or operator:**
1. BLK-SPEC-001: supply the Workbench Specification, or formally remove it.
2. BLK-SIGN-001: Apple Developer ID Application certificate and notarytool profile.
3. BLK-PHYS-001: a physical double-click on a supported Mac, with physical-input evidence.
4. BLK-LLM-001: Anthropic key (TGT-009).
5. BLK-WEB-001: Brave search key (TGT-006, MATRIX-008).

**Engineering:**
- an arm64 portable runtime manifest and lock, plus an Apple Silicon portable-lane run (RC-009)
- a separately provisioned host for the packaged lifecycle (F-4)
- WKWebView and VoiceOver evidence on macOS (TGT-036, MATRIX-015)
- the 49 OPEN rows
- #158 canonical F-8 repair, #161 complete V06 permission reconstruction, #145 clean V04 reconstruction, and the V06↔V04 combined integration gate are waiting on nonzero-step hosted execution under BLK-ACTIONS-001; #114 and #117 are preserved as draft historical/unique-delta evidence

## 6.1 Candidate merge-order compatibility

Static file-overlap review on 2026-09-30 found:

- **#158 F-8 vs #161 V06:** 0 overlapping files.
- **#158 F-8 vs #163 V04-on-V06:** 0 overlapping files.
- **#158 F-8 vs #139 arm64 prep:** one overlap, `backend/tests/test_cli.py`; the hunks are disjoint (source-wrapper expectations vs portable-runtime CLI tests).
- **#161 V06 vs #139 arm64 prep:** one overlap, `backend/app/cli.py`; the hunks are disjoint (subprocess permission gating vs portable-runtime parser/handler additions).

Therefore #158 is independently mergeable from the V06/V04 stack once executed gates pass. #139 has no identified semantic conflict with V06, but it must still be restacked/replayed after V06 lands so its exact-head test evidence covers the combined CLI tree. This is source compatibility only, not execution PASS.

## 6.2 Hosted-runner cause boundary — 2026-09-30 refresh

A fresh public-status check on 2026-09-30 found GitHub **Actions operational** and no public incident reported for September 30 (`https://www.githubstatus.com/`). The connected GitHub integration can read workflow runs/jobs but does not expose repository Actions-permission, hosted-runner eligibility, billing/minutes, or quota endpoints. Therefore the account/repository-specific cause of the universal zero-step failures remains **UNKNOWN**. Billing/quota and repository/account policy remain hypotheses only. Do not spend certification runs on retries unless runner admission state changes.

## 7. Critical path

1. **Infrastructure:** clear BLK-ACTIONS-001 by restoring GitHub-hosted runner admission; the first valid evidence must execute at least one workflow step.
2. **Inputs:** BLK-SPEC-001 and BLK-PHYS-001 decisions, then the Apple signing credentials.
3. **Engineering:** once hosted execution resumes, validate #158 F-8 identity repair, #161 complete V06 permission enforcement, then integrate/revalidate #145 V04 failover against V06 before #139 arm64-runtime prep on their current-main heads; then complete the native arm64 portable lane and the separately provisioned lifecycle host, followed by the OPEN and PROVISIONAL rows in DAG order (N02 → N25).
4. **Freeze:** freeze one commit, refresh `current_state_snapshot_v1.json` on that exact SHA, and re-run every gate on it. That covers both packaged lanes, the signed-installer receipt and hosted CI, followed by the final release manifest.

## 8. Blind spots

- The PR #136 portable-lane artifact `11047195637` was subsequently opened and independently checked: the downloaded artifact SHA-256 matched GitHub's recorded digest, and its receipts bind the x86_64 `0.1.23 -> 0.1.24` packaged lifecycle while explicitly leaving native arm64, clean-host, physical-input and signing gates false. PR #132's uploaded portable-lane artifact remains unopened here.
- Rosetta and iOS Safari behavior are T4.
- The KERNEL process rows were not audited, and the 45 KB release contract was not read in full.
