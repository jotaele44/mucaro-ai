# Usability, Setup, and Mobile Safari Audit

Date: 2026-09-25
Scope: first-run setup (bootstrap, launcher, `.env`), browser UI usability, and
iOS/iPadOS Safari rendering of the local web UI.
Out of scope by decision: a LAN-serving mode or native iOS app. The backend
stays loopback-only by default; nothing about network exposure changes.
*Superseded 2026-10-02 for the LAN part: phone access is now a supported mode
(`SD-012`, `docs/PHONE_ACCESS.md`). A native iOS app stays out of scope (`SD-010`).*

## Method

1. Static read of the setup path (`scripts/bootstrap_*`, `scripts/preflight.py`,
   `scripts/launch_app.py`, `scripts/dev_start.py`, `Makefile`, `README.md`,
   `docs/FIRST_RUN.md`) and the whole UI (`frontend/`).
2. Trace of each UI call into the backend (`backend/app/main.py`,
   `services.py`, `model_router.py`, `config.py`).
3. Live run: sample ingestion, `scripts/launch_app.py`, then scripted browser
   checks with iPhone 13, iPad (7th gen), and 1280x800 desktop profiles
   (Chromium engine with Playwright device descriptors).
4. Contradiction testing on every finding that looked like it might be a
   harness artifact (below).

Evidence tiers: **T1** = code or measurement in this repo; **T4** = platform
behavior documented elsewhere and not reproduced on real hardware here.

## Findings

Severity is the impact on a first-time user. Confidence is the probability
that the finding is real as stated.

