# First Run Guide

This guide is the safest path before opening the full app.

## macOS recommended path

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
| `ERROR_PYTHON_MISSING` | Install Python 3.11+ (`brew install python@3.12` or python.org) |
| `ERROR_PYTHON_TOO_OLD` | Bootstrap found only an older Python; install 3.11+ and rerun |
| `ERROR_PYTHON_TOO_OLD_OR_MISSING` | Windows: install Python 3.11+ from python.org with "Add to PATH" |
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
