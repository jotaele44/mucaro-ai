# Architecture

```text
User Request
  → Execution Gate
  → Readiness Score
  → Mode Selector
  → Approval Lock
  → Conversation Session
  → File Ingestion
  → Source Manifest
  → Chunking
  → Retrieval
  → Model Router
  → Tool Runtime
  → Citation Validator
  → Answer Trace Export
```

## Control layer

The Execution Gate is the workflow controller. It prevents premature execution by requiring a planning state, readiness score, mode alternatives, and approval trace.

## Data layer

- SQLite: metadata, conversation sessions, and run records.
- JSON files: inspectable manifest, chunks, schemas, exports.
- Local folders: `data/input`, `data/processed`, `data/exports`, `data/indexes`.
- API upload boundary: sanitizes upload filenames, rejects traversal/generated/unsupported names, and enforces `MUCARO_AI_MAX_UPLOAD_BYTES` with a 25 MB default.
- Directory permission boundary: direct path ingestion outside the project root requires a local grant recorded under `data/processed/directory_permissions.json`.
- Conversation records: local chat sessions store ordered user/assistant turns and reference answer traces by `run_id`.
- Workspace profiles: local JSON records under `data/processed/workspace_profiles.json` describe named workspace/runtime profiles.
- Tool runtime records: `data/processed/tool_calls.json` stores current tool-call state; `data/processed/tool_audit_log.json` stores request, approval, denial, and execution audit events.
- Source library controls: operators can reindex all chunks, inspect persisted index status, and delete managed sources while rebuilding the index from both the API/browser surface and the installed CLI.
- Run history: `/runs`, `/runs/summary`, and `/runs/{run_id}` provide scan-friendly history plus full trace retrieval.
- Parser evidence: source manifests include byte counts, parser warnings, parse status, and format metadata such as CSV delimiter/header/row counts.
- Multimodal metadata boundary: image/audio/video ingestion records local media
  metadata and warnings as cited source chunks; semantic media understanding is
  deferred to a future model-backed adapter.

## Operator layer

The API is local-first and loopback-oriented by default. If `MUCARO_AI_OPERATOR_TOKEN` is configured, mutating routes require either `Authorization: Bearer <token>` or `X-Mucaro-Token: <token>`.

Read-only inspection routes expose health, config, sources, chunks, conversations, runs, workspace profiles, tools, tool audit events, and exports. Mutating routes include planning/approval writes, upload/path ingestion, directory grant changes, source deletion/reindexing, chat creation/asking, workspace profile saves, and tool-call request/approval/execution.

The CLI `doctor` command provides a read-only local readiness report over the
same operational domains: paths, sources, chunks, persistent index status,
directory grants, model-provider setup, API auth posture, conversations, runs,
and the local tool runtime.

The CLI `audit product` command provides a broader completion matrix for the
ChatGPT/Claude-like local assistant objective. It intentionally reports OPEN
while personalized memory or production hardening remain incomplete. The
multimodal requirement is scoped to metadata ingestion in this release; rich
image, audio, and video understanding remains a model-layer upgrade.

## Tool layer

The tool runtime is deny-by-default. Operators can list locally allowlisted
tools, create a pending request for a specific tool and JSON argument object,
approve or deny that request, then execute only an approved `tool_call_id`.
Unknown tools are rejected and recorded as audit events.

The initial allowlist is intentionally read-only:

- `source_library.status`
- `product.audit`
- `workspace_profiles.list`

Broader write/action tools should be added one at a time with explicit policy,
operator UX, and regression tests for approval and audit behavior.

## Packaging layer

The desktop package flow is validated by `scripts/package_desktop.py --self-test`. It verifies launcher, bootstrap, frontend lockfile, and package metadata inputs without writing generated artifacts during certification.

## Conversation layer

Conversations are local SQLite-backed chat sessions. The `/conversations` API creates and lists sessions; `PATCH /conversations/{conversation_id}` renames sessions; `DELETE /conversations/{conversation_id}` removes a session record; `/conversations/{conversation_id}/ask` routes through the same cited RAG service as `/ask`, passes recent turns as bounded conversation context, and appends the user turn plus assistant answer to the selected session.

Answer traces remain separately stored under `runs` and `data/exports/`, so chat history does not replace citation auditability. Deleting a conversation does not delete the answer traces it previously referenced. The API, browser, and CLI all use this same separation. Prior turns help interpret follow-up questions but are not treated as source evidence; chunk IDs and source IDs still come from retrieval.

## Retrieval layer

V1 uses deterministic local hybrid retrieval:

- `lexical_v2`: token overlap, cosine normalization, query coverage, stable tie order.
- `vector_tfidf_v1`: local TF-IDF vectors with cosine similarity and no external service requirement.
- `bm25_v1`: local BM25 ranking with term-frequency and document-length normalization.
- `hybrid_tfidf_v1`: the default answer path, combining lexical and vector scores behind the stable retrieval result contract.
- `persistent_tfidf_v1`: opt-in persisted local TF-IDF index stored under `data/indexes/tfidf_index.json`, rebuilt during ingestion and rejected when stale.
- `local_semantic_hash_store_v1`: opt-in persisted local embedding store stored under `data/indexes/semantic_store.json`, rebuilt during ingestion and rejected when stale.
- `scripts/evaluate_retrieval.py`: committed retrieval fixture gate that reports recall@k, top-1 hit rate, and no-context accuracy for lexical/vector/BM25/hybrid/persistent/semantic strategies.

Higher-quality embedding providers or stores such as Chroma can still be added behind this interface later without changing the answer trace schema.

## Model layer

Mock mode is the deterministic default. Live Anthropic calls require explicit `model_provider="anthropic"`, `MUCARO_AI_LIVE_MODE=1`, `ANTHROPIC_API_KEY`, and the optional `.[anthropic]` dependency group unless the operator explicitly permits mock fallback.
