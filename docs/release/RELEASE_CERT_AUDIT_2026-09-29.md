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

## 4. Findings and their status after re-checking through `39b5cc7` (2026-10-21)

| ID | Finding | Tier | Confidence | Status |
| --- | --- | --- | --- | --- |
| F-1 | Release-grade and macOS evidence is bound to `0a806c1`, and the mobile rendered receipt to `a8515be`. Later commits changed the release control plane, the launcher and the frontend. | T1 | 99% | OPEN until the final commit is frozen and every gate is re-run on it (release gate in `docs/RELEASE_SCOPE_2026-09-29.md`) |
| F-2 | The release class requires a native arm64 portable runtime. Canonical main now contains `packaging/runtime/macos-arm64.json` for CPython 3.13.15 / `20260901`, with independently frozen source bytes/hash, archive geometry, logical-member and payload-multiset hashes, plus the dedicated hash-locked `packaging/runtime/requirements-macos-arm64.lock`. PR #257 head `34b89979c71e38a089b27b1aaa54f2b33f225107` executed ARM64 Portable Runtime Materialization Certification run `39684687117` successfully and CI `39684687130` successfully. | T1 | 100% | PARTIAL-CLOSED. Source discovery, native manifest/lock and one exact-head arm64 materialization certification are proven. PR #257 is still open, and the final exact-SHA portable app lifecycle / clean-host / release freeze remain independent gates; do not equate materialization PASS with full release certification. |
| F-3 | The "distinct-version" upgrade compared two builds with the same version (`0.1.23`), and the API reported `0.1.21`. | T1 | 97% | CLOSED in code. #134 rejects same-version rollback (`ERROR_CLEAN_MACHINE_SAME_VERSION`) and aligns the API version (`test_api_version_contract.py`); PR #136 bumps to `0.1.24`. |
| F-4 | The "clean-machine" lifecycle ran on a runner that had just installed Python, Node and dev dependencies. | T1 | 90% | RELABELLED by #134 (`PACKAGED_LIFECYCLE_VERIFIED`, `clean_host_certified: false`). A separately provisioned host lifecycle is still OPEN. |
| F-5 | The packaged app binds loopback only and the backend has no TLS, so a phone cannot reach a released build. | T1 | 95% | DECIDED outside this release. Recorded as SD-012 (unadopted enhancement). *Update 2026-10-01: the project owner adopted SD-012 as a PROVISIONAL scope item for access through the source launcher (`docs/PHONE_ACCESS.md`). The packaged app is unchanged and still binds loopback only.* |
| F-6 | Rendered browser coverage originally lacked WebKit. Current exact-head PR #260 rendered QA run `39688908375` passed both WebKit and Chromium at desktop and iPhone-width viewports before merge as main `39b5cc7f938e6db023162952d0779518ed887e88`. | T1 | 100% | CLOSED for Playwright WebKit/Chromium rendered-browser coverage. Native macOS WKWebView, VoiceOver and on-device iOS Safari remain separate evidence gates. |
| F-7 | `current_state_snapshot_v1.json` (2026-09-24, `0bf5bf8`) described the app as a source-bound bash wrapper, with no receipts. The replacement snapshot was actually captured at exact head `4a95ca820baf3156c75959069528fba57345b917` on 2026-09-29T16:55:34Z with 975 passed / 1 skipped and six valid recorded receipts. Subsequent audit/governance commits mean that snapshot is now historical rather than current. | T1 | 100% | CLOSED for the stale 2026-09-24 description; refresh `current_state_snapshot_v1.json` again at the final exact-SHA freeze instead of treating the 4a95ca8 capture as evidence for later heads. |
| F-8 | The packaged release artifact and source-bound launchers previously shared the visible identity `Mucaro AI.app`. Current-main PR #260 separated the authority surfaces: source checkout/generated source wrapper = `Mucaro AI Setup.app` / display name `Mucaro AI Setup` / `ai.mucaro.source-launcher`; packaged release = `Mucaro AI.app` / `ai.mucaro.app`. The source-wrapper verifier rejects release-name, release-bundle-ID, display-name and receipt drift. Portable packaging excludes tracked `.app` trees case-insensitively, receipts the exclusions, rejects nested app members, and preserves legacy-v1 manifest compatibility. | T1 | 100% | CLOSED on canonical main `39b5cc7f938e6db023162952d0779518ed887e88`. Exact head `487a1ea76885dd77a36d39983a9ad69cc8571675` passed CI `39688908457`, Rendered UI QA `39688908375`, One-click setup `39688908425`, macOS app `39688908389`, OCR08 structure `39688908383`, and MATRIX-017 performance `39688908434`. PR #158 is closed superseded; PR #133 remains historical evidence only. |
| F-9 | Windows and Linux have a source setup but no packaged or signed lane. | T1 | 99% | DECIDED outside this release. Recorded as SD-013 (unadopted enhancement). |
| F-10 | The former permission/failover branch overlap is now resolved in canonical history. V06 permission enforcement merged through PR #161 at exact head `830995b109f73196b91eb13d70623b48b97a2efe` after executed PASS in CI, Rendered UI, OCR structure/live, and macOS. V04 failover was then integrated on the merged V06 boundary through PR #234 exact head `eaf83f78a0b4050ca231f69a5b8d8244dd13fa57`, with CI `38960512965`, Rendered UI `38960513049`, and macOS `38960513344` all PASS. Current main contains the failover engine, contract and V06↔V04 negative integration regressions. | T2 | 100% | CLOSED. Legacy/mixed reconstruction PRs #114/#117/#145/#163/#196/#199/#200/#205/#207 are historical or closed superseded; they are not current merge candidates. |
| F-11 | `BLK-ACTIONS-001` recorded a real period in which hosted jobs terminated before runner step 1. Runner admission subsequently recovered; current canonical blocker ledger no longer lists `BLK-ACTIONS-001` ACTIVE, and multiple October exact-head workflows execute normally. | T1 | 100% | CLOSED as an active blocker. Preserve the historical zero-step evidence and unknown account-specific cause; do not retroactively relabel those runs as code failures. |
| F-12 | PR #146 merged during the historical runner-admission outage even though its exact-head CI did not execute steps. The generated-status correction itself was mechanically correct, but that merge remains a process exception to the declared executed-PASS rule. | T1 | 100% | HISTORICAL PROCESS_EXCEPTION retained. Subsequent exact-head workflows now execute and PASS; the exception does not justify bypassing executed gates on future merges. |
| F-13 | V06 permission enforcement and V04 failover overlap in `model_router.py` and `services.py`; their combined semantics required explicit negative integration coverage. | T1 | 100% | CLOSED by merged PR #234 on the already merged V06 boundary. Current main preserves settings-aware permission checks, terminal permission denial, bounded retry/failover, checkpoint interruption semantics, streaming suppression and the canonical failover contract; #234 executed CI, Rendered UI and macOS PASS before merge. |

