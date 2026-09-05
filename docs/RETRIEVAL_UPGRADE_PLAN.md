# Retrieval Upgrade Plan

## Goal

Upgrade retrieval without breaking Mucaro AI's source-lineage and answer-trace
contracts.

## Current Certified Surface

| Strategy | Status | Role |
|---|---|---|
| `lexical_v2` | Certified | Deterministic token overlap baseline |
| `vector_tfidf_v1` | Certified | Local TF-IDF vector scoring |
| `bm25_v1` | Candidate | Local BM25 ranking without external dependencies |
| `hybrid_tfidf_v1` | Certified | Default answer retrieval strategy |
| `persistent_tfidf_v1` | Certified | Local persisted TF-IDF index rebuilt during ingestion |
| `local_semantic_hash_store_v2` | Candidate | Local persisted hash-embedding store rebuilt during ingestion, constrained by normalized matched-term evidence and rare-term weighting |

The current persistent index is stored under `data/indexes/tfidf_index.json`.
The local semantic store is stored under `data/indexes/semantic_store.json`.
Both stores fail closed when missing or stale. The local semantic hash store also
fails closed when hash similarity exists without normalized matched query terms.
The v2 semantic store preserves the local deterministic embedding boundary while
ranking with a small rare-term matched-evidence component so domain-specific
source-lineage terms are not hidden by broad hash-vector similarity.

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

## Current Upgrade Boundary

The first semantic-store cycle adds `strategy="semantic"` behind the existing
`retrieve()` boundary without changing the default `hybrid` answer path. The
store uses deterministic local hash embeddings, preserves the answer-trace
schema, rebuilds during ingestion/reindex/delete, and fails closed when the
store is missing or stale. Because the current store is local and deterministic,
not a hosted language embedding model, it requires matched terms before returning
cited context.

The committed retrieval fixture now has its own validation gate: chunk IDs must
be unique, expected chunk bindings must point at existing chunks, source
coverage must span a minimum number of source IDs, positive and no-context cases
must both meet minimum floors, multi-binding cases must be present, query
categories must cover multiple product surfaces, and benchmark counts are
reported beside retrieval scores.

Release certification also runs `backend/tests/fixtures/retrieval_eval_large.json`
as a larger deterministic corpus. That larger fixture exercises the same local
retrieval strategies across more source IDs, more query categories, multi-binding
queries, and additional no-context cases. The evaluation gate requires
no-context accuracy across every strategy, not only the semantic store.

Release certification also runs
`backend/tests/fixtures/retrieval_eval_mixed_formats.json` as a mixed-format
corpus for the current ingestion surface. That fixture covers TXT, Markdown,
HTML, DOCX, EML, MSG text probing, CSV, JSON, PDF, metadata-only media,
traceability, and multi-format source questions so parser upgrades have
retrieval evidence beyond unit-level extraction checks.

`mucaro certify retrieval-quality` promotes those fixture checks into an
exact-head release receipt. The current `v0.1.23-local-release` profile runs the
default fixture with strict per-category expected-binding recall, the large
fixture with a calibrated multi-binding category threshold, and the mixed-format
fixture with strict per-category binding recall, preserving the full eval
reports in a `retrieval_quality` receipt for release-state inspection.

Future model-backed embeddings or Chroma-style stores should use this boundary
instead of changing the answer service contract. They should not replace the
default `hybrid` strategy until eval evidence shows a material improvement.

## Required Gates Before Enabling Chroma

| Gate | Required Result |
|---|---|
| Fixture eval | `RETRIEVAL_EVAL_OK` with the candidate strategy included |
| Fixture contract | `RETRIEVAL_FIXTURE_OK` with no dangling expected IDs, source diversity, multi-binding cases, category coverage, and positive/no-context minimums |
| Large-corpus eval | `RETRIEVAL_EVAL_OK` for `backend/tests/fixtures/retrieval_eval_large.json` during release certification |
| Mixed-format eval | `RETRIEVAL_EVAL_OK` for `backend/tests/fixtures/retrieval_eval_mixed_formats.json` during release certification |
| Retrieval-quality receipt | `RETRIEVAL_QUALITY_CERT_OK` from `mucaro certify retrieval-quality` on the exact release `HEAD` |
| No-context accuracy | Every evaluated strategy meets the configured `--min-no-context-accuracy` threshold |
| Expected-binding recall | Every evaluated strategy meets the configured `--min-expected-binding-recall` threshold |
| Category recall | Optional `--min-category-recall` can fail any positive-query category below the configured threshold |
| Category expected-binding recall | Optional `--min-category-binding-recall` can fail any positive-query category that misses required expected chunks |
| Context precision | Hybrid and semantic retrieval meet their configured precision thresholds so noisy top-k context is visible |
| Multi-binding full hit | Every evaluated strategy meets the configured `--min-multi-binding-full-hit` threshold for multi-source questions |
| Determinism check | Stable top-k order for ties or explicit tie-break metadata |
| Empty corpus | Explicit error status, no exception |
| Missing index/store | Explicit repair warning |
| Stale index/store | Fingerprint mismatch rejected |
| Answer trace compatibility | No schema change required |
| Fresh clone | Optional dependency does not break default install |
| Dependency audit | No moderate+ npm or Python known-vulnerability gate failures |

## Implementation Order

1. Add an optional dependency group for Chroma or another model embedding store
   when the privacy and eval gates are ready.
2. Implement the provider behind the existing build, load, fingerprint, and
   query operations without changing `answer_question()`.
3. Add the provider strategy only after tests cover missing and stale store
   states.
4. Extend `scripts/evaluate_retrieval.py` to include the new strategy when any
   optional dependency is installed, and keep fixture validation mandatory for
   all candidate strategies.
5. Keep `hybrid` as default until larger-corpus evals prove the new strategy is
   better under recall@k, expected-binding recall, context precision,
   full-binding hit rate, multi-binding full-hit rate, top-1 hit rate, and
   no-context accuracy.

## Non-Goals

- Do not require Pinecone, hosted embeddings, or network access for the default
  local workflow.
- Do not change answer-trace schemas in the same patch as a vector-store
  adapter.
- Do not treat nearest-neighbor output as identity proof; retrieved chunks remain
  candidates until citations validate against chunk IDs and source IDs.

## Readiness

Current state: `persistent_tfidf_v1` is certified as the first local persistence
upgrade. `local_semantic_hash_store_v2` is the current semantic-store candidate
for certification with normalized matched-term false-positive protection,
rare-term scoring, and fixture-shape validation. Chroma or model-backed
embeddings remain future quality upgrades behind the same boundary.
