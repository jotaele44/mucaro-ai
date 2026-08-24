# Release Status

Current package version: `0.1.3`.

Current package includes CLI hardening, local hybrid retrieval, desktop launcher
smoke certification, hosted CI release gates, fresh-clone validation, API upload
hardening, parser evidence, retrieval evaluation gates, explicit live-model
routing, and frontend evidence display.

Observed success markers:

- `PREFLIGHT_OK`
- `REFERENCE_EXTRACTION_OK`
- `JSON_OK`
- `56 passed` from pytest for the `v0.1.3-local-hardening` candidate
- Retrieval eval gate: `RETRIEVAL_EVAL_OK`
- `INGEST_FOLDER_OK`
- `CITATION_OK`
- `npm audit` reported 0 vulnerabilities after Vite/package-lock repair
- `npm run build` succeeded
- `RELEASE_CHECK_OK`
- `CERTIFY_RELEASE_OK` after dependency install, source-boundary scans, tests, JSON validation, frontend audit, and frontend build
- `FRESH_CLONE_CERT_OK` after temporary clone install, release check, CLI help, launcher self-test, and desktop smoke test
- GitHub issues #1, #2, and #3 closed with evidence comments
- GitHub Actions CI passed on the retrieval and CLI hardening commits
- `v0.1.3-local-hardening` is the next candidate tag after the high-ROI hardening batch passes CI and fresh-clone validation

Local expected marker after first setup:

- `BOOTSTRAP_OK` from `scripts/bootstrap_macos.sh` or `scripts/bootstrap_windows.ps1`

Known environment-dependent item:

- Reference repo cloning still depends on local internet/DNS access. Use `bash scripts/clone_references.sh`.
- Persistent Chroma/vector-database integration remains queued for larger-corpus
  Phase 2 work; the certified V1 retrieval layer is local TF-IDF hybrid retrieval.
