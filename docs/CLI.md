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
| `mucaro validate` | Validate JSON artifacts and schemas | 0 | 2 |
| `mucaro serve` | Start the local FastAPI server | 0 | 2 |

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
| `ERROR_NO_SOURCES` | `ask` ran before any source chunks were available |
| `ERROR_NO_RETRIEVAL_CONTEXT` | sources exist but no chunk matches the query |
| `ERROR_NO_VALID_CHUNKS` | available chunks are malformed or empty |
| `ERROR_INVALID_TOP_K` | `--top-k` is outside the supported range |
| `ERROR_INVALID_RETRIEVAL_STRATEGY` | retrieval strategy is outside the supported set |
| `PORT_RANGE:1-65535` | `serve --port` is outside the TCP port range |
| `CITATION_ERROR` | answer trace failed citation validation |

## Examples

```bash
mucaro ingest data/input --format text
mucaro ask "How does source lineage work?" --format json --no-persist
mucaro plan "Add CLI capability" --input mucaro-ai --format text
mucaro validate --format text
mucaro serve --host 127.0.0.1 --port 8000
```
