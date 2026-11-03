# GUI interaction and accessibility declaration

This page records the keyboard and accessibility behaviour the release
declares for TGT-036, and the rendered evidence behind it. The checks are
concrete, not subjective. They are exercised by `qa/rendered/rendered_qa.mjs`
at desktop 1440×900, touch tablet 820×1180, and narrow mobile 390×844.

Latest browser evidence (2026-11-03): full Chromium and WebKit runs pass 383/383
checks each at `7c963594c8b450c433c249ab7e5ac3236cb83700`, with 36 axe scans per
engine and zero detected violations. The subsequent accounting hardening at
`48f36a197fcf291959f791d453d4cacd5fcef247` has a separate research-only replay:
45/45 checks and three zero-violation axe scans in each engine. Those later
receipts are explicitly PARTIAL, not a full browser rerun. All four receipts
have identical frontend inputs, built assets, and served-index hashes.
Backend accounting enforcement, its error category, a certification diagnostic,
and tests changed after the full browser baseline; whole backend-tree identity
is not claimed. The later source passes 29/29 local release gates with 1,773
tests passed and 8 skipped, plus independent fresh-clone and source-ZIP checks.
See `_architecture/release/evidence/web_research_checkpoint_48f36a197fcf.json`
for receipt paths and hashes. Earlier checkpoints below retain historical scope.
Native/device/full-manual-review boundaries remain open.

The historical full browser checkpoint `622e9c48fb948bf816245203fb8316df3a90e83e`
is superseded for current browser claims:
321/321 checks pass independently in Chromium 141.0.7390.37 and WebKit 26.0.
Each engine ran all three viewport classes and 30 axe scans with zero detected
violations. The receipts are
`_architecture/release/evidence/rendered_qa_chromium_622e9c48fb94.json` and
`_architecture/release/evidence/rendered_qa_webkit_622e9c48fb94.json`. Earlier
checkpoint references below retain their historical scope. Automated browser
checks do not establish full WCAG conformance, native WKWebView, VoiceOver, or
physical-device behavior.

A separate check at `4019745eb7de` sends actual emulated touch events through
the Artifacts library in Chromium and WebKit at tablet and narrow-mobile sizes.
All four profiles pass, observe touch events, and return the recorded bytes with
one download request per tap. Its receipt is
`_architecture/release/evidence/artifact_touch-4019745eb7de.json`. Repeat it with
`node qa/rendered/artifact_touch_qa.mjs`; it remains distinct from a physical
device or native-window test. That checkpoint also passed 28 local release gates
(1,704 tests passed, 8 skipped) and fresh-clone installation/CLI/launcher checks.
Their exact-source receipts are retained alongside it. Later documentation and
test-runner changes require an explicit application-byte comparison before this
proof is reused.

