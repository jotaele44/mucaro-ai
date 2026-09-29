# Double-Click Setup

Double-click `Start Mucaro AI.app` (macOS) or `Start Mucaro AI.exe` (Windows). A progress page
opens in the browser; the first run installs everything and the page turns into the app; later runs
open it in seconds. No input is needed at any point and no terminal window opens. The app runs in
the background until **Quit Mucaro AI** is clicked.

`Mucaro AI.command` (macOS/Linux) and `Mucaro AI.cmd` (Windows) run the same setup with progress in
a Terminal or console window; closing that window stops the app.

All four run Mucaro AI from this source folder. The native macOS app (`Mucaro AI.app`, its own
window, no browser) is a separate release build that CI makes and that is never committed: see
`docs/release/MACOS_APP.md`.

## Why it exists

Before this change, first use required a terminal, a separate bootstrap command, and a Python
3.11+ and Node 20+ that the user had installed. Each of these, confirmed in the repo, stopped a
first-time user:

| Problem | Evidence |
|---|---|
| `Mucaro AI.command` quit with `ERROR_VENV_MISSING` until `bootstrap_macos.sh` had been run | previous `Mucaro AI.command` |
| Missing Python 3.11+ or Node stopped the bootstrap with install instructions | `scripts/bootstrap_macos.sh`, `scripts/bootstrap_windows.ps1` |
| On a Mac without the Command Line Tools, running `python3` or `git` opens an "install developer tools" dialog | bootstrap probe of `python3`; `preflight.py` runs `git --version` |
| A generic `python3` is often 3.14 today, and `rasterio==1.4.3` publishes no cp314 wheels, so the install would fail | PyPI file list for rasterio 1.4.3 |
| Another program on port 8000 stopped the launcher (`ERROR_PORT_IN_USE_NON_MUCARO_AI`) | `scripts/launch_app.py` |
| `frontend/dist` was rebuilt only when missing, so a `git pull` could serve an old UI | `scripts/launch_app.py` |

## How it works

```
Start Mucaro AI.app (macOS)                    Start Mucaro AI.exe (Windows)
  Contents/MacOS/mucaro-launcher (bash)          C launcher, GUI subsystem
  writes + opens the progress page,              starts the .ps1 with CREATE_NO_WINDOW,
  starts setup detached, exits at once           exits at once
        | --ui                                          | --ui
        |      Mucaro AI.command / Mucaro AI.cmd (Terminal/console window, no --ui)
        v                                               v
scripts/one_click.sh        -- stage 0 --   scripts/one_click_windows.ps1
  pins: scripts/one_click_pins.env (versions + SHA-256)
  platform check, disk check, setup lock, log file
  private uv -> private Node.js -> private CPython 3.12 + .venv
        | runs .venv python
        v
scripts/one_click.py        -- stage 1 (same on every OS) --
  Python packages -> npm ci -> vite build -> .env + preflight -> sample documents
  each step skipped when its inputs are unchanged (.mucaro-runtime/state.json)
        |
        v
scripts/launch_app.py --auto-port --exit-if-running --startup-timeout 120
                      (--ui adds: --no-browser --status-file <page> --stop-file <file>)
```

- **Progress page.** `.mucaro-runtime/ui/progress.html` is rewritten from
  `scripts/one_click_progress.html` at every step by the same renderer in three languages
  (`scripts/progress_page.py`, `scripts/progress_page.sh`, and the copy in
  `scripts/one_click_windows.ps1`; tests keep them byte-identical). While setup runs it reloads
  itself every second; when the app is ready it redirects to `http://127.0.0.1:<port>` (and shows an
  "Open Mucaro AI" link); on failure it shows the error code, what to do, and the log path. It uses
  no JavaScript, so it behaves the same for a `file://` page in every browser.
