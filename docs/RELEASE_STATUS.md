# Release Status

Current package version: `0.1.2`.

Current package includes CLI hardening, local hybrid retrieval, desktop launcher
smoke certification, hosted CI release gates, and fresh-clone validation.

Observed success markers:

- `PREFLIGHT_OK`
- `REFERENCE_EXTRACTION_OK`
- `JSON_OK`
- `32 passed` from pytest
- `INGEST_FOLDER_OK`
- `CITATION_OK`
- `npm audit` reported 0 vulnerabilities after Vite/package-lock repair
- `npm run build` succeeded
- `RELEASE_CHECK_OK`
- `CERTIFY_RELEASE_OK` after dependency install, source-boundary scans, tests, JSON validation, frontend audit, and frontend build
- `FRESH_CLONE_CERT_OK` after temporary clone install, release check, CLI help, launcher self-test, and desktop smoke test
- GitHub issues #1, #2, and #3 closed with evidence comments
- GitHub Actions CI passed on the retrieval and CLI hardening commits

Local expected marker after first setup:

- `BOOTSTRAP_OK` from `scripts/bootstrap_macos.sh` or `scripts/bootstrap_windows.ps1`

Known environment-dependent item:

- Reference repo cloning still depends on local internet/DNS access. Use `bash scripts/clone_references.sh`.
- Persistent Chroma/vector-database integration remains queued for larger-corpus
  Phase 2 work; the certified V1 retrieval layer is local TF-IDF hybrid retrieval.
