# Mucaro AI for macOS

`Mucaro AI.app` is the release target (GOAL-001). Opening it starts Mucaro's
local service and shows the conversation window. There is no Terminal, browser,
source checkout or manual server start involved.

## What opening the app does

1. The launcher takes a lock on the data directory, so only one copy writes
   your data at a time.
2. It starts the local service on `127.0.0.1` with a random port and a fresh
   access token. The service does not listen on any network interface.
3. It opens a native window (WKWebView) on the app. The token travels in the
   URL fragment, which is never sent over HTTP, and the GUI removes it from the
   address as soon as it loads.
4. Closing the window or quitting (Cmd+Q) stops the service. A signal such as
   `kill -TERM` quits the same way.

Your data lives in `~/Library/Application Support/Mucaro AI`, never inside the
app bundle. That folder holds sources, conversations, memory, receipts, logs
(`logs/service.log`, `logs/startup-error.log`) and `instance.json`. Replacing
or reinstalling the app keeps it. Deleting the app does not delete it; remove
the folder yourself if you want your data gone.

## Requirements and limits

- A Mac with Apple Silicon (arm64) running macOS 14 or later. The minimum was
  raised from 13 because the pinned GIS libraries publish arm64 wheels only
  for macOS 14 and later (RC-008).
- The app includes Python, the GUI, the schemas and the GIS libraries. It
  does **not** include:
  - a language model — answers use the deterministic local synthesizer until
    you run an OpenAI-compatible local server such as Ollama, llama.cpp or
    LM Studio;
  - tesseract / Poppler (for OCR);
  - Playwright/Chromium (for rendered web pages);
  - the Anthropic SDK.

  Settings → "What works offline" lists exactly what is available in the
  running app.
- **Signing:** current builds are ad-hoc signed. Developer ID signing,
  notarization and stapling need an Apple Developer account (`BLK-SIGN-001`).
  Until then, the first time you open a downloaded copy, macOS Gatekeeper asks
  you to confirm: Control-click the app, choose **Open**, or allow it under
  System Settings → Privacy & Security.

## Upgrades, downgrades and reinstalls

- **Replacing the app keeps your data.** The app bundle holds no user data, so
  dragging in a newer `Mucaro AI.app`, or reinstalling the same one, keeps
  everything in Application Support. The macOS workflow checks this on every
  build: it deletes the app, reinstalls it from the zip, and confirms that the
  earlier conversation and source are still there.
- **Database upgrades are atomic and forward-only.** On first start after an
  upgrade, the local database moves to the new schema in a single transaction.
  If that is interrupted, it rolls back and the old data stays intact.
- **Downgrades fail safely.** An older app will not open a database written by
  a newer one. It stops with `ERROR_DATABASE_SCHEMA_TOO_NEW` instead of
  guessing, and it does not modify the data.

`backend/tests/test_db_upgrade.py` tests all three paths: upgrade, interrupted
upgrade, and a too-new database. A cross-version app-to-app upgrade test needs
a previous packaged release, and this is the first one.

## Build and verify (maintainers)

On an Apple Silicon Mac:

```bash
python3.11 -m venv .build-venv
.build-venv/bin/pip install --require-hashes --no-deps -r packaging/macos/requirements-macos-arm64.lock
(cd frontend && npm ci && npm run build)
.build-venv/bin/python scripts/build_macos_app.py            # clean tree required
.build-venv/bin/python scripts/verify_macos_app.py \
  --zip data/exports/macos_app/<stamp>-<head>/Mucaro-AI-*-macos-arm64.zip --output e2e_receipt.json
```

`build_macos_app.py` does the following and writes `build_receipt.json` and
`bundle_manifest.json` (a SHA-256 for every file):
- freezes the app with `packaging/macos/mucaro.spec`;
- signs it ad-hoc and runs `codesign --verify`;
- checks the Info.plist, the arm64 architecture and that no data or secrets
  are bundled;
- smoke-starts the frozen app;
- zips it with `ditto`.

`verify_macos_app.py --zip` installs the zip into `~/Applications` and opens
it through LaunchServices, the same path a Finder double-click takes. It then
records:
- that the native window was shown (Quartz window list, plus a screenshot);
- that the GUI connected with the launch token;
- that private routes refuse requests without the token;
- ingest → ask → cited answer → export;
- quit and relaunch with data persisted;
- that only one instance runs;
- that SIGTERM quits gracefully;
- that reinstalling keeps user data.

The launch token is never written to a receipt.

The `macOS app` GitHub workflow (`.github/workflows/macos-app.yml`) runs both
scripts on `macos-14`. It uploads the receipts, the screenshot and the zip.

On Linux, `scripts/build_macos_app.py --structural --allow-dirty` builds the
frozen onedir tree with the same spec. `verify_macos_app.py --executable
<tree>/Mucaro\ AI` then runs the same API workflow headless. These checks
cover the spec and the frozen service only, never window or LaunchServices
behaviour.
