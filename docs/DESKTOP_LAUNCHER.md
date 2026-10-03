# Desktop Launcher Mode

> **Source setup, not a release artifact.** Double-click the tracked `Mucaro AI.app` beside
> `pyproject.toml` on macOS. It runs setup quietly and opens a native window without Terminal or
> a browser. `Mucaro AI Setup.app` does the same but shows each setup step in its own window first.
> Release-grade certification requires two packaged lanes,
> and none of these launchers is one:
>
> - the native app, `Mucaro AI.app` with its own window: see [release/MACOS_APP.md](release/MACOS_APP.md);
> - the portable embedded-runtime app: see [PACKAGING_POLICY.md](PACKAGING_POLICY.md#portable-runtime-and-application).
>
> The source-bound bundle needs the checkout and provisions its own Python environment. It is
> distinct from the signed, embedded-runtime release app. Native Finder/WKWebView behavior
> still requires verification on macOS; Linux regression tests do not certify it.

Desktop Launcher Mode lets Mucaro AI run with one command.

## Legacy console setup and launch

`Mucaro AI.command` (macOS) and `Mucaro AI.cmd` (Windows) set up everything on first use and then
run `scripts/launch_app.py --auto-port --exit-if-running`. `Mucaro AI.app` and `Mucaro AI.exe` run
the same setup silently and add `--native` (own window, no browser). See `docs/ONE_CLICK_SETUP.md`.
`Mucaro AI Setup.app` (macOS) runs the same setup inside its own window, showing each step,
then turns that window into the app with the local service running inside it
(`scripts/one_click_desktop.py`).

Launcher options used by the double-click files:

| Option | Behavior |
|---|---|
| `--auto-port` | If the port is used by another program, use the next free port (up to 20 higher) and print `PORT_FALLBACK from=... to=...` |
| `--exit-if-running` | If a healthy Mucaro AI backend is already running, open it in the browser, print `DESKTOP_LAUNCH_REUSED`, and exit 0 |

Closing the terminal window (SIGHUP) or SIGTERM stops the backend the same way `Ctrl+C` does.

It starts the private local backend on `127.0.0.1:8000`, serves the built React UI through FastAPI, opens the browser, and stops cleanly with `Ctrl+C`.

## Double-Click App

For source-checkout use, open the `Mucaro AI Setup.app` committed in the repository root; it needs no build.
The generated wrapper below is kept for the packaging checks (`MACOS_APP_BUNDLE_VERIFIED`).

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

Double-clicking that app runs `scripts/native_start.sh`, which redirects output into logs and
starts `scripts/one_click.sh --native`. Setup installs the desktop extra and checks Cocoa/WebKit
imports before launching. No pre-existing Python environment is required. A failed setup or
launch produces one dialog and leaves its log available for troubleshooting.

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
