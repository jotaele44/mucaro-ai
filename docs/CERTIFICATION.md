# Mucaro AI Certification

## Baseline Anchor

Date: 2026-08-20

The original local-prototype baseline remains frozen at:

- Tag: `v0.1.0-local-prototype`
- Commit: `fbc7f2cd785f9c55a8cf7ef9ab58837d55ba1529`
- Prototype archive SHA256: `5718f66d8393a9661331ccae245049236022ff3de1c1e674d83d5ca2dc3663f7`
- Interim successor tag: `v0.1.1-local-certified` at
  `2fe0951b5bfc6243ae907f281377265d89021690`

## Successor Certification

Date: 2026-08-24

Certified target:

- Tag: `v0.1.2-local-certified`
- Tag target: final certification commit containing this document
- Implementation evidence through commit: `80c1fb8c5118b85871daf5194ce798d647a2ceaa`
- Python package version: `0.1.2`
- Frontend package version: `0.1.2`
- Final v0.1.2 commit: `3902f5d4adcdcc19610e06309b6f1ea73b0beb16`
- Final v0.1.2 CI run: `32753426403`
- Final v0.1.2 tag-based fresh clone: `FRESH_CLONE_CERT_OK`

## Post-Certification Hardening

Date: 2026-08-24

Certified target:

- Tag: `v0.1.3-local-hardening`
- Package version: `0.1.3`
- Commit: `101e457743a7a0e1b830cbd7e066b6b817af9184`
- CI run: `32759653416`
- Main fresh clone: `FRESH_CLONE_CERT_OK`
- Tag fresh clone: `FRESH_CLONE_CERT_OK`
- Scope: API upload/runtime hardening, FastAPI integration coverage, parser evidence, retrieval eval gate, explicit live model routing, and improved frontend operator evidence display.
- Local evidence: `56 passed`, `RETRIEVAL_EVAL_OK`, `JSON_OK checked=7`, `npm audit` 0 vulnerabilities, frontend build passed, and `CERTIFY_RELEASE_OK`.

## Persistent Index Certification

Date: 2026-08-24

Certified target:

- Tag: `v0.1.4-local-persistent-index`
- Package version: `0.1.4`
- Commit: `a1bd219b25bc239f88f30c53902ffd5b4c1bd9a8`
- CI run: `32760785579`
- Scope: explicit persistent local TF-IDF strategy, ingestion-time index rebuild, stale-index rejection, CLI/API strategy selection, and retrieval eval coverage for persistent retrieval.
- Local evidence: `61 passed`, `RETRIEVAL_EVAL_OK`, `JSON_OK checked=7`, `npm audit` 0 vulnerabilities, frontend build passed, and `CERTIFY_RELEASE_OK`.
- Fresh clone evidence: `FRESH_CLONE_CERT_OK` from `v0.1.4-local-persistent-index`.

## A/B/C Operator, Chat, CLI Boundary, Source Index, History, BM25, Doctor, And Product Audit Candidate

Date: 2026-08-25

Candidate target:

- Candidate tag: `v0.1.21-production-security`
- Candidate package version: `0.1.21`
- Baseline preserved: `v0.1.4-local-persistent-index` at `a1bd219b25bc239f88f30c53902ffd5b4c1bd9a8`; interim combined tag `v0.1.9-all-vectors-certified` at `b5deea7170eb1c94b5238e9a923827e6dc913213`
- Scope: source library delete/reindex controls, retrieval strategy UI, no-context recovery guidance, expanded retrieval eval corpus, optional local operator token boundary, workspace profiles, structured run history, conversation follow-up context, conversation rename/delete/search, directory access UI, desktop package self-test, CLI `--version`, early `ask --top-k` validation, CLI source listing, CLI source deletion, CLI reindexing, CLI index status, CLI conversation create/list/show/rename/delete/ask, CLI run list/summary/show, opt-in `bm25_v1` retrieval, CLI readiness diagnostics, executable product-gap auditing, opt-in `local_semantic_hash_store_v1` retrieval, approval-gated local tool runtime, metadata-only image/audio/video ingestion, explicit local memory controls, and production-security profile checks.
- Candidate local evidence: `109 passed`, `RETRIEVAL_EVAL_OK`, `JSON_OK checked=10`,
  `DESKTOP_PACKAGE_READY`, `npm audit` 0 vulnerabilities, frontend build passed,
  `PRODUCT_AUDIT_OK` at `100.0%`, clean `worktree_clean`, and
  `CERTIFY_RELEASE_OK`.
