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

V1 uses deterministic lexical scoring. Chroma can be added behind the retrieval interface later without changing answer trace schema.
