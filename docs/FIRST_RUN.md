# First Run Guide

## Recommended: double-click

Open `Mucaro AI Setup.app` (macOS) or double-click `Mucaro AI.cmd` (Windows). No other step is needed.
On a Mac the app shows the steps below in its own window and then turns into Mucaro AI, with no
Terminal or browser. `Mucaro AI.command` (macOS/Linux) and `Mucaro AI.cmd` show them in a
terminal window and open the app in the browser.

The first run prepares everything inside the project folder and then opens the app:

| Step | Marker when done | First run | Later runs |
|---|---|---|---|
| 0. Private uv, Node.js 22, Python 3.12 (SHA-256 verified), `.venv` | `UV_OK`, `NODE_OK`, `VENV_OK` | downloads | `cached` |
| 1. Python packages | `PY_DEPS_OK` | installs | `PY_DEPS_CACHED` |
| 2. Web interface packages | `FRONTEND_DEPS_OK` | `npm ci` | `FRONTEND_DEPS_CACHED` |
| 3. Web interface build | `FRONTEND_BUILD_OK` | builds | rebuilt only when `frontend/` changed |
| 4. `.env` and readiness check | `PREFLIGHT_OK` | runs | `PREFLIGHT_CACHED` |
| 5. Sample documents | `INGEST_FOLDER_OK` | loads `data/input` samples (skipped if you already have sources) | `SAMPLES_CACHED` |
| 6. Start and open the app (Mucaro AI Setup.app: in its window; otherwise the browser) | `DESKTOP_APP_READY` / `BACKEND_READY` | | |

`ONE_CLICK_SETUP_OK` marks a run that did setup work; `ONE_CLICK_FAST_PATH` marks a run that had
nothing to do. If port 8000 is taken by another program the launcher moves to the next free port
(`PORT_FALLBACK`); Mucaro AI Setup.app always uses a free random port. If Mucaro AI is already running,
a second double-click just brings it up (`DESKTOP_LAUNCH_REUSED`). If setup stops, the window names
the problem and the log file; fix it (usually the internet connection) and choose **Try again** in
the app, or double-click the launcher again: setup resumes where it stopped. Details and error
codes: `docs/ONE_CLICK_SETUP.md`.

## Manual developer path (macOS)

Use this only if you manage Python 3.11-3.13 and Node 20+ yourself.

From the folder containing `mucaro-ai/`:

```bash
cd mucaro-ai
bash scripts/bootstrap_macos.sh
```

Expected final output:

```text
PREFLIGHT_OK
RELEASE_CHECK_OK
BOOTSTRAP_OK
```

### Start the app (one command)

```bash
source .venv/bin/activate
python scripts/launch_app.py
```

Or double-click `Mucaro AI.command`, or run `make start`. The launcher builds
`frontend/dist` if it is missing, starts the backend on `127.0.0.1:8000`, opens
the browser, and stops everything on `Ctrl+C`. It reads `.env`; variables
exported in your shell take precedence.

Open:

```text
http://127.0.0.1:8000
```

If the page shows **Backend not reachable**, the launcher is not running (or is
on another port). Start it and press **Retry**.

### Developer mode (two processes, hot reload)

Start the backend:

```bash
source .venv/bin/activate
python scripts/dev_start.py --backend-only
```

Open this URL:

```text
http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"OK"}
```

In a second Terminal tab, start the frontend if Node/npm installed successfully:

```bash
cd mucaro-ai
source .venv/bin/activate
python scripts/dev_start.py --frontend-only
```

Open:

```text
http://127.0.0.1:5173
```

## Windows PowerShell path

```powershell
cd mucaro-ai
.\scripts\bootstrap_windows.ps1
```

Then start the app:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\launch_app.py
```

For developer mode, use `python scripts\dev_start.py --backend-only` and
`python scripts\dev_start.py --frontend-only` in two terminals.

## Manual verification commands

```bash
python scripts/preflight.py --strict-imports
python scripts/ingest_folder.py data/input
python scripts/ask.py "What is the Execution Gate?"
python scripts/validate_json.py
python scripts/release_check.py
python scripts/launch_app.py --smoke-test --no-browser
```

## Expected success markers

| Marker | Meaning |
|---|---|
| `PREFLIGHT_OK` | Tools, folders, and dependencies are ready enough |
| `INGEST_FOLDER_OK` | Sample file ingestion worked |
| `CITATION_OK` | The answer included source lineage |
| `JSON_OK` | JSON schemas and exports validated |
| `RELEASE_CHECK_OK` | The package passed local gates |
| `DESKTOP_SMOKE_OK` | Desktop launcher started, passed health check, stopped, and closed its port |
| `BOOTSTRAP_OK` | One-command setup completed |

## If something fails

Use the exact error code printed by the script. Do not continue after a failed bootstrap unless you understand the failure.

Common failures:

| Error | Fix |
|---|---|
| `ERROR_DOWNLOAD_FAILED` | Double-click setup: no internet connection during first setup; reconnect and double-click again |
| `ERROR_MACOS_TOO_OLD` / `ERROR_PLATFORM_UNSUPPORTED` | Double-click setup: see the supported platforms in `docs/ONE_CLICK_SETUP.md` |
| `ERROR_PYTHON_MISSING` | Install Python 3.11+ (`brew install python@3.12` or python.org) |
| `ERROR_PYTHON_TOO_OLD` | Bootstrap found only an older Python; install 3.11+ and rerun |
| `ERROR_PYTHON_UNSUPPORTED` | Developer bootstrap found only Python 3.14+; install 3.11-3.13, or use the double-click setup (it brings Python 3.12) |
| `ERROR_PYTHON_UNSUPPORTED_OR_MISSING` | Windows developer bootstrap: install Python 3.11-3.13 from python.org with "Add to PATH", or double-click `Mucaro AI.cmd` |
| `PYTHON_TOO_OLD` | Use Python 3.11+ explicitly |
| `BOOTSTRAP_FAILED step=...` | Windows: the named step failed; its output is printed just above |
| `ENV_MODEL_RETIRED` (warning) | Your `.env` pins a retired model; set `MUCARO_AI_MODEL=claude-sonnet-5` |
| `ERROR_NPM_MISSING` | Install Node 20+ or run backend-only |
| `PY_IMPORT_MISSING` | Run `python -m pip install -r backend/requirements.txt` inside `.venv` |
| `ERROR_FRONTEND_DEPS_MISSING` | Run `cd frontend && npm ci` |
| `ERROR_NO_SOURCES` | Run `python scripts/ingest_folder.py data/input` |
| `ERROR_PORT_IN_USE_NON_MUCARO_AI` | Choose another `--port` or stop the process using that port |

## First stable task

After release check passes, use the sample file to verify the app:

```bash
python scripts/ask.py "How does the Mucaro AI preserve source lineage?"
```

The answer should include cited chunk IDs and source IDs.

## If npm times out on an internal registry

Run this from the project root:

```bash
bash scripts/repair_npm_registry.sh
```

Expected marker:

```text
NPM_REGISTRY_REPAIR_OK
```

Then rerun:

```bash
bash scripts/bootstrap_macos.sh
```
