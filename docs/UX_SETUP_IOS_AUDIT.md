# Usability, Setup, and Mobile Safari Audit

Date: 2026-09-25
Scope: first-run setup (bootstrap, launcher, `.env`), browser UI usability, and
iOS/iPadOS Safari rendering of the local web UI.
Out of scope by decision: a LAN-serving mode or native iOS app. The backend
stays loopback-only by default; nothing about network exposure changes.

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

## Existing network opt-in (unchanged)

The backend remains loopback-only. Serving to another device on the network
still requires an explicit opt-in: set `MUCARO_AI_ALLOW_PUBLIC_BIND=1` and a
strong `MUCARO_AI_OPERATOR_TOKEN`, then run `mucaro serve --host <interface>`.
With U1 fixed, the UI served that way sends requests to the host it was loaded
from. Enter the operator token under Settings on the device before using
actions that change data.
