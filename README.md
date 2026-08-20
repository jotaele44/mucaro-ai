# Custom LLM Lab

Local-first prototype for a custom LLM workflow with file ingestion, source lineage, cited retrieval, mock/Claude model routing, and a Planning-to-Execution Gate.

## Fastest first run

### macOS/Linux

```bash
cd custom-llm-lab
bash scripts/bootstrap_macos.sh
```

Expected final markers:

```text
PREFLIGHT_OK
RELEASE_CHECK_OK
BOOTSTRAP_OK
```

### Windows PowerShell

```powershell
cd custom-llm-lab
.\scripts\bootstrap_windows.ps1
```

See `docs/FIRST_RUN.md` for step-by-step startup and error recovery.

## Safe startup helpers

```bash
# Check tools, folders, .env, and imports
python scripts/preflight.py --strict-imports

# Start backend only
python scripts/dev_start.py --backend-only

# Start frontend only after npm ci
python scripts/dev_start.py --frontend-only

# Start both services after npm ci
python scripts/dev_start.py
```

## What this is

- A runnable local prototype.
- A clean-room implementation inspired by public RAG/Claude architecture patterns.
- A safe workflow controller that separates planning from execution.

## What this is not

- Not a trained foundation model.
- Not a production multi-user SaaS.
- Not a clone of Dify, AnythingLLM, RAGFlow, Open WebUI, LlamaIndex, or GraphRAG.
- Not a tool that executes destructive actions without approval.

## Setup

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
python scripts/release_check.py
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
python scripts\release_check.py
```

## Optional environment variables

Copy `.env.example` to `.env` and fill only what you need.

- `ANTHROPIC_API_KEY`: enables Claude mode when `CUSTOM_LLM_LIVE_MODE=1`.
- `CUSTOM_LLM_LIVE_MODE=1`: permits live Anthropic calls. Default is deterministic mock mode.
- `CUSTOM_LLM_MODEL`: model name for live mode.

## Run backend

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

## Run frontend

```bash
cd frontend
npm install
npm run dev
```

## Ingest files

```bash
python scripts/ingest_folder.py data/input
```

Supported v1 types: TXT, MD, CSV, JSON, PDF with pypdf installed. PDF parsing is marked partial if the parser is unavailable or text extraction fails.

## Ask a question

```bash
python scripts/ask.py "How does source lineage work?"
```

## Command line interface

The CLI exposes the same local ingestion, planning, validation, and cited-answer flow used by the app.

```bash
PYTHONPATH=.:backend python -m backend.app.cli ingest data/input
PYTHONPATH=.:backend python -m backend.app.cli ask "How does source lineage work?" --format text
PYTHONPATH=.:backend python -m backend.app.cli plan "Add a CLI capability" --input custom-llm-lab
PYTHONPATH=.:backend python -m backend.app.cli validate
```

The `ask` command exports answer traces under `data/exports/` and stores runs in the local SQLite database by default. Use `--no-persist` for a terminal-only answer trace.

## Validate JSON

```bash
python scripts/validate_json.py
```

Expected success:

```text
JSON_OK
```

## Release check

```bash
python scripts/release_check.py
```

Expected success:

```text
RELEASE_CHECK_OK
```

## Reference clones

The sandbox snapshot does not include cloned reference repositories because GitHub DNS was unavailable during generation. To clone locally:

```bash
bash scripts/clone_references.sh
python scripts/extract_reference_patterns.py
```

## Security notes

- No API keys are logged.
- Mock mode is default.
- Execution Gate approval is required before mutation-oriented execution.
- Uploaded files are stored locally under `data/input/`.
- Every answer trace must include source IDs and cited chunk IDs or validation flags.

## Phase 2 roadmap

See `docs/PHASE2_QUEUE.md`.


## NPM registry repair

If `npm ci` tries to fetch from an internal `packages.applied-caas...` URL or times out, run:

```bash
bash scripts/repair_npm_registry.sh
```

Then rerun:

```bash
bash scripts/bootstrap_macos.sh
```
