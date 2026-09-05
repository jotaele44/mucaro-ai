# Mucaro AI

Local-first prototype for a custom LLM workflow with file ingestion, source lineage, cited retrieval, mock/Claude model routing, and a Planning-to-Execution Gate.

## Fastest first run

### macOS/Linux

```bash
cd mucaro-ai
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
cd mucaro-ai
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
python -m pip install -e ".[dev]"
python scripts/release_check.py
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\release_check.py
```

## Optional environment variables

Copy `.env.example` to `.env` and fill only what you need.

- `ANTHROPIC_API_KEY`: required for Anthropic mode when `MUCARO_AI_LIVE_MODE=1`.
- `MUCARO_AI_LIVE_MODE=1`: permits live Anthropic calls. Default is deterministic mock mode.
- `MUCARO_AI_MODEL_PROVIDER`: default model provider. Default is `mock`.
- `MUCARO_AI_MODEL`: model name for live mode.
- `MUCARO_AI_MAX_UPLOAD_BYTES`: API upload limit. Default is 25 MB.
- `MUCARO_AI_CORS_ORIGINS`: comma-separated browser origins for local frontend development.
- `MUCARO_AI_OPERATOR_TOKEN`: optional local operator token required for mutating API actions when set.
- `MUCARO_AI_WORKSPACE_PROFILE`: runtime workspace profile name. Default is `default`.
- `MUCARO_AI_DEPLOYMENT_PROFILE`: `local` or `production`. Production mode requires a strong operator token and explicit CORS origins.
- `MUCARO_AI_ALLOW_PUBLIC_BIND=1`: explicitly permits non-loopback binding in local mode.

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

Supported v1 types: TXT, MD, CSV, JSON, PDF with pypdf installed, plus
metadata-only media ingestion for PNG, JPEG, GIF, WEBP, WAV, MP3, M4A, MP4,
MOV, and WEBM. PDF parsing is marked partial if the parser is unavailable or
text extraction fails. Media parsing records local file metadata for lineage and
retrieval, but does not claim semantic image, audio, or video understanding
until a multimodal model adapter is added.

## Ask a question

```bash
python scripts/ask.py "How does source lineage work?"
```

## Command line interface

The CLI exposes the same local ingestion, planning, validation, and cited-answer flow used by the app.
See `docs/CLI.md` for exit codes, retrieval strategies, structured error payloads, and output contracts.

```bash
python scripts/mucaro.py ingest data/input
python scripts/mucaro.py doctor
python scripts/mucaro.py certify status --format text
python scripts/mucaro.py certify status --include-github --format text
python scripts/mucaro.py audit product --format text
python scripts/mucaro.py permissions grant ~/Documents/mucaro-sources --yes
python scripts/mucaro.py ingest ~/Documents/mucaro-sources
python scripts/mucaro.py sources list --format text
python scripts/mucaro.py index status --format json
python scripts/mucaro.py sources reindex --format text
python scripts/mucaro.py conversations create --title "Source lineage"
python scripts/mucaro.py conversations ask CONVERSATION_ID "How does source lineage work?" --strategy persistent
python scripts/mucaro.py runs summary --format text
python scripts/mucaro.py memory remember "Prefer concise technical answers" --kind preference
python scripts/mucaro.py memory list --format text
python scripts/mucaro.py security status --host 127.0.0.1 --format text
python scripts/mucaro.py tools list --format text
python scripts/mucaro.py tools request source_library.status --reason "readiness check" --format json
python scripts/mucaro.py ask "How does source lineage work?" --format text
python scripts/mucaro.py ask "How does source lineage work?" --strategy bm25 --format text
python scripts/mucaro.py ask "How does source lineage work?" --strategy persistent --format text
python scripts/mucaro.py ask "How does source lineage work?" --strategy semantic --format text
python scripts/mucaro.py plan "Add a CLI capability" --input mucaro-ai
python scripts/mucaro.py validate
python scripts/mucaro.py serve --reload
```

After `python -m pip install -e ".[dev]"`, the installed console command is available:

```bash
mucaro --version
mucaro doctor
mucaro certify status --format text
mucaro certify status --include-github --format text
mucaro audit product --format text
mucaro ingest data/input
mucaro permissions grant ~/Documents/mucaro-sources --yes
mucaro ingest ~/Documents/mucaro-sources
mucaro sources list --format text
mucaro index status --format json
mucaro sources reindex --format text
mucaro conversations create --title "Source lineage"
mucaro conversations ask CONVERSATION_ID "How does source lineage work?" --strategy persistent
mucaro runs summary --format text
mucaro memory remember "Prefer concise technical answers" --kind preference
mucaro memory list --format text
mucaro security status --host 127.0.0.1 --format text
mucaro tools list --format text
mucaro tools request source_library.status --reason "readiness check" --format json
mucaro ask "How does source lineage work?" --format text
mucaro ask "How does source lineage work?" --strategy bm25 --format text
mucaro ask "How does source lineage work?" --strategy persistent --format text
mucaro ask "How does source lineage work?" --strategy semantic --format text
mucaro plan "Add a CLI capability" --input mucaro-ai
mucaro validate
mucaro serve --reload
```

Equivalent module form:

