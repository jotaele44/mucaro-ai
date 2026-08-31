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
| `mucaro doctor` | Check local assistant readiness | 0 | 2 with `--strict` warnings/failures |
| `mucaro audit product` | Audit product gaps against the full assistant objective | 0 | 2 with `--strict` while gaps remain |
| `mucaro ingest PATH` | Ingest one supported file or a folder | 0 | 2 |
| `mucaro ask QUERY` | Answer from ingested local sources | 0 | 2 or 3 |
| `mucaro sources list` | List ingested sources, persistent index status, and semantic-store status | 0 | 2 |
| `mucaro sources reindex` | Rebuild the local persistent index and semantic store | 0 | 2 |
| `mucaro sources delete SOURCE_ID` | Delete one ingested source and rebuild the index | 0 | 2 |
| `mucaro index status` | Inspect local persistent index and semantic-store health | 0 | 2 |
| `mucaro conversations list` | List saved local chat sessions | 0 | 2 |
| `mucaro conversations create` | Create a saved local chat session | 0 | 2 |
| `mucaro conversations ask CONVERSATION_ID QUERY` | Ask with saved conversation context | 0 | 2 or 3 |
| `mucaro conversations show CONVERSATION_ID` | Show a saved conversation and its turns | 0 | 2 |
| `mucaro conversations rename CONVERSATION_ID TITLE` | Rename a saved conversation | 0 | 2 |
| `mucaro conversations delete CONVERSATION_ID` | Delete a saved conversation while preserving runs | 0 | 2 |
| `mucaro runs list` | List persisted answer traces | 0 | 2 |
| `mucaro runs summary` | Summarize persisted answer traces | 0 | 2 |
| `mucaro runs show RUN_ID` | Show one persisted answer trace | 0 | 2 |
| `mucaro memory list` | List explicit local memory items | 0 | 2 |
| `mucaro memory remember CONTENT` | Save an explicit memory item | 0 | 2 |
| `mucaro memory enable MEMORY_ID` | Re-enable one memory item | 0 | 2 |
| `mucaro memory disable MEMORY_ID` | Disable one memory item without deleting it | 0 | 2 |
| `mucaro memory delete MEMORY_ID` | Delete one memory item | 0 | 2 |
| `mucaro memory export` | Export all explicit memory items | 0 | 2 |
| `mucaro security status` | Inspect local or production security readiness | 0 | 2 |
| `mucaro tools list` | List locally allowlisted tools | 0 | 2 |
| `mucaro tools request TOOL_NAME` | Create a pending tool-call request | 0 | 2 |
| `mucaro tools approve TOOL_CALL_ID` | Approve a pending tool call | 0 | 2 |
| `mucaro tools deny TOOL_CALL_ID` | Deny a pending tool call | 0 | 2 |
| `mucaro tools run TOOL_CALL_ID` | Execute an approved tool call | 0 | 2 |
| `mucaro tools audit` | Inspect local tool runtime audit events | 0 | 2 |
| `mucaro plan REQUEST` | Analyze a task with the Planning Gate | 0 | 2 |
| `mucaro permissions COMMAND` | Manage user-directory access grants | 0 | 2 |
| `mucaro validate` | Validate JSON artifacts and schemas | 0 | 2 |
| `mucaro serve` | Start the local FastAPI server | 0 | 2 |

Global flags:

- `--version` prints the installed package version for fresh-clone and support
  receipts.

Supported ingest inputs are TXT, MD, CSV, JSON, PDF, PNG, JPEG, GIF, WEBP, WAV,
MP3, M4A, MP4, MOV, and WEBM. Media files are metadata-only in this release:
they preserve source lineage, byte counts, dimensions or container facts where
available, and parser warnings, but do not perform semantic image/audio/video
understanding.

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
| `ERROR_SEMANTIC_STORE_MISSING` | `--strategy semantic` was requested before a semantic store was built |
| `ERROR_SEMANTIC_STORE_STALE` | `--strategy semantic` store does not match current chunks |
| `ERROR_INVALID_TOP_K` | `--top-k` is outside the supported range |
| `TOP_K_RANGE:1-20` | CLI argument validation rejected `ask --top-k` before retrieval ran |
| `TOP_K_MUST_BE_INTEGER` | CLI argument validation rejected a non-integer `ask --top-k` |
| `INDEX_MISSING` | `index status` found no persistent index |
| `INDEX_STALE` | `index status` found an index that no longer matches current chunks |
| `ERROR_SOURCE_NOT_FOUND` | `sources delete` was given an unknown source ID |
| `ERROR_CONVERSATION_NOT_FOUND` | a conversation command was given an unknown conversation ID |
| `ERROR_RUN_NOT_FOUND` | `runs show` was given an unknown run ID |
| `ERROR_MEMORY_NOT_FOUND` | a memory command was given an unknown memory ID |
| `ERROR_MEMORY_KIND_UNSUPPORTED` | memory kind is not one of the supported types |
| `ERROR_MEMORY_CONTENT_REQUIRED` | memory content was empty after normalization |
| `ERROR_MEMORY_CONTENT_TOO_LONG` | memory content exceeded the local limit |
| `ERROR_SECURITY_PROFILE_NOT_READY` | production or public-bind security checks failed |
| `ERROR_TOOL_NOT_ALLOWED` | requested tool is not in the local allowlist |
| `ERROR_TOOL_APPROVAL_REQUIRED` | tool execution was attempted before approval |
| `ERROR_TOOL_CALL_NOT_FOUND` | tool command was given an unknown tool call ID |
| `ERROR_TOOL_CALL_NOT_PENDING` | approval or denial was attempted after the pending state |
| `ERROR_TOOL_CALL_DENIED` | denied tool call cannot be executed |
| `ERROR_TOOL_CALL_ALREADY_EXECUTED` | tool call execution was attempted more than once |
| `DOCTOR_WARN` | readiness check found missing or incomplete local setup |
| `DOCTOR_FAIL` | readiness check found unsupported or invalid local setup |
| `PRODUCT_AUDIT_OPEN` | product audit found remaining ChatGPT/Claude-comparability gaps |
| `PRODUCT_AUDIT_BLOCKED` | product audit found a blocked requirement |
| `ERROR_INVALID_RETRIEVAL_STRATEGY` | retrieval strategy is outside the supported set |
| `PORT_RANGE:1-65535` | `serve --port` is outside the TCP port range |
| `CITATION_ERROR` | answer trace failed citation validation |