| ID | Finding | Evidence | Tier | Conf. | Sev. | Status |
|---|---|---|---|---|---|---|
| U1 | UI hard-coded its API origin to `http://127.0.0.1:8000`, so the built UI broke whenever it was served from another origin or port | `frontend/src/api.js` | T1 | 95% | High | Fixed: production build uses the page's own origin; dev server keeps `127.0.0.1:8000`; `VITE_API_BASE` still overrides |
| U2 | UI always sent `model_provider: "mock"`, overriding the configured default provider | `App.jsx` ask payload vs `services.py` `model_provider or cfg.default_model_provider` | T1 | 98% | High | Fixed: field omitted, so the backend default applies |
| U3 | Backend-offline state was invisible on phones and tablets: load errors went only to the nav status line, which is hidden below 900px, and the status dot was always green | `App.jsx`, `styles.css` `.nav-health { display: none }` | T1 | 95% | High | Fixed: full-width "Backend not reachable" alert with start command and Retry; dot is amber while connecting and red when offline |
| U4 | Planning Gate swallowed errors (no try/catch; `error` was never set) | `PlanningGate.jsx` | T1 | 98% | Med | Fixed: errors shown, busy state added |
| U5 | Deleting a chat, source, or memory item took one tap and could not be undone | `App.jsx` delete handlers | T1 | 95% | Med | Fixed: confirmation dialog |
| U6 | Ask button allowed double submission; no keyboard send; question stayed after sending | `App.jsx` composer | T1 | 90% | Low | Fixed: busy state, Cmd/Ctrl+Enter, composer clears on success |
| U7 | File input had no label and no `accept` list, so the iOS picker offered file types the server rejects | `App.jsx` upload vs `main.py` `SUPPORTED_UPLOAD_SUFFIXES` | T1 | 90% | Med | Fixed: labelled picker, `accept` mirrors server list, supported-types hint |
| U8 | Unlabelled chat search and memory fields; source rows worked by mouse only | `App.jsx` | T1 | 90% | Low | Fixed: `aria-label`s; rows focusable and selectable with Enter or Space |
| U9 | Directory Access did not say the path refers to the computer running the backend; iOS auto-capitalized and auto-corrected paths | `DirectoryIntake.jsx` | T1 | 85% | Low | Fixed: host-path copy, `~/` placeholder (the backend expands `~`), autocapitalize and autocorrect off |
| U10 | Export links left the single-page app, so mobile Safari lost the current chat state | `App.jsx` export links | T1 | 80% | Low | Fixed: links open in a new tab |
| I1 | Form fields had no explicit 16px size; iOS Safari zooms when focusing a field smaller than 16px | `styles.css` | T4 | 85% | Med | Fixed: 16px fields below 900px or on coarse pointers; measured 0 undersized fields on all views |
| I2 | `100vh` layouts are affected by iOS Safari's dynamic toolbar | `styles.css` | T4 | 85% | Low | Fixed: `100dvh` added after each `100vh` fallback |
| I3 | Touch targets were 35-37px, under the 44pt HIG minimum; `:hover` styles stuck after a tap | measured | T1 | 90% | Low | Fixed: 44px minimum below 900px or on coarse pointers; hover styles gated on `(hover: hover)` |
| I4 | No `format-detection`, `color-scheme`, `theme-color`, or favicon; iOS can turn byte counts and IDs into phone links; the missing favicon caused a 404 | `frontend/index.html` | T4 | 80% | Low | Fixed |
| I5 | **Page overflowed horizontally on phones.** The shell's `1fr` track was floored at the nav strip's min-content (about 596px), and the workspace's `auto` column at the 720px source table. Measured layout width on a 390px iPhone: 610px (Chat) and 776px (Sources) | measured `innerWidth` vs `clientWidth` | T1 | 97% | High | Fixed: `minmax(0, 1fr)` tracks; measured 390/390 on every view |
| S1 | `.env` was created by bootstrap and preflight, but nothing ever read it, so settings users entered there had no effect | no loader in `backend/` or `scripts/` | T1 | 98% | High | Fixed: `backend/app/env_file.py`, used by `launch_app.py` and `dev_start.py`; shell-exported values win; blank values are ignored |
| S2 | Default Anthropic model `claude-3-5-sonnet-latest` is retired, so live mode fails out of the box | `config.py`, `model_router.py`, `.env.example` | T1 | 95% | High | Fixed: `claude-sonnet-5` (same tier); regression test keeps config and router defaults in sync; preflight prints `ENV_MODEL_RETIRED` when an existing `.env` still pins the old id (now that `.env` is loaded, it would override the new default) |
| S3 | macOS bootstrap used the first `python3` it found (often older than 3.11 on macOS) and masked the version error, which surfaced later as a cryptic pip failure | `bootstrap_macos.sh` | T1 | 90% | High | Fixed: probes 3.13, 3.12, 3.11, then `python3`; `ERROR_PYTHON_TOO_OLD` with install hint; recreates a too-old `.venv` |
| S4 | macOS bootstrap rewrote the user's global npm registry setting | `bootstrap_macos.sh` | T1 | 95% | Low | Fixed: the registry is pinned per project only |
| S5 | Windows bootstrap required exactly Python 3.11; PowerShell 5.1 does not stop on native-command failures, so `BOOTSTRAP_OK` could print after a failed install | `bootstrap_windows.ps1` | T1 | 90% | Med | Fixed: `py -3` with a 3.11+ check; every native step goes through `Invoke-Checked` |
| S6 | `PYTHONPATH` was joined with `:` instead of `os.pathsep`, which breaks Windows paths | `release_check.py`, `certify_release.py` | T1 | 98% | Med | Fixed; the regression test simulates `;` and fails on the old code |
| S7 | Bootstrap output and README led with the two-terminal dev flow instead of the one-command launcher | bootstrap scripts, `README.md`, `docs/FIRST_RUN.md` | T1 | 90% | Low | Fixed: Quick start, `make start`, `NEXT_START` marker |

## Verification (this change set)

