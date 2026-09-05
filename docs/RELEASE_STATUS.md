# Release Status

Current package version: `0.1.23`.

Current package includes CLI hardening, local hybrid retrieval, desktop launcher
smoke certification, hosted CI release gates, fresh-clone validation, API upload
hardening, parser evidence, retrieval evaluation gates, explicit live-model
routing, frontend evidence display, and an explicit persistent local TF-IDF
retrieval strategy backed by `data/indexes/tfidf_index.json`. Post-baseline
operator upgrades add source deletion/reindexing, retrieval strategy selection
in the UI, expanded retrieval eval coverage with fixture validation, optional
local operator auth, workspace profiles, structured run history, conversation
rename/delete/search, desktop package self-testing, CLI `--version`, early `ask --top-k`
validation, CLI source/index management, CLI conversation management, CLI
run-history inspection, opt-in local BM25 retrieval, CLI readiness diagnostics,
executable product-gap auditing, an opt-in local semantic embedding store,
approval-gated local tool runtime, metadata-only image/audio/video ingestion,
explicit local memory, CLI-exposed retrieval evaluation, and CLI-exposed
packaging readiness with a governed source-artifact policy.
The production-security baseline added profile checks, fail-closed startup
validation, and public-bind safeguards. The current certification target adds
CLI certification-readiness status so operators can inspect product audit,
doctor, JSON validation, Git metadata, worktree cleanliness, tag-at-HEAD, and
upstream state before running heavier release or fresh-clone gates. It also
repairs fresh-clone certification for exact commit SHAs.
The current development candidate adds optional read-only GitHub governance
checks to `mucaro certify status --include-github`, covering branch protection,
latest hosted CI on the current branch for a named workflow, and remote tag
binding without mutating GitHub repository settings. The CI workflow also
supports explicit manual `workflow_dispatch` runs so a release operator can
request a current-head hosted gate when a push-triggered run fails before logs
or jobs are available.

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
- `98 passed` from pytest for the certified `v0.1.19-multimodal-metadata` candidate
- Product audit after multimodal metadata ingestion reports `13 PASS`, `2 OPEN`,
  `0 BLOCKED`, and `86.7%` readiness.
- Local release certification for `v0.1.19-multimodal-metadata` reports
  `CERTIFY_RELEASE_OK`.
- `102 passed` from pytest for the certified `v0.1.20-explicit-memory` candidate
- Product audit after explicit memory controls reports `14 PASS`, `1 OPEN`,
  `0 BLOCKED`, and `93.3%` readiness.
- Local release certification for `v0.1.20-explicit-memory` reports
  `CERTIFY_RELEASE_OK`.
- `109 passed` from pytest for the current `v0.1.21-production-security` candidate
- Product audit after production-security profile checks reports `15 PASS`,
  `0 OPEN`, `0 BLOCKED`, and `100.0%` readiness.
- Local release certification for `v0.1.21-production-security` reports
  `CERTIFY_RELEASE_OK`.
- `112 passed` from pytest for the current
  `v0.1.22-cli-certification-status` target
- Local `mucaro certify status` reports product audit, JSON validation, Git
  repository, clean worktree, and upstream checks passing on commit
  `37c4fc5fde0e0fcfa63edd7a3100dd9bb82a3846`; expected warnings remain for
  local-mode auth and no tag before the release tag is created.
- Local release certification for `v0.1.22-cli-certification-status` reports
  `CERTIFY_RELEASE_OK`.
- Hosted CI run `33642841335` passed on commit
  `37c4fc5fde0e0fcfa63edd7a3100dd9bb82a3846`.
- Exact-SHA fresh-clone certification reports `FRESH_CLONE_CERT_OK` for commit
  `37c4fc5fde0e0fcfa63edd7a3100dd9bb82a3846`.
- `v0.1.23` is the current development candidate for optional GitHub governance
  checks in the CLI certification-readiness receipt.
- Local release certification for `v0.1.23` reports `CERTIFY_RELEASE_OK` on
  commit `f48828b8dd5648554b65dba1ca86a07db099f7d3`, including `122 passed`,
  retrieval eval, JSON validation, desktop package self-test, frontend audit,
  and frontend build.
- Exact-SHA fresh-clone certification reports `FRESH_CLONE_CERT_OK` for commit
  `f48828b8dd5648554b65dba1ca86a07db099f7d3`, including clean clone install,
  CLI help, launcher self-test, and desktop smoke test.
- `mucaro certify status --include-github` now fails closed when the latest
  hosted CI run for the current HEAD completes unsuccessfully. A GitHub-hosted
  `CI` run for commit
  `f48828b8dd5648554b65dba1ca86a07db099f7d3` reports `startup_failure`, so the
  GitHub-governed baseline remains open and untagged.
- `mucaro eval retrieval` exposes the retrieval-quality fixture through the
  supported CLI boundary while `scripts/evaluate_retrieval.py` remains a
  compatibility wrapper for release automation. The fixture gate validates
  benchmark shape, unique chunk IDs, expected chunk bindings, and positive plus
  no-context coverage before scoring strategies.
- `mucaro package status` exposes package metadata, version consistency,
  launcher entrypoint, desktop package manifest, optional generated outputs, and
  current distribution blockers through the supported CLI boundary while
  `scripts/package_desktop.py` remains the desktop-package manifest entrypoint.