| Behaviour | How it is verified |
|---|---|
| **Frozen-source research.** Source selection binds ID and SHA, default synthesis stays offline, differences and exact citations are inspectable, and reports download through byte verification. Pending repeated activation is fenced; old results and drafts survive navigation. Changed metadata cannot silently accept a new source digest. | Both full 383-check runs and both later 45-check research-only replays exercise actual local HTTP captures after their servers stop. Desktop/tablet/mobile cover activation, duplicate-source refusal, one persisted execution, difference inspection, numbered citation display, exact excerpts/hash inspection, verified download, no-context recovery, navigation, stale metadata, and revocation. Latest representative desktop/tablet/mobile screens were visually inspected. Live-model semantics, native and physical-device proof remain open. |
| **Capture recovery.** Sources offers an explicit ID/hash form, retains its draft references, rejects stale bytes and blocks duplicate pending activation. A successful result states that the saved observation was indexed without a new fetch. | Full browser runs use real API recovery with authored frozen files; all three viewport classes exercise wrong-hash refusal, one pending request, exact saved-file/index accounting, draft retention, and unchanged index bytes on replay. Tablet/mobile use actual emulated touch events. Each profile receives an axe scan. |
| **Drafts during acknowledgement.** Typing the next draft while a previous send is pending must not lose edits. | A real delayed message response verifies that a later draft revision remains after acknowledgement in both browser engines. A synchronous in-flight guard also fences repeated sends before the next render. |
| **Empty conversation scrolling.** The empty conversation list remains keyboard reachable even when its scroll geometry has no focusable rows. | The list has an explicit tab stop and the recovery/empty-library screens pass the formerly failing scrollable-region accessibility check. |
| **Recover saved outputs.** Filter, inspect direct parents, page, refresh, and download use labeled one-click controls. Pending operations refuse duplicate activation. A failed refresh retains the last snapshot and explains recovery; an unmatched filter has an actionable empty state. | Both full browser receipts exercise actual saved GeoTIFF downloads against the recorded artifact SHA, one request under a double-click, navigation away/back, refresh failure/retry and empty filtering at all three viewport classes. The library has an axe scan per profile. Representative WebKit tablet/mobile screenshots are retained alongside the receipts. |
| **Conversation exports.** Cards show persisted states and expose existing answer formats. Downloads use the explicit answer identity; older runs are reachable through a paging button. | Both engines download and inspect an actual answer trace from the Artifacts screen at each viewport. Missing/absent answer exports have no download controls. `test_run_summaries_bind_export_controls_to_the_actual_answer_id` verifies the backend binding and removal of a missing format. |
| **WebKit focus and touch controls.** Pointer activation focuses a button before a sheet mounts, preserving its opener without consuming the primary click. Touch select controls retain a themed dropdown affordance and measure at least 44px high. | Both full receipts pass first-click sheet opening, Escape focus return, field-size and touch-target checks; the old WebKit focus/size failures remain historical, superseded observations. |
| **Send from the keyboard.** With a hardware keyboard, Enter sends and Shift+Enter inserts a new line. On a touch screen, whose keyboard has no Shift+Enter, Return inserts a new line and the Send button (or Ctrl/Cmd+Enter) sends. An IME composition never submits. | The rendered checks "Shift+Enter keeps composing", "with a hardware keyboard Return sends (M4)", "on a touch keyboard Return adds a line and sends nothing (M4)" and "Ctrl+Enter still sends from a touch device (M4)". IME behaviour is verified in code only (`isComposing`, keyCode 229). |
| **No empty send.** Send stays disabled while the message is empty. | The rendered check "empty send is disabled". |
| **Skill lifecycle.** Mount, authorize, invoke, and deactivate use explicit buttons with one-click primary actions. Inactive skills require reauthorization. Results remain inspectable. Pending actions disable duplicate activation. | Desktop and emulated iPhone rendered checks cover deactivation, reload persistence, reauthorization, receipt preservation, and one invocation request under a rapid double-click. Skills receives an axe scan at both widths. Physical touch and assistive-technology behavior remain unverified. |
| **Governed OCR.** Image/PDF settings use labeled inputs and a select. One Invoke action extracts text locally; conversation execution requires an explicit approval. Settings survive navigation. A failed list refresh offers Retry loading skills without invoking the skill again. Extracted document text is identified as untrusted evidence. | The 189-check receipt at clean source `69db9b384074` exercises a frozen table-image fixture through real Tesseract, verifies expected text, approval, retained settings, and recovery with zero replayed invocation requests at both widths. The narrow OCR result screen was visually inspected; broader document quality and native/device operation remain open. |
| **Sheets behave as modal dialogs.** Tool activity, Evidence, Background tasks, Rename, Delete, and the confirmations for removing a source, deleting a memory and revoking folder access use `role="dialog"` and `aria-modal`. Focus is trapped inside and starts on the field or the safe choice (Keep it). It is not reset while the app re-renders. Escape closes the sheet and focus returns to the control that opened it. | The rendered checks "Escape closes the sheet and returns focus", "Rename takes keystroke-by-keystroke typing and keeps focus (M1)", "focus inside an open sheet is not reset in the background (M1)" and the three "asks first" checks (M5). |
| **Skip link.** A "Skip to content" link is the first focusable element. | Source (`App.jsx`) and axe scans. |
| **Visible focus.** Focus shows a 3px `:focus-visible` outline in both light and dark themes. | `styles.css`. |
| **Live announcements.** A `role="status"` `aria-live="polite"` region announces sent, queued, approval-needed, question and completion states. | `App.jsx`, `ChatView.jsx`. |
| **Labelled controls.** Every control has an accessible name. The rendered QA drives the UI through roles and labels only. | axe (WCAG 2.0/2.1 A and AA): zero violations across the empty, answer, sources and settings views at both widths. |
| **Reduced motion.** Animations are disabled when the user asks for reduced motion. | The `prefers-reduced-motion` rule in `styles.css`. |
| **Colour scheme.** Light and dark themes follow the system preference. | The `prefers-color-scheme` tokens in `styles.css`. |
| **Phone widths.** Navigation opens off-canvas. Top and bottom safe-area insets are honoured; left and right insets are not yet, which matters in landscape on a notched iPhone (finding M9 in `docs/UX_SETUP_IOS_AUDIT.md`). Sheets open from the bottom. The page never overflows horizontally. | The rendered checks "no horizontal page overflow" and "iphone-12-nav-open". |
| **Governed GIS.** Explicit capability choices authorize local vector tasks. Labeled controls reveal reference/CRS fields when relevant. Results show counts, CRS, readable warnings, optional diagnostic codes, and one-click JSON saving. | The 199-check receipt at `7c5df71c9231` inspects the frozen USGS layer at both widths, preserves its Z/planar-topology warning, and verifies downloaded source identity. Fourteen axe scans detect zero violations. The narrow result screen was visually inspected after wording corrections; other operations have separate real-engine tests. |
| **Raster and hydrology.** Coordinate rows provide named X/Y fields, explicit CRS, and one-click add/remove actions. Numeric controls expose only relevant operation settings. Results distinguish valid values, NODATA, zero, outside points, and unresolved D8 states. Output downloads use recorded artifact IDs. | The 214-check desktop/mobile receipt at `385bc85f8caa` and scoped 78-check tablet receipt at `adbb1ca0576a` exercise real GeoTIFF inspection, known-cell sampling, preserved outside states, point removal, D8 accounting, and a byte-verified derived download. All three classes receive raster-control and raster-result axe scans. |

