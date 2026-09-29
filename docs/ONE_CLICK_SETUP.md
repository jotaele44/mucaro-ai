# Double-Click Setup

Open `Mucaro AI Setup.app` (macOS) or double-click `Mucaro AI.cmd` (Windows). The first run installs
everything and opens the app; later runs open it in seconds. No input is needed at any point.

On a Mac the app runs without a terminal or a browser and shows Mucaro AI in its own window: see
[Mucaro AI Setup.app](#mucaro-aiapp-macos). `Mucaro AI.command` runs the same setup in Terminal and
opens the browser; it is also the launcher for Linux.

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
Mucaro AI Setup.app (macOS)      Mucaro AI.command (macOS/Linux)      Mucaro AI.cmd (Windows)
  Contents/MacOS/Mucaro AI         |                                   |
        | exec --desktop           | exec                              | powershell -ExecutionPolicy Bypass
        v                          v                                   v
scripts/one_click.sh             -- stage 0 --              scripts/one_click_windows.ps1
  pins: scripts/one_click_pins.env (versions + SHA-256)
  platform check, disk check, setup lock, log file
  private uv -> private Node.js -> private CPython 3.12 + .venv
        | runs .venv python
        v
scripts/one_click.py             -- stage 1 (same on every OS) --
  Python packages -> npm ci -> vite build -> .env + preflight -> sample documents
  each step skipped when its inputs are unchanged (.mucaro-runtime/state.json)
        |                                           |
        v                                           v  with --desktop (macOS)
scripts/launch_app.py --auto-port            scripts/one_click_desktop.py
  --exit-if-running --startup-timeout 120      the same steps behind the Mucaro AI window,
  (backend + browser)                          then the service in-process, the app in that window
```

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
  (`PORT_FALLBACK`). If Mucaro AI is already running, the browser opens and the new window exits
  (`DESKTOP_LAUNCH_REUSED`). Closing the window stops the backend.

## Mucaro AI Setup.app (macOS)

`Mucaro AI Setup.app` is committed in this folder, in the same way as the federation apps
(`PRII-THEHUB.app` in `thehub-pr`). It is a standard app bundle with the owl icon, and its
program is a short bash script:

| File | Purpose |
|---|---|
| `Contents/Info.plist` | name, icon, bundle id `ai.mucaro.launcher` (the packaged release app is `ai.mucaro.app`) |
| `Contents/MacOS/Mucaro AI` | the launcher: finds this folder, then runs `scripts/one_click.sh --desktop` |
| `Contents/Resources/AppIcon.icns` | the owl of the release app, rendered by `scripts/build_app_icon.py` (`make app-icon`) |
| `Contents/PkgInfo` | `APPL????` |

What opening it does:

1. The launcher checks that it runs inside its mucaro-ai folder. A copy moved somewhere else, or a
   quarantined copy that macOS runs from a temporary read-only location ("App Translocation"),
   stops with a dialog that says how to fix it.
2. Stage 0 runs as described above, with no terminal. If it takes more than 3 seconds (a first
   run), a small "Mucaro AI is getting ready" notice appears. A failure is shown in a dialog that
   can reveal the setup log in Finder.
3. Stage 1 (`scripts/one_click_desktop.py`) installs the window library if it is missing
   (pywebview, pinned by the `desktop` extra in `pyproject.toml`; on macOS the `desktop` extra is
   part of every setup). It then opens the Mucaro AI window, with the owl in the Dock and
   "Mucaro AI" in the menu bar, and closes the notice. The window lists the setup steps while they
   run; a later open, which has nothing to set up, shows only "Starting Mucaro AI".
4. When setup is done, the local service starts inside the app process. It listens on `127.0.0.1`
   only, on a random port, with a fresh token for each launch and the `production` security
   profile (the same service as the packaged release app, `backend/app/desktop_shell.py`). The
   same window then shows Mucaro AI with the sample documents loaded.
5. A failure is shown in the window with its code, a hint and the log, and three buttons:
   **Try again** (setup resumes where it stopped), **Show log** (reveals it in Finder) and
   **Quit**.
6. Closing the window, or Cmd+Q, stops the service. Closing it during setup stops setup; the next
   open resumes it.

The app opens the same library as `Mucaro AI.command` and the `mucaro` CLI: `data/` in this folder,
or `MUCARO_AI_DATA_DIR` from the environment or `.env`. Settings in `.env` (live mode, model, API
key) apply to the app; they are loaded only after setup, so setup commands never see them. The
window keeps `instance.json`, `instance.lock`, `logs/` and `webview/` in the library; git ignores
them. Only one window can use a library at a time (`ERROR_ALREADY_RUNNING`), and a second open of
the app brings the running one to the front. Use one launcher at a time: `Mucaro AI.command` does
not check for a running app.

`MUCARO_APP_DIALOGS=0` turns the dialogs off (automation and tests).

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
| `.mucaro-runtime/logs/setup-*.log` | the last 10 setup logs |
| `.mucaro-runtime/state.json` | step fingerprints |
| `.venv`, `frontend/node_modules`, `frontend/dist`, `.env` | project environment, UI build, settings |
| `data/instance.json`, `data/instance.lock`, `data/logs/`, `data/webview/` | Mucaro AI Setup.app's window state and service log (git-ignored) |

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
| `ERROR_DESKTOP_WINDOW_FAILED`, `ERROR_DESKTOP_WINDOW_MISSING` | Mucaro AI Setup.app could not install or load its window library (pywebview) | check the internet connection, open the app again |
| `ERROR_SETUP_UNEXPECTED` | Mucaro AI Setup.app's setup stopped on an unexpected error | choose Try again; if it repeats, see the log |
| `ERROR_DESKTOP_START_FAILED` | the local service did not start inside Mucaro AI Setup.app | see the log; `data/logs/startup-error.log` has the reason |
| `ERROR_DATA_DIR_INVALID` | `MUCARO_AI_DATA_DIR` is not an absolute path | fix it in `.env`, then choose Try again |
| `ERROR_ALREADY_RUNNING` | another Mucaro AI window is using the same library | switch to it from the Dock |
| `SETUP_CANCELLED` | the Mucaro AI window was closed during setup | open the app again; setup resumes |

## Limits that code cannot remove

| Limit | Effect | Tier | Confidence |
|---|---|---|---|
| A ZIP downloaded from GitHub carries the macOS quarantine flag (Windows: Mark-of-the-Web) | Gatekeeper or SmartScreen asks once before the first run. Cloning with `git` or GitHub Desktop avoids it. Removing it for ZIP downloads needs a signed and notarized installer. | T4 | 90% |
| Project inside `~/Desktop`, `~/Documents`, or `~/Downloads` on macOS | macOS asks once whether Mucaro AI (or Terminal, for `Mucaro AI.command`) may access that folder. `~/Developer` is not affected. | T4 | 85% |
| Folder downloaded as a ZIP, opened with Mucaro AI Setup.app | macOS blocks the app once, or runs a read-only copy of it that cannot see this folder (App Translocation; the app then says so). Double-click `Fix-Gatekeeper.command` once; if macOS blocks that too, allow it under System Settings, Privacy & Security, Open Anyway. `git clone` or GitHub Desktop avoid it. Removing it needs a signed and notarized app (BLK-SIGN-001). | T4 | 85% |
| The app window's web engine | WebKit keeps its caches for the app under `~/Library` (as for any app); everything the setup installs stays in this folder | T4 | 80% |
| Moving Mucaro AI Setup.app out of the folder | it must stay next to `scripts/`; drag it onto the Dock to start it from there | T1 | 99% |
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
| `pytest backend/tests` | 710 passed on Python 3.11 and 710 passed on the private 3.12 |
| shellcheck 0.11 / PowerShell 7.5 parser | clean / 0 parse errors |

On real macOS (Apple silicon and Intel), Windows x64, and Linux runners, the `One-click setup`
workflow (`.github/workflows/one-click-setup.yml`) runs the double-click files themselves. `PATH`
is cut down to OS directories (plus the tripwire stubs on macOS and Linux). The workflow requires
`ONE_CLICK_SETUP_OK` on the first run and `ONE_CLICK_FAST_PATH` on the second, both with
`DESKTOP_SMOKE_OK`. It also checks that `.venv` runs the private Python and that setup left no
tracked changes.

Its `Mucaro AI Setup.app (macOS arm64)` job opens the committed app with `open`, the path a Finder
double-click takes, on a clean runner, so the app gets no terminal and none of the runner's tools.
It waits for the first-run setup, then `scripts/verify_checkout_app.py` checks:

- the window was shown and the GUI loaded with the launch token;
- the production security profile;
- the two sample documents are loaded;
- the ingest, cited answer and export workflow of `scripts/verify_macos_app.py`;
- quitting through an Apple Event stops the service;
- a second open reaches the loaded GUI in under 60 seconds and finds the earlier conversation.

It also checks that setup left no tracked changes. Screenshots of the first-run progress window
and of the loaded app are uploaded with the receipt and logs.

`backend/tests/test_macos_app_bundle.py` and `backend/tests/test_one_click_desktop.py` check, on
any OS:

- the bundle (plist, modes, line endings, icon);
- the launcher's hand-over and refusals;
- the in-window setup flow, with a stand-in window;
- the real in-process service under the production profile.

## Blind spots

- No physical Mac or Windows PC was used; macOS and Windows behaviour is proven on GitHub-hosted
  runners, which have developer tools installed. The tripwire stubs show those tools are not used,
  but the Command Line Tools dialog itself was not reproduced.
- Mucaro AI Setup.app's dialogs, Dock icon and menu name, and its Gatekeeper and folder-access
  behaviour were not seen on a physical Mac. The runner job proves the window, the GUI and the
  quit path through LaunchServices; the dialogs use standard `osascript` `display dialog`.
- Terminal.app's behaviour when a window is closed was simulated with SIGHUP.
- Gatekeeper, SmartScreen, and macOS folder-access prompts come from platform documentation (T4)
  and were not reproduced.

## Queued leads (not changed here)

1. **Signed installer.** A notarized macOS `.pkg`/`.dmg` and a signed Windows installer would
   remove the one-time Gatekeeper/SmartScreen prompt for ZIP downloads.
2. **git at runtime.** `backend/app/skill_runtime.py` runs `git rev-parse HEAD` when a skill runs.
   On a Mac without the developer tools that can open the install dialog after setup.
3. **Python lock file.** `--exclude-newer` freezes resolution by date. A committed lock file
   (for example `uv.lock`) would also pin exact versions for the developer bootstrap and CI.
4. **Windows on ARM.** The setup could run x64 Python under emulation; not attempted because it
   could not be verified.