- **Background app and Quit.** In `--ui` mode all output goes to `.mucaro-runtime/logs/run-*.log`.
  The **Quit Mucaro AI** button calls `POST /shutdown`, which writes `.mucaro-runtime/stop-request`;
  the launcher notices it within a second, stops the backend, and marks the page stopped. The
  endpoint only works when the launcher started the backend, only from this computer (loopback),
  only with the header `X-Mucaro-Action: quit` (so other websites cannot trigger it), and with the
  operator token when one is set.
- **Each double-click is a fresh launcher.** Both launchers exit within a second, so a second
  double-click while the app runs goes through the fast path and the page switches straight to the
  running app (`DESKTOP_LAUNCH_REUSED`).
- **Crashes never leave the page spinning.** If setup ends while the page still says "running",
  the launcher (macOS) or the `.ps1` (Windows) turns it into an error page.

- **Private runtimes only.** Setup never runs `python`, `python3`, `git`, `node`, or `npm` from
  `PATH`, so preinstalled or broken tools cannot interfere and the macOS developer-tools dialog
  never appears. A unit test (`test_stage0_shell_never_runs_python_git_node_or_npm_from_path`)
  and the CI tripwire stubs enforce this.
- **Verified downloads.** uv and Node.js archives are checked against SHA-256 values pinned in
  `scripts/one_click_pins.env`, which were copied from the official release checksum files.
  CPython is installed by the pinned uv, which verifies its own downloads. A mismatch deletes the
  file and stops with `ERROR_CHECKSUM_MISMATCH`.
- **Frozen dependency set.** Python packages are installed with
  `--exclude-newer $PY_EXCLUDE_NEWER`, so a later release of a transitive dependency cannot change
  what a fresh setup installs. npm uses `package-lock.json` through `npm ci`.
- **Isolation.** The user's `~/.npmrc`, npm cache, `NODE_OPTIONS`, `PYTHONPATH`, active virtualenv
  or conda environment, and uv config files are ignored.
- **Resumable.** Each step records a fingerprint of its inputs only after it succeeds. An
  interrupted or failed setup resumes at the first unfinished step on the next double-click.
- **One setup at a time.** A lock (`.mucaro-runtime/setup.lock`) makes a second window wait
  (`SETUP_WAITING`). A lock left by a killed process is detected and removed
  (`SETUP_LOCK_STALE`).
- **Launch.** If port 8000 is taken by another program, the next free port is used
  (`PORT_FALLBACK`). If Mucaro AI is already running, it is reused (`DESKTOP_LAUNCH_REUSED`).
  With `Mucaro AI.command` / `Mucaro AI.cmd`, closing the window stops the backend.

## The launchers

They are named `Start Mucaro AI` so they cannot be confused with the native release app
`Mucaro AI.app`; the bundle identifier (`ai.mucaro.source-launcher`) is distinct as well.

| File | What it is | How it is checked |
|---|---|---|
| `Start Mucaro AI.app/Contents/MacOS/mucaro-launcher` | bash 3.2 script; same "no `PATH` tools" rules as `one_click.sh` | shellcheck, unit tests, CI launches the `.app` through LaunchServices (`open`) |
| `Start Mucaro AI.app/Contents/Info.plist` | `LSUIElement` (no Dock icon for a launcher that lives a second), macOS 13+, version kept equal to `pyproject.toml` | unit test |
| `Start Mucaro AI.exe` | about 89 KB, x86-64, GUI subsystem, built from `packaging/launchers/windows/` with pinned zig | `scripts/build_launchers.py --check` rebuilds it byte for byte in CI; `BUILD_SHA256` records its hash |
| `AppIcon.icns`, `packaging/icons/mucaro.ico` | owl mark (a mucaro is an owl), drawn by `scripts/build_launchers.py` with pinned Pillow | rebuilt byte for byte in CI |

To change the launchers or icons, edit their source, run `python scripts/build_launchers.py` on
Linux x86_64 (Python 3.11 or 3.12), and commit the rebuilt files together with `BUILD_SHA256`.

## Pinned versions

