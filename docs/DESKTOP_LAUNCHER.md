# Desktop Launcher Mode

Desktop Launcher Mode lets Mucaro AI run with one command.

It starts the private local backend on `127.0.0.1:8000`, serves the built React UI through FastAPI, opens the browser, and stops cleanly with `Ctrl+C`.

## Double-Click App

Build a local source-bound macOS Application bundle:

```bash
python scripts/package_desktop.py --build-app
```

Expected marker:

```text
MACOS_APP_BUNDLE_READY
```

The generated app is:

```text
data/exports/desktop_apps/Mucaro AI.app
```

Double-clicking that app launches this checked-out source tree with the Python
executable recorded at build time, then runs `scripts/launch_app.py`.

Verify an existing bundle with:

```bash
python scripts/package_desktop.py --verify-app "data/exports/desktop_apps/Mucaro AI.app"
```

Expected marker:

```text
MACOS_APP_BUNDLE_VERIFIED
```

This is a local source-bound wrapper. A portable signed installer with embedded
runtime, no-setup reinstall, upgrade, and rollback guarantees remains future
packaging work.

## Run

```bash
cd "/Users/jotaele/Developer/mucaro-ai"
source .venv/bin/activate
python scripts/launch_app.py
```

## Self Test

Use this to verify launcher file layout without starting the backend:

```bash
python scripts/launch_app.py --self-test
```

Expected marker:

```text
LAUNCH_APP_SELF_TEST_OK
```

## Smoke Test

Use this for automation and certification. It builds or reuses `frontend/dist`, starts the backend, verifies `/health`, stops the backend, and confirms the local port closes.

```bash
python scripts/launch_app.py --smoke-test --no-browser --port 8000
```

Expected markers:

```text
BACKEND_READY
DESKTOP_SMOKE_READY
DESKTOP_BACKEND_PORT_CLOSED
DESKTOP_SMOKE_OK
```

## Port Conflicts

If the target port is already used by a non-Mucaro process, the launcher exits with:

```text
ERROR_PORT_IN_USE_NON_MUCARO_AI
```

If an existing Mucaro backend is already healthy on the port, the launcher reuses it.

## Fresh-Clone Certification

Run the full first-run certification from a temporary clone:

```bash
python scripts/certify_fresh_clone.py
```

Expected marker:

```text
FRESH_CLONE_CERT_OK
```

The certification script removes the temporary clone unless `--keep` is passed.