## Examples

```bash
mucaro ingest data/input --format text
mucaro --version
mucaro doctor
mucaro audit product --format text
mucaro permissions grant ~/Documents/mucaro-sources --yes --format json
mucaro permissions list --format text
mucaro ingest ~/Documents/mucaro-sources --format json
mucaro sources list --format text
mucaro index status --format json
mucaro sources reindex --format text
mucaro conversations create --title "Source lineage" --format json
mucaro conversations list --format text
mucaro conversations ask CONVERSATION_ID "How does source lineage work?" --strategy persistent --format json
mucaro runs summary --format text
mucaro runs show RUN_ID --format json
mucaro memory remember "Prefer concise technical answers" --kind preference --format json
mucaro memory list --format text
mucaro memory disable MEMORY_ID --format json
mucaro memory export --format json
mucaro security status --host 127.0.0.1 --format text
mucaro tools list --format text
mucaro tools request source_library.status --reason "readiness check" --format json
mucaro tools approve TOOL_CALL_ID --format json
mucaro tools run TOOL_CALL_ID --format json
mucaro tools audit --format text
mucaro ask "How does source lineage work?" --format json --no-persist
mucaro ask "How does source lineage work?" --strategy persistent --format text
mucaro ask "How does source lineage work?" --strategy semantic --format text
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

## Diagnostics

Use `mucaro doctor` to inspect local readiness without ingesting files or
granting access. The report checks project paths, local data directories,
source/chunk counts, persistent index status, semantic-store status, directory
grants, model-provider readiness, API auth posture, conversations, runs,
explicit memory, production-security posture, and the local tool runtime. Add `--strict` when
automation should fail on warnings.

Use `mucaro audit product` to classify the broader assistant objective as
requirement rows with `PASS`, `OPEN`, or `BLOCKED` status. Add `--strict` when
automation should fail while any product gap remains open.

## Retrieval Strategies

`mucaro ask` defaults to `--strategy hybrid`, which recomputes deterministic lexical and TF-IDF scores from the current chunk file. Use `--strategy bm25` for local BM25 ranking, `--strategy persistent` to query the local persisted TF-IDF index, or `--strategy semantic` to query the local embedding store built during ingestion. Persistent and semantic retrieval fail closed when their stores are missing or stale; run `mucaro sources reindex` after ingesting sources to rebuild them.

Use `mucaro index status` to check whether the persistent index and semantic
store are usable. Use `mucaro sources reindex` to rebuild them from current
chunks without ingesting new files. Use `mucaro sources list` to inspect the local source library and
`mucaro sources delete SOURCE_ID` to remove one source from the assistant's
knowledge base.

## Conversation And Run History

Use `mucaro conversations create` to start a saved chat session and `mucaro
conversations ask CONVERSATION_ID QUERY` to ask follow-up questions with recent
turn context. Conversation deletion only removes the chat session; persisted
answer traces remain inspectable through `mucaro runs list`, `mucaro runs
summary`, and `mucaro runs show RUN_ID`.

## Explicit Memory

Use `mucaro memory remember CONTENT` to save a user-approved preference, profile
detail, instruction, or fact. Memory is never inferred from conversations in
this release. Enabled memory is passed to the model as preference/profile
context and recorded in answer traces by `memory_id`; it is not treated as cited
source evidence.

```bash
mucaro memory remember "Prefer concise technical answers" --kind preference
mucaro memory list --active-only --format text
mucaro memory disable MEMORY_ID
mucaro memory enable MEMORY_ID
mucaro memory export --format json
mucaro memory delete MEMORY_ID
```

## Security Profile

Use `mucaro security status` to inspect whether the app is safe for the chosen
runtime posture. Local mode is loopback-oriented. Non-loopback `mucaro serve
--host` values fail unless `MUCARO_AI_ALLOW_PUBLIC_BIND=1` is set. Production
mode requires a high-entropy `MUCARO_AI_OPERATOR_TOKEN` and explicit
`MUCARO_AI_CORS_ORIGINS`.

```bash
mucaro security status --host 127.0.0.1 --format text
MUCARO_AI_DEPLOYMENT_PROFILE=production \
MUCARO_AI_OPERATOR_TOKEN="replace-with-a-long-random-token" \
MUCARO_AI_CORS_ORIGINS="https://mucaro.example" \
mucaro security status --format json
```

## Tool Runtime

`mucaro tools ...` is the local approval-gated tool surface. The runtime is
deny-by-default: unknown tools are rejected, known tools start as
`PENDING_APPROVAL`, and `mucaro tools run TOOL_CALL_ID` fails until the call is
approved.

Initial allowlisted tools are read-only:

- `source_library.status`
- `product.audit`
- `workspace_profiles.list`
- `memory.list`

Tool call state is stored under `data/processed/tool_calls.json`. Audit events
are stored under `data/processed/tool_audit_log.json`. These files are local
runtime state and should not be committed.

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
