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

## Retrieval layer

V1 uses deterministic local hybrid retrieval:

- `lexical_v2`: token overlap, cosine normalization, query coverage, stable tie order.
- `vector_tfidf_v1`: local TF-IDF vectors with cosine similarity and no external service requirement.
- `hybrid_tfidf_v1`: the default answer path, combining lexical and vector scores behind the stable retrieval result contract.

Persistent embedding stores such as Chroma can still be added behind this interface later without changing the answer trace schema.
