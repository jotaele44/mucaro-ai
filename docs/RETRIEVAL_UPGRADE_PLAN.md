# Retrieval Upgrade Plan

## Goal

Upgrade retrieval without breaking Mucaro AI's source-lineage and answer-trace
contracts.

## Current Certified Surface

| Strategy | Status | Role |
|---|---|---|
| `lexical_v2` | Certified | Deterministic token overlap baseline |
| `vector_tfidf_v1` | Certified | Local TF-IDF vector scoring |
| `hybrid_tfidf_v1` | Certified | Default answer retrieval strategy |
| `persistent_tfidf_v1` | Certified | Local persisted TF-IDF index rebuilt during ingestion |

The current persistent index is stored under `data/indexes/tfidf_index.json` and
fails closed when missing or stale.

## Invariants

- Every retrieved result must retain `chunk_id`, `source_id`, `text`,
  `chunk_index`, `score`, `retrieval_strategy`, and `matched_terms` where
  available.
- Answer traces must continue to export `retrieved_chunk_ids`,
  `cited_chunk_ids`, `source_ids`, confidence, validation flags, and export
  paths.
- Empty corpus, malformed chunks, duplicate chunk IDs, invalid `top_k`, missing
  indexes, stale indexes, and no-context queries must return explicit status
  codes.
- Ranking must remain deterministic for ties.
- No external vector service may become required for the default local path.

## Next Upgrade Boundary

The next retrieval upgrade should add a pluggable embedding/vector-store adapter
behind the existing `retrieve()` strategy boundary. Chroma remains the first
candidate because it can run locally and fits the existing queued Phase 2 item,
but it should not replace the default `hybrid` strategy until eval evidence
shows a material improvement.

The first development cycle keeps retrieval behavior unchanged and tightens the
ingestion/access boundary first. This preserves the certified
`persistent_tfidf_v1` contract while allowing larger local user-directory
corpora to be ingested through an explicit permission gate for future retrieval
evaluation.

Candidate strategy name:

```text
chroma_local_v1
```

## Required Gates Before Enabling Chroma

| Gate | Required Result |
|---|---|
| Fixture eval | `RETRIEVAL_EVAL_OK` with Chroma included |
| Determinism check | Stable top-k order for ties or explicit tie-break metadata |
| Empty corpus | Explicit error status, no exception |
| Missing index/store | Explicit repair warning |
| Stale index/store | Fingerprint mismatch rejected |
| Answer trace compatibility | No schema change required |
| Fresh clone | Chroma optional dependency does not break default install |
| Dependency audit | No moderate+ npm or Python known-vulnerability gate failures |

## Implementation Order

1. Add an optional dependency group for Chroma or another local vector store.
2. Add an adapter module that exposes build, load, fingerprint, and query
   operations without changing `answer_question()`.
3. Add `strategy="chroma"` to `retrieve()` only after tests cover missing and
   stale store states.
4. Extend `scripts/evaluate_retrieval.py` to include the new strategy when the
   optional dependency is installed.
5. Keep `hybrid` as default until larger-corpus evals prove the new strategy is
   better under recall@k, top-1 hit rate, and no-context accuracy.

## Non-Goals

- Do not require Pinecone, hosted embeddings, or network access for the default
  local workflow.
- Do not change answer-trace schemas in the same patch as a vector-store
  adapter.
- Do not treat nearest-neighbor output as identity proof; retrieved chunks remain
  candidates until citations validate against chunk IDs and source IDs.

## Readiness

Current state: `persistent_tfidf_v1` is certified as the first local persistence
upgrade. Chroma or embedding-backed retrieval is planned but not yet certified.
