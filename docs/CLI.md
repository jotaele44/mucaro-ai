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
| `mucaro release state` | Classify bounded release-state progress | 0 | 2 with `--strict` until governed release is ready |
| `mucaro release receipts` | List local release/fresh-clone certification receipts | 0 | 2 |
| `mucaro release receipt RECEIPT_ID` | Show one local certification receipt | 0 | 2 |
| `mucaro certify status` | Inspect release readiness before running full certification gates | 0 | 2 on failures, or with `--strict` warnings/failures |
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
| `mucaro package status` | Inspect packaging and distribution readiness | 0 | 2 on failures, or with `--strict` warnings/failures |
| `mucaro package build` | Build the governed source ZIP and artifact manifest | 0 | 2 |
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
| `RELEASE_STATE_OPEN` | release-state classifier found open or unverified release states |
| `RELEASE_STATE_READY` | release-state classifier found the governed release state ready |
| `RELEASE_RECEIPTS_OK` | release receipt listing completed |
| `RELEASE_RECEIPT_OK` | release receipt inspection completed |
| `ERROR_RELEASE_RECEIPT_NOT_FOUND` | requested release receipt does not exist |
| `CERTIFICATION_WARN` | release readiness has warnings such as local-mode auth disabled, missing tag, missing upstream, unavailable Git metadata, or unenforced GitHub governance |
| `CERTIFICATION_FAIL` | release readiness has a hard blocker such as product audit failure, JSON validation failure, or dirty worktree |
| `GITHUB_ACTIONS_DIAGNOSTIC_WARN` | hosted Actions diagnostic found incomplete read-only evidence but no hard failure |
| `GITHUB_ACTIONS_DIAGNOSTIC_FAIL` | hosted Actions diagnostic found a hard failure, such as current-head CI startup failure before jobs |
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
mucaro release state --include-github --format text
mucaro release receipts --current-head --format text
mucaro release receipt local_release-HEADPREFIX --format json
mucaro certify status --format text
mucaro certify status --include-github --format text
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

Use `mucaro release state` to classify the current repo against the bounded
release path: `CANONICAL_LOCAL`, `LOCAL_RELEASE_CERTIFIED`,
`FRESH_CLONE_CERTIFIED`, `SOURCE_ARTIFACT_PACKAGING`,
`GITHUB_GOVERNANCE_OPEN`, `GOVERNED_RELEASE_READY`, and `PRODUCTION_READY`.
The command reuses existing audit, package, Git, and optional GitHub governance
receipts. It does not run heavy certification scripts, so those states remain
`UNVERIFIED` until the operator runs the reported commands and creates matching
exact-HEAD receipts under `data/exports/release_receipts`. Add `--include-github`
when authenticated `gh` evidence is available.

Use `mucaro release receipts` to list local release and fresh-clone
certification receipts. Add `--current-head` to filter the list to receipts
whose `head_sha` matches the current Git `HEAD`. Each row includes receipt
validity, receipt type, certification status, bound head SHA, and receipt file
SHA-256. The listing also summarizes valid/invalid counts by receipt type and
status. Use `mucaro release receipt RECEIPT_ID` to inspect the full JSON receipt
by file stem or filename.

Use `mucaro certify status` before tagging or pushing release candidates. It is
a read-only readiness receipt over product audit, doctor, JSON validation, Git
HEAD, worktree cleanliness, tag-at-HEAD, and upstream configuration. Add
`--strict` when automation should fail on warnings, then run the reported
`scripts/certify_release.py` and `scripts/certify_fresh_clone.py` commands for
the heavier gates. Add `--include-github` when network access and authenticated
`gh` are available to include branch protection, latest hosted CI, and remote
tag-binding checks. GitHub governance checks are read-only and never mutate
repository settings. The default hosted workflow name is `CI`; use
`--github-workflow NAME` with `--include-github` if the release workflow is
renamed. A completed hosted CI run for the current HEAD must conclude
successfully; an unsuccessful completed run is a failed certification gate. If
the current-head run reports `startup_failure` with `jobs_count=0`, the JSON
payload marks `github.latest_ci.startup_layer` as `before_jobs` and adds a
`github_hosted_runner_startup` failure gate. That gate is diagnostic evidence
for GitHub Actions billing, spending-limit, runner-availability, or account
policy checks; it does not replace the required `github_latest_ci` pass.

Use `mucaro certify github-actions` when the GitHub governance gate fails and
the operator needs a narrower hosted-runner diagnostic receipt. The command
checks repository Actions permissions, workflow token permissions, workflow
registration, the latest named release workflow, the optional startup probe
workflow, job counts, and account billing endpoint access. It is read-only and
returns `GITHUB_ACTIONS_DIAGNOSTIC_FAIL` when hosted startup is isolated outside
the project build/test stack.

Use `mucaro audit product` to classify the broader governed assistant objective
as requirement rows with `PASS`, `OPEN`, or `BLOCKED` status. The audit includes
assistant capability rows plus release-governance rows, so an otherwise working
local build still reports `PRODUCT_AUDIT_OPEN` while hosted CI, branch
protection, or governed tag binding remains unresolved. Add `--strict` when
automation should fail while any product gap remains open.

Use `mucaro eval retrieval` to run the committed retrieval-quality fixture from
the supported CLI surface. The command reports the fixture path, chunk/query
counts, positive/no-context query counts, expected binding counts, fixture
validation checks, evaluated strategy count, recall thresholds, per-strategy
`recall@k`, top-1 hit rate, and no-context accuracy. It returns
`RETRIEVAL_EVAL_INVALID` for malformed benchmark fixtures and
`RETRIEVAL_EVAL_FAILED` when configured thresholds are not met, so release
automation can fail on invalid or regressed retrieval evidence.

Use `mucaro package status` to inspect packaging readiness without writing
artifacts. The command checks required package inputs, Python/frontend package
metadata, version consistency, the installed CLI entrypoint, desktop launcher
entrypoint, desktop package manifest readiness, optional generated outputs, and
known distribution blockers. It reports `PACKAGE_FAIL` for broken required
inputs, `PACKAGE_WARN` for incomplete distribution readiness, and
`PACKAGE_READY` only when no packaging warnings remain.

Use `mucaro package build` to create the current governed release artifact: a
tracked-source ZIP plus JSON manifest under `data/exports/packages`. The command
refuses dirty worktrees by default, records the exact Git `HEAD`, and includes
SHA-256 receipts for the archive and every tracked source member. Use
`--allow-dirty` only for diagnostics, not governed release tags.

## Retrieval Strategies

`mucaro ask` defaults to `--strategy hybrid`, which recomputes deterministic lexical and TF-IDF scores from the current chunk file. Use `--strategy bm25` for local BM25 ranking, `--strategy persistent` to query the local persisted TF-IDF index, or `--strategy semantic` to query the local embedding store built during ingestion. The local semantic hash store requires matched terms before returning a cited chunk, reducing false-positive context from hash similarity alone. Persistent and semantic retrieval fail closed when their stores are missing or stale; run `mucaro sources reindex` after ingesting sources to rebuild them.

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
