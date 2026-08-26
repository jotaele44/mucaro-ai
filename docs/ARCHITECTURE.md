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
- Source library controls: operators can reindex all chunks, inspect persisted index status, and delete managed sources while rebuilding the index.
- Run history: `/runs`, `/runs/summary`, and `/runs/{run_id}` provide scan-friendly history plus full trace retrieval.
- Parser evidence: source manifests include byte counts, parser warnings, parse status, and format metadata such as CSV delimiter/header/row counts.

## Operator layer

The API is local-first and loopback-oriented by default. If `MUCARO_AI_OPERATOR_TOKEN` is configured, mutating routes require either `Authorization: Bearer <token>` or `X-Mucaro-Token: <token>`.

Read-only inspection routes expose health, config, sources, chunks, conversations, runs, workspace profiles, and exports. Mutating routes include planning/approval writes, upload/path ingestion, directory grant changes, source deletion/reindexing, chat creation/asking, and workspace profile saves.

## Packaging layer

The desktop package flow is validated by `scripts/package_desktop.py --self-test`. It verifies launcher, bootstrap, frontend lockfile, and package metadata inputs without writing generated artifacts during certification.

## Conversation layer

Conversations are local SQLite-backed chat sessions. The `/conversations` API creates and lists sessions, while `/conversations/{conversation_id}/ask` routes through the same cited RAG service as `/ask`, passes recent turns as bounded conversation context, and appends the user turn plus assistant answer to the selected session.

Answer traces remain separately stored under `runs` and `data/exports/`, so chat history does not replace citation auditability. Prior turns help interpret follow-up questions but are not treated as source evidence; chunk IDs and source IDs still come from retrieval.

## Retrieval layer

V1 uses deterministic local hybrid retrieval:

- `lexical_v2`: token overlap, cosine normalization, query coverage, stable tie order.
- `vector_tfidf_v1`: local TF-IDF vectors with cosine similarity and no external service requirement.
- `hybrid_tfidf_v1`: the default answer path, combining lexical and vector scores behind the stable retrieval result contract.
- `persistent_tfidf_v1`: opt-in persisted local TF-IDF index stored under `data/indexes/tfidf_index.json`, rebuilt during ingestion and rejected when stale.
- `scripts/evaluate_retrieval.py`: committed retrieval fixture gate that reports recall@k, top-1 hit rate, and no-context accuracy for lexical/vector/hybrid/persistent strategies.

Persistent embedding stores such as Chroma can still be added behind this interface later without changing the answer trace schema.

## Model layer

Mock mode is the deterministic default. Live Anthropic calls require explicit `model_provider="anthropic"`, `MUCARO_AI_LIVE_MODE=1`, `ANTHROPIC_API_KEY`, and the optional `.[anthropic]` dependency group unless the operator explicitly permits mock fallback.