| Check | Result |
|---|---|
| `pytest backend/tests` | 673 passed (about 40s) |
| Browser checks (iPhone, iPad, desktop, offline) | 62/62 pass: no horizontal overflow on any view, fields ≥ 16px, touch targets ≥ 44px, cited answer returned, composer clears, delete asks for confirmation, offline alert appears and Retry recovers, no console errors |
| `scripts/launch_app.py --smoke-test --no-browser` | `DESKTOP_SMOKE_OK`, port closed |
| `scripts/validate_json.py` | `JSON_OK checked=34` |
| Source-boundary gates (prior names, secret patterns, large files, generated artifacts) | all PASS |
| `npm run build` / `npm audit --audit-level=moderate` | built / 0 vulnerabilities |
| `bash -n scripts/bootstrap_macos.sh` | syntax OK |

## Contradiction testing

- **Overflow (I5).** The first overflow check compared `scrollWidth` with
  `innerWidth` and passed, but mobile browsers widen the layout viewport to fit
  overflowing content. Comparing against the device `clientWidth` (390)
  exposed the real 610/776px layout. The check now requires both to be ≤
  `clientWidth`.
- **Touch targets (I3).** Early runs reported 37px. Diagnosis: after any
  synthetic click or tap, the Chromium emulator stops matching
  `(pointer: coarse)`. On a fresh load the rule computes to 44px. To avoid
  depending on the pointer query alone, the rule also applies to widths up to
  900px. This is a harness artifact, not a product defect, but the hardening
  stands.
- **`.env` (S1).** Confirmed by search: before this change `.env` appeared only
  in `preflight.py` (creation) and `certify_release.py` (exclusion list).
- **PYTHONPATH (S6).** Plain Linux cannot tell `:` from `os.pathsep`; the test
  patches the separator to `;` and was confirmed to fail on the previous code.
- **Model swap (S2).** The adapter sends only `model`, `max_tokens`, and one
  user message. It uses no `temperature`, `budget_tokens`, or prefill, all of
  which Sonnet 5 rejects.

## Blind spots

- No real iPhone or iPad and no WebKit engine was available. Mobile results
  come from Chromium with iOS device descriptors (viewport, DPR, touch, user
  agent). iOS-specific behavior such as focus zoom, `dvh`, and data detectors is
  T4.
- `bootstrap_windows.ps1` was not executed (no PowerShell available). Changes
  were limited to well-known PowerShell 5.1 semantics.
- The live Anthropic path was not exercised (no API key in the audit
  environment).
- `bootstrap_macos.sh` was syntax-checked but not run end to end on macOS.
- An iPad wider than 900px using a trackpad may report a fine pointer and
  therefore get desktop sizing.

## Queued leads (not changed here)

1. **Live answer length.** `model_router.py` sends `max_tokens=900`. Sonnet 5
   runs adaptive thinking by default, so check answer completeness with a live
   key and raise the limit or tune effort if answers truncate.
2. **Stale UI after `git pull`.** `launch_app.py` rebuilds `frontend/dist` only
   when it is missing, so an old build can be served after source changes.
3. **Version mismatch.** The FastAPI app reports `0.1.21`
   (`backend/app/main.py`); the package is `0.1.23`.
4. **HEIC/HEIF photos.** The iPhone's default photo format is not in the
   accepted media list (`main.py`, `multimodal.py`). Safari usually converts to
   JPEG on upload (T4), but not always.
5. **Raw status codes.** The UI shows machine codes such as `ASK_ERROR` and
   `INGEST_OK`; a human-readable mapping would help non-operators.
6. **Global npm config.** `repair_npm_registry.sh` still sets the global npm
   registry, which is acceptable for an explicit repair tool.
7. **Newest Python releases.** Pinned geospatial wheels (rasterio, pyproj,
   shapely) may lag them. The bootstrap now prefers 3.13 to 3.11 before a
   generic `python3` (not verified on 3.14).
8. **Home-screen app.** A standalone PWA would need `viewport-fit=cover` plus
   safe-area padding.

## Network access (corrected 2026-10-02)