- Required external receipt: hosted CI and fresh-clone certification must pass on the final pushed commit before tagging. Current release candidates must also build `PACKAGE_ARTIFACT_READY` from the same clean source state before a governed tag.

## Scope

This certification covers the committed `mucaro-ai` source tree intended for the
`v0.1.2-local-certified` successor tag.

Included:

- Backend source, schemas, tests, scripts, docs, sample inputs, frontend source,
  package lockfile, and CLI packaging metadata.
- Packaging surface: read-only package readiness status plus governed tracked-source ZIP and JSON manifest generation under `data/exports/packages`.
- CLI surface: `mucaro ask`, `mucaro ingest`, `mucaro plan`, `mucaro validate`,
  and `mucaro serve`.
- Retrieval surface: `lexical_v2`, `vector_tfidf_v1`, `bm25_v1`,
  `persistent_tfidf_v1`, `local_semantic_hash_store_v1`, and default
  `hybrid_tfidf_v1` answer retrieval.
- Tool surface: `source_library.status`, `product.audit`, and
  `workspace_profiles.list`, and `memory.list` behind pending-request, explicit-approval,
  deny-by-default execution, and local audit events.
- Multimodal metadata surface: PNG, JPEG, GIF, WEBP, WAV, MP3, M4A, MP4, MOV,
  and WEBM ingestion through parser, API upload, CLI folder ingestion, source
  manifests, and local indexes. This is metadata-only and excludes semantic
  image/audio/video understanding.
- Memory surface: opt-in local memory save/list/enable/disable/delete/export
  through API, browser, and CLI; enabled memory is passed as answer context and
  recorded by memory ID without becoming cited source evidence.
- Security surface: production profile startup checks, high-entropy operator
  token requirement, explicit CORS requirement, `/security` inspection, CLI
  security status, and local-mode public-bind guard.
- Desktop launcher smoke path, editable install path, local release gates, hosted
  CI, and fresh-clone certification.

Excluded by design:

- `.env`, virtual environments, node modules, frontend build output, processed
  manifests, SQLite databases, answer exports, local indexes, pycache, Finder
  metadata, agent metadata, and cloned reference repositories.

## Certification Gates

| Gate | Result |
|---|---|
| GitHub repo visibility | PASS: private |
| Local/remote branch alignment | PASS: `main` pushed to `origin/main`; exact tag target recorded by `v0.1.2-local-certified` |
| GitHub issues | PASS: issues #1, #2, and #3 closed with evidence comments |
| Prior prototype-name residue | PASS: no committed hits for prior prototype identifiers |
| Secret-pattern scan | PASS: no committed secret material found; `.env.example` model name is an allowed non-secret example |
| Large committed files | PASS: no committed file over 1 MB |
| Generated artifact scan | PASS: no committed env, venv, node modules, dist, SQLite, exports, pycache, Finder metadata, or generated ingestion artifacts |
| Backend tests | PASS: 32 tests passed |
| JSON schema validation | PASS: `JSON_OK checked=7` |
| Source package artifact | PASS: `PACKAGE_ARTIFACT_READY` |
| Frontend security audit | PASS: `npm audit --audit-level=moderate` found 0 vulnerabilities |
| Frontend build | PASS: `npm run build` succeeded |
| Release certification | PASS: `CERTIFY_RELEASE_OK` |
| Hosted CI | PASS: implementation run `32751159589` passed on commit `80c1fb8c5118b85871daf5194ce798d647a2ceaa`; final tag target must pass CI before tagging |
| Fresh clone certification | PASS: `FRESH_CLONE_CERT_OK` from final `v0.1.2-local-certified` tag |
| Desktop launcher smoke | PASS: covered by fresh-clone `scripts/launch_app.py --smoke-test --no-browser` |

## Notes

- This is a bounded local-first certification, not a production-readiness claim.
- Persistent vector databases such as Chroma remain a queued scalability item.
  The certified retrieval upgrade is deterministic local TF-IDF vector, hybrid,
  and persistent local TF-IDF retrieval behind the existing answer trace
  contract. See `docs/RETRIEVAL_UPGRADE_PLAN.md`.
- The final pre-tag CI update moved GitHub Actions to current major versions:
  `actions/checkout@v7`, `actions/setup-python@v7`, and `actions/setup-node@v7`.
- Reference repo cloning still depends on local internet/DNS access and remains
  outside the committed source certification.

## Status

CERTIFIED through `v0.1.4-local-persistent-index` after local release, hosted CI,
retrieval eval, and fresh-clone gates passed on the final tag target.