| Component | Version | Source |
|---|---|---|
| uv | 0.12.19 | github.com/astral-sh/uv releases |
| CPython | 3.12 (3.12.14 with uv 0.12.19) | python-build-standalone, installed by uv |
| Node.js | 22.23.3 LTS | nodejs.org/dist |
| Python package cutoff | 2026-09-25T00:00:00Z | `PY_EXCLUDE_NEWER` |

To update a pin, change `scripts/one_click_pins.env` using the official checksum files.
`test_pins_cover_every_supported_platform_with_sha256` checks the format, and the
`One-click setup` workflow proves the new pins on every platform. Bump `SETUP_SCHEMA` when a
change must make every existing setup redo all steps once.

## Supported platforms

| Platform | Minimum | Why the minimum |
|---|---|---|
| macOS, Apple silicon | macOS 14 | rasterio 1.4.3 and pyproj 3.7.2 publish `macosx_14_0_arm64` wheels only |
| macOS, Intel | macOS 13 | pyproj 3.7.2 publishes `macosx_13_0_x86_64` wheels |
| Windows x64 | Windows 10 | rasterio publishes `win_amd64` wheels only (no Windows on ARM) |
| Linux x86_64 | glibc 2.28 | pyproj `manylinux_2_28` wheels; run `./"Mucaro AI.command"` from a terminal |

Anything else stops at once with `ERROR_PLATFORM_UNSUPPORTED` or `ERROR_MACOS_TOO_OLD`.

## Where files go and how to remove them

Everything stays inside the project folder:

| Path | Contents |
|---|---|
| `.mucaro-runtime/uv`, `node`, `python` | private uv, Node.js, CPython |
| `.mucaro-runtime/cache` | uv and npm download caches (lets a moved folder rebuild offline) |
| `.mucaro-runtime/logs/setup-*.log`, `run-*.log` | the last 10 setup logs and the last 10 background-run logs |
| `.mucaro-runtime/ui/progress.html` | the progress page |
| `.mucaro-runtime/state.json` | step fingerprints |
| `.venv`, `frontend/node_modules`, `frontend/dist`, `.env` | project environment, UI build, settings |

To remove everything, delete `.mucaro-runtime/` and `.venv/` (and `frontend/node_modules/` and
`frontend/dist/` if you like). Your documents and answers in `data/` are not affected. Moving the
project folder is safe: the next double-click notices and rebuilds `.venv`.

First run: about 200 MB downloaded, about 700 MB on disk. Setup checks for 2 GB free before it
downloads (`ERROR_DISK_FULL`).

## Error codes

| Code | Meaning | What to do |
|---|---|---|
| `ERROR_DOWNLOAD_FAILED` | a download failed 5 times (waits of 2, 4, 8, 16 s) | check the internet connection, double-click again |
| `ERROR_CHECKSUM_MISMATCH` | a download did not match its pinned SHA-256 and was deleted | double-click again; if it repeats, the network is altering downloads |
| `ERROR_PY_DEPS_FAILED`, `ERROR_NPM_CI_FAILED` | package install failed 3 times | check the internet connection, double-click again |
| `ERROR_FRONTEND_BUILD_FAILED` | the UI build failed | see the log; report it |
| `ERROR_PREFLIGHT_FAILED` | the readiness check found a problem | see the `ERROR` lines above it |
| `ERROR_DISK_FULL` | less than 2 GB free for the first setup | free disk space, double-click again |
| `ERROR_FOLDER_NOT_WRITABLE` | the project folder is read-only (for example opened inside a disk image) | move the folder somewhere writable |
| `ERROR_MACOS_TOO_OLD`, `ERROR_PLATFORM_UNSUPPORTED` | see Supported platforms | |
| `ERROR_SETUP_LOCK_TIMEOUT` | another window has been setting up for over an hour | close it, double-click again |
| `ERROR_STAGE0_MISSING`, `ERROR_NOT_IN_VENV` | `scripts/one_click.py` was started directly | use the double-click file |
| `ERROR_UNEXPECTED` | setup hit an error it does not know | see the log named on the page; double-click again |
| `SHUTDOWN_NOT_SUPERVISED` (409) | Quit was called on a backend not started by a double-click launcher | stop it where it was started |
| `SHUTDOWN_LOOPBACK_ONLY` (403), `SHUTDOWN_HEADER_REQUIRED` (400) | Quit was called from another computer, or without `X-Mucaro-Action: quit` | use the button on the computer running Mucaro AI |

