# Múcaro-AI GUI Draft Integration — bounded execution record v1

**Source snapshot frozen 2026-09-30 UTC.** This document describes source-level implementation work added to draft PR #148; it does not establish rendered/UI, device, backend-integration, or native-release certification.

## Source identities and archive audit

| Input | SHA-256 / evidence |
| --- | --- |
| User uploaded `Mucaro AI.zip` | `20047e9421b6eeb9329a2e6f39a9da7d39f5bf2ab39303815cceb41692851bd6` |
| User uploaded owl JPEG | `783e24be90f11ed800199cc0adb1d45f4c37ba2dc4cc8175864ebd521fdbec16` |
| Original archive payload canonical serialization | `bd10e542bdfba30fd0099201d929d7e895e1d81b2937dde625d50d6382337231` |
| Separate frozen local member-manifest JSON | `aba91b8fd65c9939fe36f77b68d2d9cfac0d704f5abd660edfb2f318624ad8ea` |
| Current local source audit receipt v2 | `c63cfb22ce3202fbb951e5c013f899ad8c4860d5535f34e52de7c6af5004e80f` |
| GitHub bundled derived brand icon | `3be31e5aa096458a7534f855393a4e9637889db5999782397ceaeaa30c485d02` |

The ZIP had **143 directory entries including 137 files and 6 directories**, and the source-audit CRC, individual member hashes, size/path guards, and canonical member digest passed in the current bounded snapshot. The locally generated member manifest and receipt are evidence files, not source archives; regenerated receipts cannot prove historical byte identity. Full original brand JPEG remains separate and unmodified. The 64 px WebP rail/favicon derivative is a different byte and resolution identity.

The archive included a 40-frame owl sprite **manifest** but not its animated atlas binary, hosted asset URLs, and two Google Font imports. It declared 46 direct package dependencies. The existing application dependency set is not replaced by that prototype and existing GitHub source remains the functional authority.

## Route equivalence (category comparison only)

- **Draft routes:** chat, browser, sources, access, memory, planning, settings.
- **Existing certified-in-scope route names in PR #148:** chat, sources, memory, provenance, settings.
- **INTERSECTION:** chat, sources, memory, settings.
- **A_ONLY (draft):** browser, access, planning.
- **B_ONLY (PR):** provenance.
- **UNION:** chat, browser, sources, access, memory, planning, provenance, settings.
- **SYMMETRIC_DIFFERENCE:** browser, access, planning, provenance.

These route *names* support only a set comparison; they do not establish functional identity. Draft-only pages are **design candidates**, not installed working routes. The source-authoritative implementation remains Chat/Sources/Memory/Provenance/Settings. Background Tasks stays a drawer; Directory Access remains integrated with existing Sources. The evidence inspector remains the existing source-bound `EvidencePanel`, not the prototype's mock-record inspector.

## Integrated scope

- Adapted teal/ink day and night design tokens without importing hosted fonts or the prototype's 46 new direct dependencies. Adjusted its normal-text muted token to reach WCAG AA contrast: `#647477` versus light surface `#f7f9f8`, computed ratio about 4.61:1 (the draft token would be about 4.41:1).
- Bundled the locally derived owl WebP for offline brand/logo, primary rail, favicon and pet static fallback; no assumption that missing 40-frame sprite atlas exists.
- Preserved PR #148's canonical three-region nav and standalone local-first dependency posture. One explicit external three-line context-sidebar handle; rail icons persist when context is collapsed; mobile overlay remains separate.
- Added validated/persisted local UI preferences for explicit Auto/Light/Dark theme and bounded EN/ES navigation + Settings localization with bottom-rail active-language flag. Workspace/backend content not yet translated is explicitly not claimed bilingual.
- Added a dockable, manually draggable optional static owl with normalized viewport geometry, reduced-motion controls and **observed** local health/run state only. The pet does not claim autonomous roaming or semantic knowledge of backend activities.
- Added truthful owl runtime events to existing ChatView backed by actual run IDs: hydrated historical terminal records do not impersonate new activity; runtime state is per-tab and cleaned up on close. An active run state or new-session stream is required for live signaling.
- Added Appearance within canonical Settings, preserving existing model, retrieval, token and offline-capability panels.
- Added **no** remote font, image CDN, new runtime package, mock workflow, browser dependency or developer-terminal requirement.

## Verification and claims

**PASS — bounded and source/local only:** original ZIP audit on exact provided files; 8 Python archive/integrity regression fixtures; 11 pure-JS GUI preference/owl state fixtures executed with a V8 semantic assertion harness; committed bundle icon derivative hash was independently recalculated from the actual fetched GitHub source and equals its manifest; all 13 contextual nav IDs bound to implemented workspace targets in static inspection; no phantom route registered.

**OPEN — not executed in this environment:** official Node test runner on a fresh checkout, Vite build, full rendered UI under Chromium and WebKit, real iPhone Safari portrait/landscape, service-outage/active streaming end-to-end tests, full accessibility audit, native standalone app packaging and actual no-Terminal launch certification.

**BLOCKED — missing physical source:** real 40-frame animated sprite atlas was not present in user ZIP, so animated-pet equivalence cannot pass. The separately identified static owl fallback is **not** promoted to animated parity.

**KNOWN external issue requiring exact-HEAD adjudication:** previous project evidence recorded `BLK-ACTIONS-001` (GitHub Actions runner admission) on 2026-09-29. Preserve it as a historical blocker, not a claim that runner service remains unavailable now. If exact-head job still executes zero steps, classify as infrastructure BLOCKED rather than code PASS/FAIL.

## Restartable sequence and governance

1. On the exact PR head, run `node --test src/navigation.test.mjs src/uiPreferences.test.mjs` from `frontend`, then `npm ci --prefix frontend` and `npm run build --prefix frontend`. Capture per-command actual exit codes and commit SHA.
2. Run exact-head rendered QA in Chromium and WebKit with separate stored receipts. Do not use simulation of iPhone width as physical Safari evidence.
3. On real Mac/iPhone validate keyboard/screen reader, touch targets, light/dark/flag state, responsive rail, external handle, pet manual drag and geo/position compensation, restart persistence, and late stream/callback transition safety.
4. Run app packaging and fresh-clone → install → test → package → reinstall/rollback checks independently. Confirm app does not expose a Terminal window or launch a browser; those conditions are separate from this GUI integration.
5. Do not merge the draft PR or certify a release until required exact-head gates pass. Do not overwrite or regenerate original source identity. Scope failures narrowly and reuse already frozen successful source-level receipts.

**Skill promotion:** `skills/gui_intake_certifier/SKILL.md` is now a concrete repo-local v1 workflow upgrade, bound to exact local archive/test evidence. This does not imply a ChatGPT runtime skill was installed globally or that this PR is merged.

## Evidence receipt mapping

`scripts/audit_gui_draft.py` — exact GitHub blob SHA-1 `cfa49d6f259e0442f0f32bf764c3de45a0a727b6`.

`scripts/test_audit_gui_draft.py` — exact GitHub blob SHA-1 after explicit original-file SKIP handling `d3b56f3b05437d305d59079bd336e66b55c58b55`.

Both were verified identical to locally executed source versions. Unavailable originals in downstream CI are marked SKIPPED, not source-PASS. The complete source manifest and local audit receipts remain separately versioned files, not inferred from Git commit digests.
