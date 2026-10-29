# GUI interaction and accessibility declaration

This page records the keyboard and accessibility behaviour the release
declares for TGT-036, and the rendered evidence behind it. The checks are
concrete, not subjective. They are exercised by `qa/rendered/rendered_qa.mjs`
at 1440×900 and 390×844.

| Behaviour | How it is verified |
|---|---|
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

Rendered receipts from `7c5df71c9231` onward build the frontend before testing,
record input/asset hashes, compare served and built page bytes, and check
source/asset stability at the end. A source commit alone does not establish
which frontend bytes were rendered. Reuse earlier receipts only within their
recorded scope and build evidence.

## Not yet verified

These items remain PROVISIONAL.

- **VoiceOver.** Behaviour with VoiceOver and native WKWebView rendering inside
  the packaged macOS app needs a macOS runner or device (see M7). The QA
  renders in Chromium.
- **iOS keyboard.** Keyboard avoidance with the iOS software keyboard cannot be
  exercised in Chromium emulation.
- **Phone access.** Signing in from a physical iPhone over a real network is not
  observed (see `docs/PHONE_ACCESS.md`, "What is verified").
