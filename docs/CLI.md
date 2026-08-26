# Mucaro CLI

The `mucaro` CLI is the operator surface for local ingestion, retrieval-backed
answers, Planning Gate analysis, validation, and local serving.

## Install

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

After installation, use:

```bash
mucaro --help
mucaro --version
```

For one-off development without installing the console entry point:

```bash
PYTHONPATH=.:backend python3 -m backend.app.cli --help
```

## Commands

| Command | Purpose | Success Exit | Failure Exit |
|---|---|---:|---:|
| `mucaro ingest PATH` | Ingest one supported file or a folder | 0 | 2 |
| `mucaro ask QUERY` | Answer from ingested local sources | 0 | 2 or 3 |
| `mucaro plan REQUEST` | Analyze a task with the Planning Gate | 0 | 2 |
| `mucaro permissions COMMAND` | Manage user-directory access grants | 0 | 2 |
| `mucaro validate` | Validate JSON artifacts and schemas | 0 | 2 |
| `mucaro serve` | Start the local FastAPI server | 0 | 2 |

Global flags:

- `--version` prints the installed package version for fresh-clone and support
  receipts.

## Output Contracts

Use `--format json` when automation needs structured output.

Error payloads use:

```json
{
  "status": "ERROR_CODE",
  "detail": "human-readable detail"
}
```

Retrieval and answer failures keep the same top-level shape as successful
retrieval payloads where practical:

```json
{
  "status": "ERROR_NO_SOURCES",
  "query": "What is source lineage?",
  "retrieved_chunks": []
}
```

## Expected Failure States

| State | Meaning |
|---|---|
| `ERROR_INPUT_PATH_NOT_FOUND` | `ingest` path does not exist |
| `ERROR_INPUT_PATH_UNSUPPORTED` | `ingest` path is neither a file nor folder |
| `ERROR_DIRECTORY_PERMISSION_REQUIRED` | direct filesystem ingest needs a prior directory grant |
| `ERROR_PERMISSION_CONFIRMATION_REQUIRED` | a directory grant was requested without `--yes` in a non-interactive terminal |
| `ERROR_DIRECTORY_PERMISSION_TOO_BROAD` | a directory grant targeted the filesystem root |
| `ERROR_NO_SOURCES` | `ask` ran before any source chunks were available |
| `ERROR_NO_RETRIEVAL_CONTEXT` | sources exist but no chunk matches the query |
| `ERROR_NO_VALID_CHUNKS` | available chunks are malformed or empty |
| `ERROR_PERSISTENT_INDEX_MISSING` | `--strategy persistent` was requested before an index was built |
| `ERROR_PERSISTENT_INDEX_STALE` | `--strategy persistent` index does not match current chunks |
| `ERROR_INVALID_TOP_K` | `--top-k` is outside the supported range |
| `TOP_K_RANGE:1-20` | CLI argument validation rejected `ask --top-k` before retrieval ran |
| `TOP_K_MUST_BE_INTEGER` | CLI argument validation rejected a non-integer `ask --top-k` |
| `ERROR_INVALID_RETRIEVAL_STRATEGY` | retrieval strategy is outside the supported set |
| `PORT_RANGE:1-65535` | `serve --port` is outside the TCP port range |
| `CITATION_ERROR` | answer trace failed citation validation |

## Examples

```bash
mucaro ingest data/input --format text
mucaro --version
mucaro permissions grant ~/Documents/mucaro-sources --yes --format json
mucaro permissions list --format text
mucaro ingest ~/Documents/mucaro-sources --format json
mucaro ask "How does source lineage work?" --format json --no-persist
mucaro ask "How does source lineage work?" --strategy persistent --format text
mucaro plan "Add CLI capability" --input mucaro-ai --format text
mucaro validate --format text
mucaro serve --host 127.0.0.1 --port 8000
```

## Directory Permissions

Project-local paths under the repository root are accessible by default. Direct
filesystem paths outside the project root require an explicit local grant:

```bash
mucaro permissions grant ~/Documents/mucaro-sources --yes
mucaro permissions check ~/Documents/mucaro-sources/source.md
mucaro ingest ~/Documents/mucaro-sources
mucaro permissions revoke ~/Documents/mucaro-sources
```

The ledger is stored under `data/processed/directory_permissions.json`, which is
local runtime state and should not be committed. Interactive operators can use
`mucaro ingest PATH --grant-directory-access` to receive a terminal prompt when
the path needs a new grant.

## Retrieval Strategies

`mucaro ask` defaults to `--strategy hybrid`, which recomputes deterministic lexical and TF-IDF scores from the current chunk file. Use `--strategy persistent` to query the local persisted TF-IDF index built during ingestion. Persistent retrieval fails closed when the index is missing or stale; run `mucaro ingest PATH` again to rebuild it.

## Live Model Mode

Mock mode is the default. To use Anthropic live mode, install the optional dependency group and opt in explicitly:

```bash
python -m pip install -e ".[anthropic]"
read -s ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY
export MUCARO_AI_LIVE_MODE=1
mucaro ask "How does source lineage work?" --provider anthropic
```

If the provider is unavailable, `ask` returns a structured provider error. Use `--allow-mock-fallback` only when an explicit mock fallback is acceptable.