The backend is loopback-only unless phone access is switched on; see
`docs/PHONE_ACCESS.md`. That document supersedes this section's earlier advice
to "enter the operator token under Settings on the device": a token-protected
service now shows an **Enter the access token** screen, because the Settings
workspace was never reachable without a token.

## Follow-up (2026-09-28)

Re-audited setup against `main` at `64a097b`, after the double-click setup
(`Mucaro AI.command` / `Mucaro AI.cmd`, #118) landed. That path pins its own
Python 3.12 and Node and passes on clean macOS arm64, macOS Intel, Linux, and
Windows runners (`.github/workflows/one-click-setup.yml`). This supersedes the
"`bootstrap_windows.ps1` was not executed" blind spot for the primary path. The
developer bootstrap scripts are still not run in CI.

| ID | Finding | Evidence | Conf. | Status |
|---|---|---|---|---|
| S8 | Developer bootstrap accepted any Python ≥ 3.11, but pinned `rasterio==1.4.3` publishes wheels only through CPython 3.13, so a machine with only 3.14 failed inside `pip install` on a GDAL source build | PyPI wheel tags for rasterio 1.4.3: cp39–cp313 | 97% | Fixed: both bootstraps select 3.11–3.13 (`ERROR_PYTHON_UNSUPPORTED` / `ERROR_PYTHON_UNSUPPORTED_OR_MISSING`, pointing to the double-click setup); preflight warns `PYTHON_UNTESTED` on 3.14+ |
| S9 | README kept a third, stale setup route (unbounded `python3 -m venv`, `py -3.11`, `npm install`, which can rewrite `package-lock.json` and fail the `worktree_clean` gate) | `README.md` | 95% | Fixed: one bounded manual-install section using `npm ci` |
| S10 | `python scripts/launch_app.py` / `make start` served a stale UI after `git pull`: it rebuilt `frontend/dist` only when missing | `scripts/launch_app.py` | 95% | Fixed: rebuilds when any build input is newer than `dist/index.html` (warns if it cannot); the double-click setup passes `--frontend-verified` because it fingerprints the same inputs |
| S11 | `repair_npm_registry.sh` still changed the user's global npm registry | `scripts/repair_npm_registry.sh` | 95% | Fixed: the project `.npmrc` and `--registry` flag pin it |

Still open, and not code work: the signed and notarized installer (BLK-SIGN-001,
needs an Apple Developer ID); live-mode answer length with a real API key
(queued lead 1); `mucaro serve` and raw `uvicorn` not reading `.env`
(documented).

## Follow-up (2026-10-02): mobile web re-audit

Re-audit of the responsive web UI at `f2ee905`, after #110 replaced `frontend/src`
with the conversation-first UI. Method: static reading of all of `frontend/src`,
then live measurement in Chromium 141 device emulation against a real backend,
with a small fault-injecting proxy for lost responses, silent streams and
refused streams. Evidence tiers as above; there is no T3 source. **Not
observed:** a physical iPhone or Android phone, or an on-device Safari/WebKit
session. GitHub-hosted runner admission later recovered
(`BLK-ACTIONS-001` is RESOLVED); hosted WebKit execution is therefore
available evidence, but it is not physical-device evidence.

### Regression ledger: the 2026-09-25 findings against the current UI

#115 fixed the pre-rebuild UI; #110 then replaced it, so the "Fixed" statuses
above could not be assumed. Counts at those commits: `100dvh` 3→0,
`hover: hover` 1→0, `PlanningGate.jsx` 1→0.

| Prior ID | At `f2ee905` | After this change |
|---|---|---|
| U1 same-origin API | Holds | Holds |
| U2 provider not forced to mock | **Regressed**: `mock` is persisted and sent with every message (`App.jsx`, `ChatView.jsx`) | Unchanged (queued lead L1) |
| U3 offline alert on phones | Holds, but latched until Retry and mislabelled a 401 as "not reachable" | Reworked (M2, M7) |
| U5 confirm deletes | **Partial**: conversation only | Fixed again for sources, memory and folder access (M5) |
| U6 double submit, composer | Holds; Return sent on touch keyboards | Fixed (M4) |
| U7 file `accept` | **Regressed** | Unchanged (M17) |
| U9 host-path copy | Holds; the component has no styles | Unchanged (M16) |
| I1 16 px fields | Holds | Holds |
| I2 `100dvh` | **Absent**: sheets use `vh` | Unchanged |
| I3 44 px targets, hover gating | Height only; hover not gated | Unchanged (M15, M24) |
| I4 meta tags | Holds | Holds |
| I5 no horizontal overflow | Holds through `.main{min-width:0}` | Holds |

### Findings

Confidence is the probability that the finding is real as stated, after
measurement where one was possible.

| ID | Finding | Measured | Conf. | After this change |
|---|---|---|---|---|
| M1 | `Sheet` re-ran its focus effect on every render | Typing "abc" in Rename left "a" with focus on ✕; focus in Background tasks was reset within 5.5 s | 95% | **Fixed** |
| M2 | The health watchdog latched into an error state, re-rendered the tree every 4 s and had no timeout | 5 React commits in 12 idle seconds; one failed `/health` unmounted the chat for good; 3 hung polls at once | 97% | **Fixed** |
| M3 | The event stream had no idle or lifecycle handling | The server run completed while the page still showed `AWAITING_APPROVAL` 45 s later | 90% | **Fixed** (25 s idle watchdog; reconnect on visibility, page show and online) |
| M3b | No backoff when a stream route fails | 655 stream attempts and 3,275 requests in 10 s against a 502 | 95% | **Fixed** (backoff, then paused with a Resume button) |
| M4 | Return sent on touch keyboards, which have no Shift+Enter | The iPhone profile sent on Return | 95% | **Fixed** |
| M5 | Destructive actions had lost their confirmation | No dialog for source removal or memory delete | 97% | **Fixed** |
| M6 | `request()` threw a raw `TypeError` on a non-JSON error body | A plain-text 500 showed "body stream already read" | 99% | **Fixed** |
| M7 | A phone had no way to enter a token; a public bind skipped the bind guard; `/health` and `/config` disclosed host paths | `uvicorn --host <network address>` served `/conversations`, `/memory/export` and a `POST /memory` without a token | 95% | **Fixed**, as a supported mode (`SD-012`) |
| M8 | Landscape phones get the desktop layout | Conversation list 0 px high at 844×390, 844×340 and 915×412; nav clipping only at 340 px height | 90% | Open (P1) |
| M9 | No left or right safe-area insets under `viewport-fit=cover` | Content at x=12 under a 47 px inset | 95% | Open (P1) |
| M10 | Drawer and menu semantics | 13 of 14 Tab stops off-screen; menus survive an outside tap and Escape | 92% | Open (P1) |
| M11 | The back gesture leaves the app | Back left the app | 95% | Open (P1) |
| M12 | A new idempotency key on every send | One intent produced 2 conversations and 2 runs after a lost response | 90% | Open (P1) |
| M13 | Approval arguments scroll sideways; Deny and Allow once are 8 px apart | About 40 of 84 characters of a URL visible | 85% | Open (P1) |
| M14 | A long unbroken title pushes the ⋯ menu off-screen | ⋯ at x=678..718 on a 390 px screen | 90% | Open (P1) |
| M15 | Undersized touch targets | Narrower than first reported: icon buttons 38×44 and 37×44, `summary` 19.5 px, the Memory on/off label 21 px | 90% | Open (P2) |
| M16 | `DirectoryIntake` has no styles | 11 classes with no rules | 95% | Open (P1) |
| M17 | Upload: `accept` dropped, no size check or progress, no HEIC | Confirmed in code | 90% | Open (P1) |
| M18 | Data volume: event-log refetch, all chunks, no gzip, no cache headers | JS served uncompressed (about 262 kB, versus about 80 kB gzipped) | 85% | Open (P2) |
| M19 | Per-token cost grows quadratically | **Refuted:** linear, 1.1 ms per event at 100 events and 1.2 ms at 900, under 4× CPU throttle | 90% | Not a problem |
| M20 | SSE pins a thread in the shared pool | `/health` median 3 ms with 20 streams open, 9.9 s with 45, 19.8 s with 70 | 90% | Open (P1) |
| M21 | No error boundary; a malformed `%` in the token fragment threw at load | `/#token=%E0%A4%A` blanked the app | 90% | Fragment guard **fixed**; the error boundary stays open (P2) |
| M22 | Unhandled rejections, missing busy states, duplicate rows on double-tap | Confirmed in code | 85% | Open (P2); Rename and Delete now show errors and busy states |
| M23 | A stored `mucaro.activeConversation` is never cleared on a 404 | Confirmed | 90% | Open (P2) |
| M24 | Visual and platform polish | Mixed | 60-90% | Open (P2) |
| M25 | `navigator.clipboard` is undefined on `http://<network address>`; Copy blamed a permission and had no fallback | Wrong reason announced | 95% | **Fixed** |

### What this change adds

- The fixes for M1-M7, M21 (fragment guard) and M25, with 37 new checks in
  `qa/rendered/rendered_qa.mjs` (130 in all, every one passing in Chromium 141).
  Run against the base commit's UI and backend, 24 of the 37 fail, 9 cannot run
  because the feature they exercise (the token gate, the Resume button) does not
  exist there, and 4 guard behaviour that already worked and pass on both:
  Ctrl+Enter on a touch device, the token deciding over the network, the link
  fragment, and Copy's fallback (which #176 had already added for messages).
  Run them alone with `node rendered_qa.mjs --only-new`.
- Phone access (`docs/PHONE_ACCESS.md`, `SD-012`, PROVISIONAL).
- The 2026-09-25 findings above are re-stated against the current code; the
  claims in `docs/release/UI_ACCESSIBILITY.md` about the Return key and safe-area
  insets were corrected.

### Still open, and needing a person with a phone

Rename typing and keyboard avoidance on an iPhone, landscape layout and the
notch, edge-swipe back, HEIC upload, VoiceOver through a sheet, resume after a
60 s lock with a pending approval, and signing in with the phone link.

### Queued leads (not changed here)

- **L1** The default `model_provider: "mock"` overrides a server-configured provider (U2).
- **L2** `GET /work-runs/{id}/events` returns at most 500 events while the client replaces its list, so a long streamed run may lose its tail.
- **L3** `/security` and `/tools` are public; review `PUBLIC_API_PATHS`.
- **L4** An upload with an existing name overwrites `data/input/<name>`; check it against source lineage.
- **L5** Settings offers retrieval strategies beyond the README's set.
- **L6** The archived rendered-QA receipt is bound to an older commit.
- **L7** The content security policy allows `style-src 'unsafe-inline'`, which may be unnecessary.
- **L8** `frontend/package.json` has no lint or test scripts.
- **L9** A pairing code exchanged for a per-device token, revocable in Settings, would replace typing a 43-character token.
- **L10** The token check has no failed-attempt throttling.
- **L11** A QR code for the phone link needs a dependency decision (the runtime graph is hash-locked).
- **L12** Whether WebKit's device emulation reports `(pointer: coarse)` is unknown; the touch-keyboard check forces the report so it does not depend on it. With no executing CI runner there is no WebKit evidence at all.
- **L13** On an iPad with a hardware keyboard the primary pointer is still coarse, so Return adds a line (Cmd or Ctrl+Enter, or the Send button, sends). `any-pointer: fine` could refine this, but its behaviour on iPadOS is unverified.
