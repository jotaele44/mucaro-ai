# Release Status

Current package version: `0.1.15`.

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
run-history inspection, opt-in local BM25 retrieval, and CLI readiness
diagnostics.

Observed success markers:

- `PREFLIGHT_OK`
- `REFERENCE_EXTRACTION_OK`
- `JSON_OK`
- `56 passed` from pytest for the certified `v0.1.3-local-hardening` tag
- `61 passed` from pytest for the certified `v0.1.4-local-persistent-index` tag
- `76 passed` from pytest for the current `v0.1.12-cli-source-index` candidate
- `78 passed` from pytest for the current `v0.1.13-cli-assistant-history` candidate
- `79 passed` from pytest for the current `v0.1.14-bm25-retrieval` candidate
- Retrieval eval gate: `RETRIEVAL_EVAL_OK` for lexical, vector, BM25, hybrid, and persistent strategies across the expanded corpus
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
- `v0.1.15-cli-doctor` is the current candidate target for the combined operator/admin/context baseline, chat management controls, CLI boundary hardening, CLI source/index management, CLI conversation/run history, BM25 retrieval, CLI diagnostics, and fresh-clone launcher certification

Local expected marker after first setup:

- `BOOTSTRAP_OK` from `scripts/bootstrap_macos.sh` or `scripts/bootstrap_windows.ps1`

Known environment-dependent item:

- Reference repo cloning still depends on local internet/DNS access. Use `bash scripts/clone_references.sh`.
- Chroma integration remains queued for larger-corpus Phase 2 work; the V1.4
  retrieval layer includes local TF-IDF hybrid retrieval, opt-in BM25 retrieval,
  plus an explicit persistent local TF-IDF index.