Rendered receipts from `7c5df71c9231` onward build the frontend before testing,
record input/asset hashes, compare served and built page bytes, and check
source/asset stability at the end. A source commit alone does not establish
which frontend bytes were rendered. Reuse earlier receipts only within their
recorded scope and build evidence.

The historical raster-only tablet replay is explicitly `RENDERED_QA_PARTIAL`: its ancillary network,
streaming, sharing, launch, and specialized-workspace scenarios were not rerun.
It supplements the complete desktop/mobile run only because all 347 backend/
frontend Git entries, frontend input manifests, and built asset hashes match.
These two receipts contain 27 automated scans with zero detected violations.
The tablet and narrow raster result screens were visually reviewed for readable
counts, warnings, controls, and overflow. This is bounded review evidence;
native rendering, physical touch, software keyboards, assistive technology,
and the complete application-wide design review remain open.

## Not yet verified

These items remain PROVISIONAL.

- **VoiceOver.** Behaviour with VoiceOver and native WKWebView rendering inside
  the packaged macOS app needs a macOS runner or device (see M7). The QA
  renders in Chromium and WebKit; it does not exercise the packaged native window.
- **iOS keyboard.** Keyboard avoidance with the iOS software keyboard cannot be
  exercised in Chromium emulation.
- **Phone access.** Signing in from a physical iPhone over a real network is not
  observed (see `docs/PHONE_ACCESS.md`, "What is verified").