## Limits that code cannot remove

| Limit | Effect | Tier | Confidence |
|---|---|---|---|
| A ZIP downloaded from GitHub carries the macOS quarantine flag (Windows: Mark-of-the-Web) | The unsigned `.app` is blocked until it is allowed in System Settings > Privacy & Security; the `.exe` gets a SmartScreen prompt. Cloning with `git` or GitHub Desktop avoids both. Removing them for ZIP downloads needs an Apple Developer ID with notarization and a Windows code-signing certificate. | T4 | 90% |
| macOS App Translocation (quarantined app run from a random read-only copy) | The launcher detects it and shows one alert: move the folder with Finder or clone with git. | T4 | 85% |
| Windows antivirus heuristics on an unsigned `.exe` that starts PowerShell with `-ExecutionPolicy Bypass` | Could be flagged. Reduced by hiding the window with a process flag (not `-WindowStyle Hidden`) and by the reproducible source build; `Mucaro AI.cmd` remains as a fallback. | T4 | 70% not flagged |
| Safari following a `file://` page's refresh to `http://127.0.0.1` | Verified in Chromium (Chrome, Edge); not reproduced in Safari. The ready page also shows an "Open Mucaro AI" link. | T4 | 85% |
| Project inside `~/Desktop`, `~/Documents`, or `~/Downloads` on macOS | macOS may ask once whether the app may access that folder. `~/Developer` is not affected. | T4 | 85% |
| First run needs internet | later runs work offline (verified with the network disabled) | T1 | 99% |
| Group Policy that enforces a PowerShell execution policy | `-ExecutionPolicy Bypass` cannot override it | T4 | 90% |
| Corporate TLS inspection | uv and Node use their own certificate stores. Set `SSL_CERT_FILE` / `NODE_EXTRA_CA_CERTS` (or `UV_SYSTEM_CERTS=1`) in the environment. | T4 | 75% |

## Verification

Linux x86_64 sandbox (T1), on a fresh copy in a path containing a space, with a new `HOME` and
tripwire stubs for `python`, `python3`, `pip`, `node`, `npm`, `npx`, and `git` first on `PATH`:

