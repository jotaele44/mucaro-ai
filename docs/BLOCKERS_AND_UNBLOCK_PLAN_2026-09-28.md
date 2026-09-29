# Blockers and unblock plan — mucaro-ai (2026-09-28)

**Audit date:** 2026-09-28 · **`main` at audit:** `64a097b` (private repository; not branch-protected) · **Release status:** `MUCARO AI RELEASE STATUS = PROVISIONAL`

This document lists every blocker that the repository, its CI, and its GitHub pull requests recorded as of the audit date, then gives an ordered plan to clear them. It changes no code, ledger, release manifest, or generated status block. Authoritative release state stays in `_architecture/release/` and in the generated block of `docs/RELEASE_STATUS.md`.

mucaro-ai is outside the seven-repository PRII federation. Its federation-dependent items are listed under MU-12.

## How this inventory was built

Sources checked:

- open issues (none) and all 10 open pull requests;
- CI on `main` (`CI` and `One-click setup` are green on `64a097b`) and on each PR;
- `_architecture/release/blocker_ledger_v1.json` and the release-grade evidence at `0a806c1`;
- the generated `docs/RELEASE_STATUS.md` block;
- `docs/CODING_GOVERNANCE_BLOCKERS.md`, `docs/PHASE2_QUEUE.md`;
- `_architecture/mucaro_flagpole_gap_matrix_v1.json`;
- the branch list and branch protection.

## Summary

Each blocker is counted once, under its primary type.

| Type | Count |
|---|---:|
| GOV (needs your decision or document) | 2 |
| CRED (operator-held credentials) | 3 |
| GATE | 3 |
| IMPL | 2 |
| PR | 1 group (10 PRs) |
| DATA/HYG | 2 |
| **Total** | **13** |

## Blocker inventory

| ID | Blocker | Type | Evidence | Owner | Unblock step | Exit criterion |
|---|---|---|---|---|---|---|
| MU-01 | BLK-SPEC-001: the "Mucaro AI Release-Grade Workbench Specification" does not exist in the repo or connected Drive | GOV (user) | `blocker_ledger_v1.json` (blocked node N26 → N25). The RELEASE_STATUS residue lists "open specification bindings", and KERNEL-003 is blocked. | User | Supply the specification. Commit it under `docs/release/`, bind its hash in the manifest, and re-enumerate requirements through the scope delta. | Spec bound; KERNEL-003 unblocked |
| MU-02 | BLK-SIGN-001: no Developer ID signing, notarization or stapling | CRED | Blocked node N23. `current_state_snapshot_v1.json`: `APPLE_DEVELOPER_ID_APPLICATION` and `APPLE_NOTARY_KEYCHAIN_PROFILE` absent. The release-grade gate `signed_notarized_installer` is BLOCKED. | Operator | Provide a signing identity and notary profile in a macOS signing environment; run codesign, notarytool and stapler, then `certify_macos_distribution.py` | Signed, notarized installer receipt |
| MU-03 | BLK-WEB-001: no governed live web search | CRED | Blocked node N11. `backend/app/web_search.py` requires `MUCARO_BRAVE_SEARCH_API_KEY` and `allow_live_search` | Operator | Supply the key to the certification environment; run the WEB-08 live eval | WEB-08 live evaluation receipt |
| MU-04 | BLK-LLM-001: no live remote-model evidence | CRED | Blocked node N13. `backend/app/model_router.py` requires `ANTHROPIC_API_KEY` and `MUCARO_AI_LIVE_MODE=1` | Operator | Run the remote-provider evaluation in a permissioned environment | Remote-provider evaluation receipt |
| MU-05 | BLK-PHYS-001: no physical double-click evidence on a Mac | GOV (user) | Blocked node N20. The agent can only produce LaunchServices `open` evidence on hosted macOS runners | User | Double-click the built app on a supported Mac and record the result, or explicitly accept `open` evidence as equivalent | Evidence or decision recorded |
| MU-06 | Release-grade gates are blocked | GATE | `_architecture/release/evidence/release_grade_0a806c1e8c98/provenance.json` (receipt `MUCARO_AI_RELEASE_GRADE_BLOCKED`) marks six gates BLOCKED: `portable_embedded_runtime`, `portable_install_reinstall_smoke`, `distinct_version_upgrade_rollback`, `clean_machine_macos` (no portable inputs yet), `signed_notarized_installer` (MU-02) and `release_scope` (MU-07). All common and native-lane gates pass. | Operator + agent | Supply the portable-lane inputs; run the clean-machine and upgrade/rollback lanes | Release-grade receipt shows no BLOCKED gates |
| MU-07 | The release-scope denominator is not closed | GATE | Generated `docs/RELEASE_STATUS.md`: 138 requirements = 55 PASS / 50 OPEN / 7 BLOCKED / 25 PROVISIONAL / 1 N/A. Blocked: KERNEL-003, MATRIX-018, TGT-001, TGT-009, TGT-026, TGT-028; externally blocked: TGT-027 | Agent + operator | Work the OPEN and PROVISIONAL residue with `mucaro release scope status`; the BLOCKED items follow MU-01 to MU-06 | Terminal rule holds: zero open, provisional or blocked |
| MU-08 | Coding governance is not certified | IMPL | `docs/CODING_GOVERNANCE_BLOCKERS.md`: CODE-B01 (no certified pinned coding-image execution), B02 (checks not executed through the isolated boundary), B03 (governed branch/commit/PR lifecycle not implemented), B05 (`coding_programming_expertise` still labelled ABSENT), B06 (regression corpus not run to zero residue). CODE-B04 (hosted Actions startup failure) is stale: CI has run steps normally since about 2026-09-19. | Agent | Execute B01/B02/B06 on a trusted surface; implement B03; relabel B05; reclassify B04 with green run evidence | Each item closed with evidence |
| MU-09 | Hosted governance lane is open | GATE/GOV | `docs/PHASE2_QUEUE.md` "Hosted governance remediation": billing visibility, and branch protection gated by the private repository's plan. `mucaro package status` = `PACKAGE_WARN`: Mac App Distribution provisioning, App Store Connect processing and App Review are external. | User/operator | Decide the plan (make the repo public, upgrade the plan, or accept no protection); record the decision on billing visibility; pursue the Store lane when ready | `mucaro release hosted-governance` no longer OPEN, or the decision is recorded |
| MU-10 | Flagpole capability gaps | IMPL | `_architecture/mucaro_flagpole_gap_matrix_v1.json` (generated 2026-09-20, now stale): V10 MCP adapter, V11 subagents and V23 seven-repository torture test ABSENT; V08 autopilot and V12 fleet DAG UNVERIFIED; 17 PARTIAL. V01–V03 were partly closed afterwards by merged #102, #103 and #105. | Agent | Regenerate the matrix on current `main`, then close the V-gaps in order | Matrix regenerated; ABSENT count reduced |
| MU-11 | Open PRs | PR | See the next table | Agent + maintainer | Per-PR actions below | No red or superseded PRs |
| MU-12 | Items waiting on a federation execution environment | DATA | `docs/PHASE2_QUEUE.md`: federation membership discovery, the FEDERATION LOCKSTEP client, cross-repo orchestration and the PR-IIS/Spiderweb connector are "Blocked on federation execution environment" | Maintainer | Provide or define the federation execution environment | Items move to Queued or Next |
| MU-13 | Branch sprawl | HYG | 44 branches on origin, including `tmp`, `noop-check`, and 6 identical `feat/ocr-*` branches at `0df53d1` | Maintainer | Prune merged and duplicate branches | Branch list reflects live work |

