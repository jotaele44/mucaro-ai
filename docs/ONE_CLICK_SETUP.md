# Double-Click Setup

Double-click `Mucaro AI Setup.app` (macOS) beside `pyproject.toml`, or `Mucaro AI.cmd` (Windows).
On macOS, the app provisions private runtimes and builds the UI quietly, then opens its native
window. No Terminal or browser launches. First setup requires internet access; completed steps
are cached. Errors appear in a dialog with the startup log path. Keep the whole checkout together
when moving it. The `.command` file remains a legacy console path.

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

The macOS app runs `scripts/native_start.sh`, which redirects startup output to a log and runs
`scripts/one_click.sh --native`. The stages below are shared with the legacy console launchers.
On macOS, the Python install includes the desktop extra and checks `webview`, `AppKit`,
`Foundation`, and `WebKit` before recording a successful setup fingerprint.

```
Mucaro AI.command (macOS/Linux)          Mucaro AI.cmd (Windows)
        | exec                                   | powershell -ExecutionPolicy Bypass
        v                                        v
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
  (`PORT_FALLBACK`). The macOS app uses `--native` and activates an existing native window
  (`DESKTOP_LAUNCH_REUSED`) when available. Closing the window stops the backend it owns.
  The legacy console launchers retain browser mode.

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
| `.mucaro-runtime/logs/launch-*.log` | the last 10 silent app startup/backend logs |
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

## Limits that code cannot remove

| Limit | Effect | Tier | Confidence |
|---|---|---|---|
| A ZIP downloaded from GitHub carries the macOS quarantine flag (Windows: Mark-of-the-Web) | Gatekeeper or SmartScreen asks once before the first run. Cloning with `git` or GitHub Desktop avoids it. Removing it for ZIP downloads needs a signed and notarized installer. | T4 | 90% |
| Project inside `~/Desktop`, `~/Documents`, or `~/Downloads` on macOS | macOS asks once whether Terminal may access that folder. `~/Developer` is not affected. | T4 | 85% |
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

## Blind spots

- No physical Mac or Windows PC was used; macOS and Windows behaviour is proven on GitHub-hosted
  runners, which have developer tools installed. The tripwire stubs show those tools are not used,
  but the Command Line Tools dialog itself was not reproduced.
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
