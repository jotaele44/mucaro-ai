# GUI interaction and accessibility declaration

This page records the keyboard and accessibility behaviour the release
declares for TGT-036, and the rendered evidence behind it. The checks are
concrete, not subjective. They are exercised by `qa/rendered/rendered_qa.mjs`
at 1440×900 and 390×844.

| Behaviour | How it is verified |
|---|---|
| **Send from the keyboard.** Enter sends. Shift+Enter inserts a new line. An IME composition never submits. | The rendered check "Shift+Enter keeps composing". IME behaviour is verified in code only (`isComposing`, keyCode 229). |
| **No empty send.** Send stays disabled while the message is empty. | The rendered check "empty send is disabled". |
| **Sheets behave as modal dialogs.** Tool activity, Evidence, Background tasks, Rename and Delete use `role="dialog"` and `aria-modal`. Focus is trapped inside. Escape closes the sheet and focus returns to the control that opened it. | The rendered check "Escape closes the sheet and returns focus". |
| **Skip link.** A "Skip to content" link is the first focusable element. | Source (`App.jsx`) and axe scans. |
| **Visible focus.** Focus shows a 3px `:focus-visible` outline in both light and dark themes. | `styles.css`. |
| **Live announcements.** A `role="status"` `aria-live="polite"` region announces sent, queued, approval-needed, question and completion states. | `App.jsx`, `ChatView.jsx`. |
| **Labelled controls.** Every control has an accessible name. The rendered QA drives the UI through roles and labels only. | axe (WCAG 2.0/2.1 A and AA): zero violations across the empty, answer, sources and settings views at both widths. |
| **Reduced motion.** Animations are disabled when the user asks for reduced motion. | The `prefers-reduced-motion` rule in `styles.css`. |
| **Colour scheme.** Light and dark themes follow the system preference. | The `prefers-color-scheme` tokens in `styles.css`. |
| **Phone widths.** Navigation opens off-canvas. Safe-area insets are honoured. Sheets open from the bottom. The page never overflows horizontally. | The rendered checks "no horizontal page overflow" and "iphone-12-nav-open". |

## Not yet verified

These items remain PROVISIONAL.

- **VoiceOver.** Behaviour with VoiceOver and native WKWebView rendering inside
  the packaged macOS app needs a macOS runner or device (see M7). The QA
  renders in Chromium.
- **iOS keyboard.** Keyboard avoidance with the iOS software keyboard cannot be
  exercised in Chromium emulation.