| Check | Result |
|---|---|
| First run | `ONE_CLICK_SETUP_OK`, `DESKTOP_SMOKE_OK`; runtimes downloaded and `CHECKSUM_OK` |
| Second run | `ONE_CLICK_FAST_PATH`, 1.6 s including backend start and stop |
| Second run with the network disabled | `ONE_CLICK_FAST_PATH`, `DESKTOP_SMOKE_OK` |
| Tripwire file and `HOME` | both empty: no `PATH` tool was run and nothing was written outside the project |
| Full run | `/health` OK, UI served, sample question answered with cited chunks, UI rendered with no console errors |
| Window closed (SIGHUP to the process group, and to the launcher alone) | backend stopped, port closed |
| Port 8000 held by another server | `PORT_FALLBACK from=8000 to=8001`, `DESKTOP_SMOKE_OK` |
| Second double-click while running | `DESKTOP_LAUNCH_REUSED`, exit 0 |
| Two double-clicks at once | the second printed `SETUP_WAITING`, then `ONE_CLICK_FAST_PATH` |
| `node_modules` deleted / a `frontend/src` file edited | only `npm ci` / only the UI build reran |
| `.venv` made with a system Python | `VENV_RECREATE`, rebuilt on the private Python |
| Lock left by a dead process | `SETUP_LOCK_STALE`, then continued |
| Pinned checksum altered | `ERROR_CHECKSUM_MISMATCH`, lock released; with the pin restored, the next run resumed |
| Real `git clone`, run twice | `ONE_CLICK_SETUP_OK` then `ONE_CLICK_FAST_PATH`; `git status` clean afterwards |
| Project folder moved, then moved again with the network disabled | `VENV_RECREATE`, packages reinstalled from the private cache, `DESKTOP_SMOKE_OK` both times |
| `Start Mucaro AI.app` launcher on a fresh copy (browser journey in Chromium) | the progress page showed each step and switched to the app by itself; **Quit Mucaro AI** asked for confirmation and showed the stopped screen; port closed, no processes left, page "stopped" |
| Second launcher while the app runs | `DESKTOP_LAUNCH_REUSED`, one backend only, page "ready" |
| Pinned checksum altered, through the launcher | page in error with `ERROR_CHECKSUM_MISMATCH` and the log path, no refresh loop, lock released; next launch resumed |
| Progress page renderers (Python, bash 5.2, PowerShell 7.5) | byte-identical output for every state |
| `scripts/build_launchers.py --check` on Python 3.11 and 3.12 | `.exe`, `.icns`, `.ico` rebuilt byte for byte |
| `pytest backend/tests` | 1012 passed on Python 3.11 and 1012 passed on 3.12, with `pwsh` on `PATH` (so including the 2 PowerShell parity tests) |
| Rendered UI QA (`qa/rendered`, Chromium, 1440x900 and 390x844) with the Quit scenario | 100/100 checks: Quit hidden when no launcher supervises the service; offered in the sidebar and Settings when one does; Keep running quits nothing; Quit writes the stop file and shows the stopped screen, which stays; no serious axe findings; no horizontal overflow |
| shellcheck 0.11 / PowerShell 7.5 parser and PSScriptAnalyzer | clean / 0 parse errors, 0 analyzer errors |

On real macOS (Apple silicon and Intel), Windows x64, and Linux runners, the `One-click setup`
workflow (`.github/workflows/one-click-setup.yml`):

- launches `Start Mucaro AI.app` through LaunchServices (`open`) and `Start Mucaro AI.exe` through
  `Start-Process`, from a fresh checkout: a first-run smoke launch, then a background start, a
  second launch while it runs, and the Quit endpoint (backend gone, page "stopped");
- runs `Mucaro AI.command` and `Mucaro AI.cmd` with `PATH` cut down to OS directories (plus tripwire stubs on macOS
  and Linux), requiring `ONE_CLICK_SETUP_OK` then `ONE_CLICK_FAST_PATH`, both with
  `DESKTOP_SMOKE_OK`, and checks the bash 3.2 page renderer on macOS;
- rebuilds the `.exe` and icons and requires them to match the committed files byte for byte.

## Blind spots

- No physical Mac or Windows PC was used; macOS and Windows behaviour is proven on GitHub-hosted
  runners, which have developer tools installed. The tripwire stubs show those tools are not used,
  but the Command Line Tools dialog itself was not reproduced.
- Terminal.app's behaviour when a window is closed was simulated with SIGHUP.
- The progress page's redirect was verified in Chromium only (see Limits for Safari).
- Whether Windows Defender flags the unsigned `.exe` on a real user's PC cannot be measured in CI.
- Gatekeeper, SmartScreen, and macOS folder-access prompts come from platform documentation (T4)
  and were not reproduced.

## Queued leads (not changed here)

1. **Signing.** A Developer ID signature with notarization for `Start Mucaro AI.app` and an
   Authenticode signature for `Start Mucaro AI.exe` would remove the Gatekeeper/SmartScreen prompts
   for ZIP downloads and the antivirus risk. Needs an Apple Developer account and a code-signing
   certificate.
2. **git at runtime.** `backend/app/skill_runtime.py` runs `git rev-parse HEAD` when a skill runs.
   On a Mac without the developer tools that can open the install dialog after setup.
3. **Python lock file.** `--exclude-newer` freezes resolution by date. A committed lock file
   (for example `uv.lock`) would also pin exact versions for the developer bootstrap and CI.
4. **Windows on ARM.** The setup could run x64 Python under emulation; not attempted because it
   could not be verified.