```bash
PYTHONPATH=.:backend python -m backend.app.cli ingest data/input
PYTHONPATH=.:backend python -m backend.app.cli ask "How does source lineage work?" --format text
PYTHONPATH=.:backend python -m backend.app.cli plan "Add a CLI capability" --input mucaro-ai
PYTHONPATH=.:backend python -m backend.app.cli validate
PYTHONPATH=.:backend python -m backend.app.cli serve --reload
```

The `ask` command exports answer traces under `data/exports/` and stores runs in the local SQLite database by default. Use `--no-persist` for a terminal-only answer trace. The default retrieval strategy is `hybrid`; `--strategy bm25` uses local BM25 ranking, `--strategy persistent` uses the local TF-IDF index, and `--strategy semantic` uses the local embedding store rebuilt during ingestion. Persistent and semantic retrieval fail closed if their stores are missing or stale.

Direct filesystem ingestion outside the project directory is permission-gated. Grant a containing directory before ingesting user-directory paths, or use `ingest --grant-directory-access` from an interactive terminal to prompt at ingestion time. See `docs/DIRECTORY_PERMISSIONS.md`.

Use `mucaro doctor` to check local assistant readiness across source ingestion,
index health, semantic-store health, directory grants, model-provider setup,
API auth posture, saved conversations, explicit memory, production-security
posture, and answer runs.

Use `mucaro certify status` to inspect release readiness before running heavier
release or fresh-clone certification scripts. The command combines product
audit, doctor, JSON validation, Git metadata, worktree cleanliness, tag-at-HEAD,
and upstream checks into a structured operator receipt. Add `--include-github`
to include read-only GitHub governance checks for branch protection, latest CI
on the current branch, and remote tag binding when `gh` is authenticated. The
default hosted workflow is `CI`; use `--github-workflow NAME` if the release
workflow is renamed. If the latest hosted CI run is for the current HEAD and
concludes unsuccessfully, the GitHub CI gate fails.

When hosted CI fails before jobs or logs exist, run `mucaro certify
github-actions --format text` for a read-only diagnostic receipt over
repository Actions permissions, workflow registration, latest CI/probe runs,
job counts, and billing endpoint access. This keeps GitHub startup triage in
the same operator CLI boundary as release certification.

Use `mucaro audit product` to track what is complete versus still open against
the full ChatGPT/Claude-like local assistant target.

Use `mucaro eval retrieval --format text` to run the committed retrieval
quality fixture from the supported CLI. It reports per-strategy recall,
top-1 hit rate, no-context accuracy, fixture counts, and threshold settings;
release automation still calls `scripts/evaluate_retrieval.py`, which delegates
to the same evaluator module.

Use `mucaro package status --format text` to inspect package metadata, version
consistency, launcher entrypoints, desktop package manifest readiness, optional
generated outputs, and distribution blockers without writing artifacts.

The browser app includes the same permission workflow in the Directory Access panel.

The CLI can also inspect and maintain the local source library: `mucaro sources
list`, `mucaro index status`, `mucaro sources reindex`, and `mucaro sources
delete SOURCE_ID`.

The installed CLI also manages saved chat sessions and run history with
`mucaro conversations ...` and `mucaro runs ...`, matching the app's durable
conversation and answer-trace model.

The CLI also exposes the local tool runtime with `mucaro tools ...`. Tool calls
are deny-by-default: request a tool call, approve or deny the pending
`tool_call_id`, then run only approved calls. Every request, decision, and
execution is recorded in the local tool audit log.

The CLI and browser app include explicit local memory controls. Saved memory
items are opt-in only, can be enabled or disabled, can be deleted, and can be
exported as JSON. Enabled memory can shape answer preferences or profile
context, but cited chunks remain the only source evidence for factual answers.

The browser app also persists local chat sessions. Each conversation keeps ordered user and assistant turns, supports chat search, rename, and delete, uses recent turns for follow-up questions, and keeps cited answer traces exportable by `run_id`.

The API exposes local admin surfaces for source deletion, reindexing, run
detail, config inspection, workspace profiles, explicit memory, and approved
tool calls. When `MUCARO_AI_OPERATOR_TOKEN` is set, mutating routes require
`Authorization: Bearer <token>` or `X-Mucaro-Token: <token>`.

Production security is opt-in and fail-closed. Set
`MUCARO_AI_DEPLOYMENT_PROFILE=production`, a high-entropy
`MUCARO_AI_OPERATOR_TOKEN`, and explicit `MUCARO_AI_CORS_ORIGINS` before broader
network exposure. Local mode blocks non-loopback `mucaro serve --host` values
unless `MUCARO_AI_ALLOW_PUBLIC_BIND=1` is set.

Live Anthropic mode is explicit:

```bash
python -m pip install -e ".[anthropic]"
read -s ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY
export MUCARO_AI_LIVE_MODE=1
mucaro ask "How does source lineage work?" --provider anthropic
```

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

## Certification gate

Use the certification gate before pushing release candidates. It combines source-boundary scans, tests, JSON validation, frontend audit, and frontend build:

```bash
npm ci --registry=https://registry.npmjs.org/ --prefix frontend
python3 scripts/certify_release.py
```

Expected success:

```text
CERTIFY_RELEASE_OK
```

The certification gate also runs the committed retrieval evaluation fixture:

```bash
python3 scripts/evaluate_retrieval.py
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
- Tool calls require explicit approval and are recorded in a local audit log.
- Uploaded files are stored locally under `data/input/`.
- Set `MUCARO_AI_OPERATOR_TOKEN` before exposing the local API beyond a trusted loopback-only development session.
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