### Open pull requests (MU-11)

| PR | State | Action |
|---|---|---|
| #117 V04 model failover | RED: `validate` → `CODING_FULL_PROFILE_CERT_FAILED`, `required_failures: ["lint"]` | Fix the lint, rerun |
| #114 V06 permission engine | RED: CI | Investigate and fix |
| #106 V04 provider attempt identity | RED: `validate` | Consolidate with #117 and #108 |
| #108 V04/V05 gaps (draft) | Green | Overlaps #106 and #117; merge the V04 work into one PR |
| #112 V01 flagpole wiring, #113 V03 context identity | Green | Rebase onto post-#110 `main` and merge |
| #116 V02 checkpoint hardening | Green, mergeable (`clean`) | Rebase check, then merge |
| #107 response-flow envelopes (draft) | Green | Probably superseded by the merged #109 and #111. Verify, then close |
| #84 RT09 real-world corpus | CI last ran before 2026-09-19 | Rebase and rerun, or close |
| #122 Shadow QA lane (draft) | Green | Review |

## Unblock plan

### P1 — executable now
1. **MU-11:** fix the lint on #117; consolidate the V04 PRs (#106, #108, #117); rebase the green PRs (#112, #113, #116); verify and close #107.
2. **MU-08:** reclassify CODE-B04.
3. **MU-10:** regenerate the flagpole gap matrix.

### P2 — you or the operator
1. **MU-01:** supply the Release-Grade Workbench Specification.
2. **MU-05:** do the double-click on a Mac, or accept `open` evidence.
3. **MU-02 to MU-04:** Apple signing and notary credentials, the Brave key, the Anthropic key.
4. **MU-06:** portable-lane inputs.

### P3 — decisions
1. **MU-09:** plan and billing decision for branch protection on a private repo.
2. **MU-12:** federation execution environment.

### P4 — longer horizon
1. **MU-07:** close the release-scope residue.
2. **MU-08:** coding governance.
3. **MU-10:** flagpole V-gaps.
4. **MU-13:** branch cleanup.

## Not verifiable with the access used for this audit
- Code-scanning and Dependabot security-alert inventories.
- Repository billing and plan settings.
- Actions secrets (credential presence is taken from `current_state_snapshot_v1.json`).