## 5. OPEN residue (45 rows)

| Group | Rows | Count |
| --- | --- | ---: |
| Process and conduct: verified by auditing the record at certification | KERNEL-001/005/007–013/015–018/021/022, EXEC-DIRECTIVE-001 | 16 |
| Workbench vectors | TGT-003/004/005/006/007/012/013/015/038, MATRIX-005/006/009/013 | 13 |
| Invariants that mostly follow those vectors | INV-001/003/008–013/015/017/019/021, GOAL-003 | 13 |
| Security and recovery | TGT-021, MATRIX-016 | 2 |
| Linked PR list and detail | MOB-F19 | 1 |

The five group counts above are an exact partition of the 45 authoritative OPEN rows on main `39b5cc7`: `16 + 13 + 13 + 2 + 1 = 45`. Since the September 49-row snapshot, `TGT-011`, `INV-020`, `TGT-020`, and `MATRIX-017` moved out of OPEN. This table is descriptive; the acceptance ledger remains authoritative.

## 6. Blockers by who can clear them

**User or operator — credentials/physical evidence last:**
1. BLK-SIGN-001: Apple Developer ID Application certificate and notarytool profile.
2. BLK-PHYS-001: literal physical double-click evidence on a supported Mac.
3. BLK-LLM-001: Anthropic key for the live remote-model gate.
4. BLK-WEB-001: Brave search key for governed live-search gates.

**Engineering / credential-free:**
- finish the native arm64 portable-app lifecycle after the now-canonical arm64 source/lock and successful materialization proof;
- separately provision and certify the clean-host packaged lifecycle;
- obtain native WKWebView and VoiceOver evidence and preserve on-device iOS Safari as its own gate;
- close the 45 authoritative OPEN rows plus remaining PROVISIONAL/BLOCKED requirements in DAG order;
- refresh exact-head state/provenance only after the candidate tree is frozen.

## 6.1 Resolved integration lineage

The September candidate merge-order problem is no longer active:

- F-8 is canonical through merged #260.
- V06 permissions are canonical through merged #161.
- V04 failover-on-V06 is canonical through merged #234.
- stale reconstruction branches have been closed or retained only as historical evidence.
- PR #257 is the current open arm64 materialization/workflow follow-up and must be judged against the post-#260 main before merge; its earlier PASS remains evidence for its exact tested SHA, not automatically for a later rebased SHA.

## 6.2 Hosted-runner cause boundary — resolved active condition

The former universal zero-step condition is no longer present. October 21 exact-head runs for #260 executed real runner steps across CI, Chromium, WebKit, macOS, Linux, Windows and certification workflows. The canonical blocker ledger therefore correctly omits `BLK-ACTIONS-001` from the ACTIVE set.

The historical account-specific cause remains **UNKNOWN**. Billing/quota/policy were hypotheses and must not be promoted to fact retroactively.

## 7. Critical path

1. **Credential-free engineering:** reconcile PR #257 against post-#260 main; complete native arm64 portable-app lifecycle, clean-host lifecycle, native accessibility evidence, and the 45 OPEN / remaining PROVISIONAL requirements.
2. **Exact candidate freeze:** freeze one commit and refresh `current_state_snapshot_v1.json`, source/build/runtime/package hashes and receipts on that exact SHA.
3. **Final external gates:** physical-input proof plus signing/notarization, live LLM and live search credentials only after the credential-free tree is closed.
4. **Terminal replay:** re-run all mandatory gates on the final exact SHA and issue release-grade certification only when every terminal condition is satisfied.

## 8. Blind spots

- The PR #136 portable-lane artifact `11047195637` was subsequently opened and independently checked: the downloaded artifact SHA-256 matched GitHub's recorded digest, and its receipts bind the x86_64 `0.1.23 -> 0.1.24` packaged lifecycle while explicitly leaving native arm64, clean-host, physical-input and signing gates false. PR #132's uploaded portable-lane artifact remains unopened here.
- Rosetta and iOS Safari behavior are T4.
- The KERNEL process rows were not audited, and the 45 KB release contract was not read in full.
