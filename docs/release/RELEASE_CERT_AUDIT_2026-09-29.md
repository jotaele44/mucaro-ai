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
| F-2 | The portable runtime source is x86_64 only, but the release class is arm64 and both lanes are required. | T1 | 95% | DECIDED 2026-09-29: native arm64 runtime required. Recorded as RC-009 (ADJUDICATED, material). The arm64 runtime spec and an Apple Silicon run are still OPEN. |
| F-3 | The "distinct-version" upgrade compared two builds with the same version (`0.1.23`), and the API reported `0.1.21`. | T1 | 97% | CLOSED in code. #134 rejects same-version rollback (`ERROR_CLEAN_MACHINE_SAME_VERSION`) and aligns the API version (`test_api_version_contract.py`); PR #136 bumps to `0.1.24`. |
| F-4 | The "clean-machine" lifecycle ran on a runner that had just installed Python, Node and dev dependencies. | T1 | 90% | RELABELLED by #134 (`PACKAGED_LIFECYCLE_VERIFIED`, `clean_host_certified: false`). A separately provisioned host lifecycle is still OPEN. |
| F-5 | The packaged app binds loopback only and the backend has no TLS, so a phone cannot reach a released build. | T1 | 95% | DECIDED outside this release. Recorded as SD-012 (unadopted enhancement). |
| F-6 | The rendered UI had no WebKit evidence. | T1 | 98% | PARTIAL. A Playwright WebKit leg on Linux was added and passed at `9422cd7`. macOS WKWebView and iOS Safari on a device are still unverified. |
| F-7 | `current_state_snapshot_v1.json` (2026-09-24, `0bf5bf8`) described the app as a source-bound bash wrapper, with no receipts. | T1 | 97% | CLOSED by the snapshot refresh that accompanies this record |
| F-8 | The packaged release artifact and the source-checkout bootstrap previously shared the visible name `Mucaro AI.app`. Draft PR #133 now separates them as `Mucaro AI.app` / `ai.mucaro.app` and `Mucaro AI Setup.app` / `ai.mucaro.launcher`, with an explicit regression preventing the checkout app from reusing the packaged name. | T2 | 95% | CANDIDATE-RESOLVED on PR #133 head `20864689ccdd5fb1a0f4df5217418f944c464d85`; remains OPEN on canonical `main` until exact-head execution and merge. |
| F-9 | Windows and Linux have a source setup but no packaged or signed lane. | T1 | 99% | DECIDED outside this release. Recorded as SD-013 (unadopted enhancement). |
| F-10 | Open workstream branches overlap but are not one duplicate set. PR #117 contains all three commits from #114, plus V02 checkpoint hardening and its V04 model-failover delta. PRs #106, #108 and #117 are mutually diverged. The unique V04 model-failover behavior has now been reconstructed cleanly on current main in draft PR #145, head `d3bd0cca8a39340848b3349e305d21f9b73e15ad`, without #114/V02/#106/#108 history; #117 was converted back to DRAFT. Historical #114/#117 CI executed and failed only the coding-profile lint row (Ruff I001 in `backend/app/coding_container.py` import order); that source defect is repaired on #114 head `4b5e34b4f4d16daf0bb97c2c1c9cc14d8e8a9c8c` and #117 head `2d66f878ea51a64747913f996c1eb871c20e727f`. Fresh #114/#117/#145 hosted jobs remain zero-step failures under BLK-ACTIONS-001. | T2 | 99% | CANDIDATE-RESOLVED structurally. #114 remains the clean permission-engine slice. #145 is the clean V04 replacement candidate. #106 and #108 remain separate because their commits are not ancestral to #117. #117 may be closed as superseded only after #145 executes real nonzero-step gates and preserves the unique V04 behavior; canonical main remains OPEN until then. |

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
- #114 permission-engine source repair and #145 clean V04 reconstruction are both waiting on nonzero-step hosted execution under BLK-ACTIONS-001; #117 is preserved as draft historical/unique-delta evidence

## 7. Critical path

1. **Infrastructure:** clear BLK-ACTIONS-001 by restoring GitHub-hosted runner admission; the first valid evidence must execute at least one workflow step.
2. **Inputs:** BLK-SPEC-001 and BLK-PHYS-001 decisions, then the Apple signing credentials.
3. **Engineering:** validate #114 and #145 on real runners, then complete the arm64 portable lane and the separately provisioned lifecycle host, followed by the OPEN and PROVISIONAL rows in DAG order (N02 → N25).
4. **Freeze:** freeze one commit and re-run every gate on it. That covers both packaged lanes, the signed-installer receipt and hosted CI, followed by the final release manifest.

## 8. Blind spots

- The PR #136 portable-lane artifact `11047195637` was subsequently opened and independently checked: the downloaded artifact SHA-256 matched GitHub's recorded digest, and its receipts bind the x86_64 `0.1.23 -> 0.1.24` packaged lifecycle while explicitly leaving native arm64, clean-host, physical-input and signing gates false. PR #132's uploaded portable-lane artifact remains unopened here.
- Rosetta and iOS Safari behavior are T4.
- The KERNEL process rows were not audited, and the 45 KB release contract was not read in full.
