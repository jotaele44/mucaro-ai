# Architecture

```text
User Request
  → Execution Gate
  → Readiness Score
  → Mode Selector
  → Approval Lock
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

- SQLite: metadata and run records.
- JSON files: inspectable manifest, chunks, schemas, exports.
- Local folders: `data/input`, `data/processed`, `data/exports`, `data/indexes`.
- API upload boundary: sanitizes upload filenames, rejects traversal/generated/unsupported names, and enforces `MUCARO_AI_MAX_UPLOAD_BYTES` with a 25 MB default.
- Parser evidence: source manifests include byte counts, parser warnings, parse status, and format metadata such as CSV delimiter/header/row counts.

## Retrieval layer

V1 uses deterministic local hybrid retrieval:

- `lexical_v2`: token overlap, cosine normalization, query coverage, stable tie order.
- `vector_tfidf_v1`: local TF-IDF vectors with cosine similarity and no external service requirement.
- `hybrid_tfidf_v1`: the default answer path, combining lexical and vector scores behind the stable retrieval result contract.
- `scripts/evaluate_retrieval.py`: committed retrieval fixture gate that reports recall@k, top-1 hit rate, and no-context accuracy for lexical/vector/hybrid strategies.

Persistent embedding stores such as Chroma can still be added behind this interface later without changing the answer trace schema.

## Model layer

Mock mode is the deterministic default. Live Anthropic calls require explicit `model_provider="anthropic"`, `MUCARO_AI_LIVE_MODE=1`, `ANTHROPIC_API_KEY`, and the optional `.[anthropic]` dependency group unless the operator explicitly permits mock fallback.
