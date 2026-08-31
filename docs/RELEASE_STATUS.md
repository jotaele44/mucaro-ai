# Release Status

Current package version: `0.1.19`.

Current package includes CLI hardening, local hybrid retrieval, desktop launcher
smoke certification, hosted CI release gates, fresh-clone validation, API upload
hardening, parser evidence, retrieval evaluation gates, explicit live-model
routing, frontend evidence display, and an explicit persistent local TF-IDF
retrieval strategy backed by `data/indexes/tfidf_index.json`. Post-baseline
operator upgrades add source deletion/reindexing, retrieval strategy selection
in the UI, expanded retrieval eval coverage, optional local operator auth,
workspace profiles, structured run history, conversation rename/delete/search,
desktop package self-testing, CLI `--version`, early `ask --top-k`
validation, CLI source/index management, CLI conversation management, CLI
run-history inspection, opt-in local BM25 retrieval, CLI readiness diagnostics,
executable product-gap auditing, an opt-in local semantic embedding store, and
an approval-gated local tool runtime.
The next candidate adds metadata-only image/audio/video ingestion through the
parser, API, CLI folder ingestion, source manifests, and local retrieval indexes.

Observed success markers:

- `PREFLIGHT_OK`
- `REFERENCE_EXTRACTION_OK`
- `JSON_OK`
- `56 passed` from pytest for the certified `v0.1.3-local-hardening` tag
- `61 passed` from pytest for the certified `v0.1.4-local-persistent-index` tag
- `76 passed` from pytest for the current `v0.1.12-cli-source-index` candidate
- `78 passed` from pytest for the current `v0.1.13-cli-assistant-history` candidate
- `79 passed` from pytest for the current `v0.1.14-bm25-retrieval` candidate
- `81 passed` from pytest for the current `v0.1.15-cli-doctor` candidate
- `82 passed` from pytest for the current `v0.1.16-product-audit` candidate
- `86 passed` from pytest for the current `v0.1.17-semantic-store` candidate
- Tool runtime candidate adds allowlisted tools, explicit approval, deny-by-default execution, and audit events.
- `98 passed` from pytest for the current `v0.1.19-multimodal-metadata` candidate
- Product audit after multimodal metadata ingestion reports `13 PASS`, `2 OPEN`,
  `0 BLOCKED`, and `86.7%` readiness.
- Local release certification for `v0.1.19-multimodal-metadata` reports
  `CERTIFY_RELEASE_OK`.
- Retrieval eval gate: `RETRIEVAL_EVAL_OK` for lexical, vector, BM25, hybrid, persistent, and semantic strategies across the expanded corpus
- Desktop package self-test: `DESKTOP_PACKAGE_READY`
- `INGEST_FOLDER_OK`
- `CITATION_OK`
- `npm audit` reported 0 vulnerabilities after Vite/package-lock repair
- `npm run build` succeeded
- `RELEASE_CHECK_OK`
- `CERTIFY_RELEASE_OK` after dependency install, source-boundary scans, tests, JSON validation, frontend audit, and frontend build
- `FRESH_CLONE_CERT_OK` after temporary clone install, release check, CLI help, launcher self-test, and desktop smoke test
- GitHub issues #1, #2, and #3 closed with evidence comments
- GitHub Actions CI passed on the retrieval and CLI hardening commits
- `v0.1.3-local-hardening` is certified on commit `101e457743a7a0e1b830cbd7e066b6b817af9184`
- `v0.1.4-local-persistent-index` is certified on commit `a1bd219b25bc239f88f30c53902ffd5b4c1bd9a8`
- `v0.1.9-all-vectors-certified` is certified on commit `b5deea7170eb1c94b5238e9a923827e6dc913213`
- `v0.1.18-tool-runtime` is certified for approval-gated tool runtime, product-audit advancement, and fresh-clone launcher certification
- `v0.1.19-multimodal-metadata` is the current candidate target for scoped
  multimodal metadata ingestion and fresh-clone launcher certification

Local expected marker after first setup:

- `BOOTSTRAP_OK` from `scripts/bootstrap_macos.sh` or `scripts/bootstrap_windows.ps1`

Known environment-dependent item:

- Reference repo cloning still depends on local internet/DNS access. Use `bash scripts/clone_references.sh`.
- Chroma or model-backed embeddings remain queued for a future quality upgrade;
  the current retrieval layer includes local TF-IDF hybrid retrieval, opt-in
  BM25 retrieval, explicit persistent local TF-IDF, and an opt-in local
  semantic hash-embedding store.
- Model-backed multimodal understanding remains queued; current media support
  is metadata-only and source-lineage oriented.