- `mucaro package build` creates the current governed release artifact: a
  tracked-source ZIP plus JSON manifest under `data/exports/packages`, with
  exact Git `HEAD`, archive SHA-256, member count, and per-member SHA-256
  receipts.
- `mucaro package status` currently reports `PACKAGE_WARN`: required package
  inputs, Python/frontend version consistency, CLI entrypoint, launcher
  entrypoint, desktop package manifest, generated outputs, release artifact
  policy, and distribution policy pass; hosted GitHub Actions startup remains
  open.
- `mucaro audit product` now uses the governed-release objective. The current
  scope reports `16 PASS`, `1 OPEN`, `0 BLOCKED`, and `94.1%` readiness:
  assistant capabilities and source-artifact packaging pass, while
  `github_release_governance` remains open because hosted CI startup, branch
  protection, and governed tag binding are not yet proven.
- Local release certification for audit commit
  `2848d6f54880a716d5fa61467e5d8a6b5bb5f301` reports
  `CERTIFY_RELEASE_OK`, including `133 passed`, retrieval eval, JSON
  validation, desktop package self-test, source artifact build, npm audit, and
  frontend build.
- Exact-SHA fresh-clone certification reports `FRESH_CLONE_CERT_OK` for commit
  `2848d6f54880a716d5fa61467e5d8a6b5bb5f301`, including clean clone install,
  CLI help, launcher self-test, and desktop smoke test.
- Hosted `CI` run `33980714464` and `CI Startup Probe` run
  `33980714466` both report `startup_failure` with `0` jobs for commit
  `2848d6f54880a716d5fa61467e5d8a6b5bb5f301`; the governed release remains
  untagged.
- A manual-only `CI Startup Probe` workflow was added to isolate hosted runner
  startup from the Mucaro build/test stack. Run `33972500645` for commit
  `b70c5a859a879e651304091b57505b74c49f6953` also reports
  `startup_failure` with `0` jobs and no logs, so the active hosted blocker is
  at the GitHub Actions startup/account/repository-runner layer, not in the
  Python, Node, checkout, or release-certification steps.
- Retrieval eval gate: `RETRIEVAL_EVAL_OK` with `RETRIEVAL_FIXTURE_OK` for
  lexical, vector, BM25, hybrid, persistent, and semantic strategies across 16
  chunks, 19 queries, 15 positive queries, and 4 no-context queries.
- Desktop package self-test: `DESKTOP_PACKAGE_READY`
- `INGEST_FOLDER_OK`
- `CITATION_OK`
- `npm audit` reported 0 vulnerabilities after Vite/package-lock repair
- `npm run build` succeeded
- `RELEASE_CHECK_OK`
- `CERTIFY_RELEASE_OK` after dependency install, source-boundary scans, tests, retrieval evaluation, JSON validation, desktop package self-test, source artifact build, frontend audit, and frontend build
- `FRESH_CLONE_CERT_OK` after temporary clone install, release check, CLI help, launcher self-test, and desktop smoke test
- GitHub issues #1, #2, and #3 closed with evidence comments
- GitHub Actions CI passed on the retrieval and CLI hardening commits
- `v0.1.3-local-hardening` is certified on commit `101e457743a7a0e1b830cbd7e066b6b817af9184`
- `v0.1.4-local-persistent-index` is certified on commit `a1bd219b25bc239f88f30c53902ffd5b4c1bd9a8`
- `v0.1.9-all-vectors-certified` is certified on commit `b5deea7170eb1c94b5238e9a923827e6dc913213`
- `v0.1.18-tool-runtime` is certified for approval-gated tool runtime, product-audit advancement, and fresh-clone launcher certification
- `v0.1.19-multimodal-metadata` is certified for scoped
  multimodal metadata ingestion and fresh-clone launcher certification
- `v0.1.20-explicit-memory` is certified for opt-in local
  memory controls and answer-context wiring
- `v0.1.21-production-security` is certified for production-security profile
  checks and full product-audit closure
- `v0.1.22-cli-certification-status` is the current certification target for
  CLI certification-readiness status and exact-SHA fresh-clone certification
- `v0.1.23` is the current development candidate for read-only GitHub
  governance visibility in `mucaro certify status --include-github`

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
- Multi-tenant SaaS isolation remains out of scope; current production security
  covers a single-operator local-first deployment profile.
- GitHub-hosted `CI` startup is currently unresolved for the `v0.1.23`
  governance candidate. Local release certification and exact-SHA fresh-clone
  certification pass, but the GitHub-governed baseline must not be tagged until
  the hosted `CI` gate succeeds for the current HEAD or the release scope is
  explicitly narrowed.
- The `CI Startup Probe` workflow is a diagnostic gate only. Remove it after
  GitHub-hosted runner startup is repaired, or keep it manual-only if continued
  startup isolation is useful for release operations. See
  `docs/GITHUB_ACTIONS_STARTUP_DIAGNOSTIC.md` for the current evidence and
  operator checks.
- `mucaro certify github-actions` provides a read-only diagnostic receipt for
  the hosted startup blocker, including Actions permissions, workflow
  registration, latest `CI` and probe runs, job counts, and billing endpoint
  access status.
