# Desktop Launcher Mode

> **Source setup, not a release artifact.** `Mucaro AI.command` / `Mucaro AI.cmd` is the supported
> way to run Mucaro from a source checkout; `Start Mucaro AI.app` / `Start Mucaro AI.exe` run the same
> setup with no terminal window. Release-grade certification requires two packaged lanes,
> and neither is this launcher:
>
> - the native app, `Mucaro AI.app` with its own window: see [release/MACOS_APP.md](release/MACOS_APP.md);
> - the portable embedded-runtime app: see [PACKAGING_POLICY.md](PACKAGING_POLICY.md#portable-runtime-and-application).
>
> The source-bound bundle below needs a checkout and a Python environment, and opens the system
> browser (contradiction RC-006, adjudicated).

Desktop Launcher Mode lets Mucaro AI run with one command.

## Double-click setup and launch

`Start Mucaro AI.app` (macOS) and `Start Mucaro AI.exe` (Windows) set up everything on first use, show
progress on a browser page, and run the app in the background until **Quit Mucaro AI** is clicked.
`Mucaro AI.command` (macOS) and `Mucaro AI.cmd` (Windows) do the same in a Terminal or console
window. All of them end in `scripts/launch_app.py --auto-port --exit-if-running`. See
`docs/ONE_CLICK_SETUP.md`.

Launcher options used by the double-click files:

| Option | Behavior |
|---|---|
| `--auto-port` | If the port is used by another program, use the next free port (up to 20 higher) and print `PORT_FALLBACK from=... to=...` |
| `--exit-if-running` | If a healthy Mucaro AI backend is already running, open it in the browser, print `DESKTOP_LAUNCH_REUSED`, and exit 0 |
| `--status-file PATH` | Keep the progress page at PATH up to date: ready (with the app URL), error, or stopped |
| `--stop-file PATH` | Stop cleanly when PATH appears (`DESKTOP_STOP_REQUESTED reason=quit_button`); the backend's `POST /shutdown` creates it, and gets its location in `MUCARO_AI_STOP_FILE` |

Closing the terminal window (SIGHUP) or SIGTERM stops the backend the same way `Ctrl+C` does.

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
