# Release Status

Current package version: `0.1.23`.

## Authoritative Exact-Head Status

Do not treat hard-coded commits in this tracked document as current proof.
Editing and committing release documentation changes Git `HEAD`, which makes
previous exact-head receipts historical. Use the CLI to bind the current source
state to receipts and handoff artifacts:

- `mucaro release receipts --current-head --format text` reports `7` receipts,
  `7` valid, `0` invalid when the local release lane is fully refreshed.
- `mucaro package distribution-manifest --verify
  data/exports/distribution_manifests/distribution-manifest-HEADPREFIX.json
  --require-current-head --expected-sha256 SHA256` proves the local
  distribution handoff bytes for the current checkout.
- `mucaro release governance-report --verify
  data/exports/governance_reports/governance-report-HEADPREFIX.json
  --require-current-head --expected-sha256 SHA256` proves the governance
  handoff report for the current checkout.
- `mucaro release support-bundle --verify
  data/exports/support_bundles/support-bundle-HEADPREFIX.json
  --require-current-head --expected-sha256 SHA256` proves the support bundle
  for the current checkout.
- `mucaro audit completion --include-github --format text` is the active-goal
  completion matrix and must remain `OPEN` while hosted governance is not
  proven.

When hosted `CI` and `CI Startup Probe` complete with `startup_failure` before
GitHub creates jobs, the support classification is
`HOSTED_STARTUP_BEFORE_JOBS`. Local certification must not be upgraded to
hosted or production readiness until run admission, billing visibility, and
branch protection are independently proven.

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
approval-gated local tool runtime, local HTML/DOCX/EML document ingestion, bounded text-like EML attachment extraction, Outlook MSG text probing, metadata-only image/audio/video ingestion,
explicit local memory, CLI-exposed retrieval evaluation, and CLI-exposed
packaging readiness with a governed source-artifact policy. The current
retrieval-quality baseline adds expected-binding recall, context precision,
full-binding hit rate, multi-binding full-hit rate, source-format coverage
counts, and explicit distractor top-1 avoidance gates so high recall cannot hide
noisy, partial, or wrong-leading top-k context.
The current cited-answer traceability candidate expands answer traces with
per-citation records that bind citation order to retrieval rank, source ID,
chunk index, retrieval strategy, score, matched terms, source format, and a
bounded text snippet across JSON, Markdown, CSV, persisted runs, and CLI text
output. The follow-on answer-trace quality gate exposes this contract through
`mucaro eval answer-trace`, `mucaro certify answer-trace-quality`, and an
exact-head `answer_trace_quality` release receipt with positive and negative
fixture cases. It now also runs generated local-corpus cases that ingest
Markdown/TXT sources in an isolated temp project, ask through the normal service
path, and verify the produced citation records, source bindings, snippets, and
export paths.
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
- `v0.1.23-retrieval-precision-gates` is certified on commit
  `9685077049b761b6d200d1493cbbb53376537a4e` for retrieval precision and
  expected-binding quality gates across the script and CLI surfaces.
- Local release certification for commit
  `9685077049b761b6d200d1493cbbb53376537a4e` reports
  `CERTIFY_RELEASE_OK`, including `149 passed`, default retrieval eval, large
  retrieval eval, JSON validation, desktop package self-test, package artifact
  build/verify, npm audit, and frontend build.
- Exact-SHA fresh-clone certification reports `FRESH_CLONE_CERT_OK` for commit
  `9685077049b761b6d200d1493cbbb53376537a4e`, including clean clone install,
  CLI help, launcher self-test, and desktop smoke test.
- Python distribution certification reports `PYTHON_DIST_CERT_OK` for commit
  `9685077049b761b6d200d1493cbbb53376537a4e`, including wheel/sdist build,
  wheel install, CLI version/help, and import checks.
- Source artifact certification reports `SOURCE_ARTIFACT_CERT_OK` for manifest
  `data/exports/packages/mucaro-ai-0.1.23-9685077049b7-artifact-manifest.json`.
- Hosted `CI` run `33987538734` and `CI Startup Probe` run `33987538749` both
  report `startup_failure` with `0` jobs for commit
  `9685077049b761b6d200d1493cbbb53376537a4e`; GitHub governance remains open at
  the Actions startup/account/repository-runner layer.
- `mucaro certify github-actions` now reports private branch-protection API
  availability and self-hosted runner inventory as explicit read-only
  diagnostics. The current private repo branch-protection API reports a plan
  gate requiring GitHub Pro or public visibility, while hosted CI startup still
  fails before job creation. The diagnostic also emits a structured
  `startup_blocker` summary for automation and release-state handoff.
- `mucaro release governance-report` combines release-state classification,
  product-audit status, package status, current-head receipt summaries, GitHub
  Actions diagnostics, startup-blocker causes, hosted run evidence, and exact
  commands/actions into one current-head handoff payload. Add `--save` to
  persist the JSON handoff artifact under `data/exports/governance_reports` with
  path, SHA-256, and byte-size metadata in the CLI response. Use
  `mucaro release governance-report --verify PATH` to validate a saved handoff
  artifact, add `--expected-sha256 SHA256` to prove byte identity, and add
  `--require-current-head` when the artifact must bind to the current checkout.
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
  benchmark shape, unique chunk IDs, expected chunk bindings, explicit
  distractor chunk bindings, source-format metadata, and positive plus
  no-context coverage before scoring strategies.
- `mucaro certify retrieval-quality` now turns the default and large retrieval
  fixtures plus the mixed-format retrieval fixture into an exact-head
  `retrieval_quality` release receipt. The mixed-format fixture covers TXT,
  Markdown, HTML, DOCX, EML, MSG text probing, CSV, JSON, PDF, metadata-only media,
  traceability, and multi-format questions with strict per-category binding
  recall plus explicit decoy chunks for distractor top-1 avoidance. The profile
  records per-fixture thresholds, query/category/source-format/distractor counts,
  category and distractor threshold failures, and full eval reports, and
  `mucaro release state` reports `RETRIEVAL_QUALITY_CERTIFIED` from that receipt.
- `mucaro package status` exposes package metadata, version consistency,
  launcher entrypoint, desktop package manifest, optional generated outputs, and
  current distribution blockers through the supported CLI boundary while
  `scripts/package_desktop.py` remains the desktop-package manifest entrypoint.
- `mucaro package build` creates the current governed release artifact: a
  tracked-source ZIP plus JSON manifest under `data/exports/packages`, with
  exact Git `HEAD`, archive SHA-256, member count, and per-member SHA-256
  receipts.
- `mucaro package distribution-manifest --save` creates a local distribution
  handoff manifest after the exact-head local release, fresh-clone,
  source-artifact, Python-distribution, answer-trace-quality, retrieval-quality,
  and operator-workflow receipts exist. The manifest records receipt file
  hashes, source artifact hashes, Python distribution artifact hashes, and keeps
  hosted governance explicitly open.
- `mucaro release dashboard` summarizes current release state, exact-head
  receipts, verified handoff artifacts, remote branch binding, tag binding, and
  blockers in one read-only operator view. With `--include-github`, it also
  reports live GitHub Actions diagnostics without converting hosted failures
  into local certification claims.
- `mucaro certify github-actions` records repository Actions permission,
  workflow token permission, workflow registration, run/job counts, check-suite
  startup failures, branch-protection plan gates, and billing-scope visibility.
  If the billing endpoint requires extra GitHub OAuth scope, the diagnostic
  preserves the required scope in both JSON and text output.
- `mucaro release hosted-governance` is the operator-facing remediation view for
  the hosted lane. It composes the GitHub diagnostic and current support-bundle
  verification into explicit steps for Actions policy, workflow registration,
  run admission, check-suite admission, billing visibility, branch protection,
  and support-bundle readiness.
- `mucaro release incident-packet --save` creates a compact hosted-governance
  escalation artifact under `data/exports/hosted_incident_packets`. It preserves
  the hosted-governance report, completion-audit hosted row, current-head
  startup run IDs, suspected startup causes, open remediation steps, diagnostic
  commands, and support-ready copy lines. Verify saved packets with
  `mucaro release incident-packet --verify PATH --require-current-head`.
- `mucaro release handoff --format text` is the restartable one-command path for
  the current-head handoff artifact set. It saves and verifies the distribution
  manifest, ensures the governance report, saves and verifies the support
  bundle, saves and verifies the next-actions runbook, and prints the exact
  verification commands. With `--refresh-certification`, it reruns current-head
  local release, Python distribution, source artifact, and fresh-clone
  certifiers first, then stops before handoff artifact writes if any certifier
  fails. Repeated `--refresh-step` values select a canonical subset in release
  order and imply certification refresh. `--refresh-timeout-seconds N` applies
  a per-certifier timeout. Every refresh attempt writes a JSON log under
  `data/exports/release_refresh_logs` using the current head prefix and a UTC
  attempt stamp, so failed or timed-out pre-handoff runs remain auditable.
  Refresh reports also preserve per-step elapsed time, total elapsed time, and
  a slowest-step summary for release-runtime triage.
- `mucaro audit completion --include-github` composes product-audit,
  release-state, release-dashboard, package, receipt, and hosted-governance
  evidence into one active-goal matrix. It reports PASS, OPEN, BLOCKED, FAIL,
  and UNVERIFIED rows so local readiness, advanced capability readiness,
  release governance, and production packaging progress cannot be conflated.
  The completion denominator includes the expanded GIS, OCR, web browsing,
  coding/programming, and transparency/objectivity rows from product audit.
  When GitHub Actions fails before jobs, the hosted-governance row carries the
  support classification, current-head startup run IDs, check-suite counts, and
  suspected causes from the GitHub Actions diagnostic.
- `mucaro release support-bundle --save` creates a support-ready JSON artifact
  for hosted governance escalation. It captures the release dashboard, GitHub
  diagnostic status, support classification, startup blocker, blockers,
  reproduction commands, and next actions without claiming the hosted blocker is
  resolved. When saving, it first verifies the matching current-head governance
  report and creates it if needed, keeping the support artifact path
  restartable instead of order-dependent.
- `mucaro audit completion --include-github` reports `COMPLETION_AUDIT_OPEN`
  while any expanded capability row remains open, and keeps the
  hosted-governance row open until GitHub runner admission, billing visibility,
  and branch protection are proven for the current head.
- `mucaro capabilities contracts` reports
  `ADVANCED_CAPABILITY_CONTRACTS_READY` when the GIS, OCR, web browsing,
  coding/programming, and transparency/objectivity proof contracts are
  structurally complete. This is a contract-readiness signal only; product and
  completion audits must remain open until adapters, fixtures, provenance
  ledgers, and enforcement hooks exist.
- `mucaro release hosted-governance` reports `HOSTED_GOVERNANCE_OPEN` with
  support-bundle readiness separated from CI job admission and branch
  protection readiness.
- `mucaro package status` currently reports `PACKAGE_WARN`: required package
  inputs, Python/frontend version consistency, CLI entrypoint, launcher
  entrypoint, desktop package manifest, generated outputs, release artifact
  policy, and distribution policy pass; hosted GitHub Actions startup remains
  open.
- `mucaro audit product` now uses the expanded governed-release objective. The
  current scope reports `16 PASS`, `7 OPEN`, `0 BLOCKED`, and `69.6%`
  readiness: the existing local assistant capabilities and source-artifact
  packaging pass, while the expanded GUI/operator workspace is represented as a
  separate final-phase open requirement. The existing launcher remains covered
  by local smoke evidence, but GUI expansion is not the active development
  vector until backend, CLI, provenance, capability, packaging, and hosted
  governance gates are stronger.
  Advanced GIS now has narrow local GeoJSON inspection, point-in-polygon,
  geometry comparison, limited EPSG:4326/EPSG:3857 reprojection, CSV
  point-to-GeoJSON conversion, and bbox feature-filter receipt candidates plus
  a committed `mucaro eval gis` fixture harness covering dimensionality, join
  multiplication, topology equality, reprojection, CSV
  header/skipped-row/row-accounting evidence, bbox retained/excluded accounting,
  fail-closed invalid bbox extents, and fail-closed invalid coordinates; full
  GIS maturity remains open until robust topology, broader
  CRS/datum/grid-shift/axis-order support, planar overlay checks, geometry
  clipping, broader spatial format adapters, raster/vector processing,
  map/export provenance, and a broader GIS eval corpus are proven.
  State-of-the-art OCR
  remains open, but a narrow local image OCR receipt candidate now preserves
  source-image hashes, Tesseract engine identity, raw/normalized/canonical text,
  confidence states, source-coordinate spans, and no-text states; PDF/page
  rendering, calibrated layout reconstruction, representative document fixtures,
  table/form/handwriting coverage, optional provider policy, and confidence
  thresholds remain open. Governed web browsing
  has candidate `web.fetch` snapshot,
  ledger, operator-supplied multi-page session receipts, robots/cache metadata,
  redirect-target robots evidence, HTML body/structure traces, PDF/non-text
  parser traces, immutable snapshot-only cache policy, explicit HTTP error-page
  citation policy, and web citation fixture evidence, while automated search/discovery,
  rendered-browser capture, autonomous link-following, coding/programming
  expertise, cross-capability transparency/objectivity governance, and hosted
  GitHub governance remain open. Tag binding is verified separately by the
  release-state and GitHub
  governance commands.
- `mucaro audit roadmap` groups the same product-audit rows into development
  stages without changing the denominator. It reports the canonical operator
  surface, permissioned source grounding, controlled assistant behavior,
  advanced capability expansion, release packaging certification, and hosted
  release governance as stage-level gates, plus a final operator GUI stage that
  remains open by design, with `advanced_capability_expansion` as the current
  open stage.
- HTML and HTM files are now first-class local document-ingestion inputs. The
  parser extracts visible text, preserves title and link-count metadata in the
  source manifest, excludes script/style/template content from retrieval text,
  and remains under the same source-lineage and permissioned-ingest boundary as
  other local documents.
- DOCX files are now first-class local document-ingestion inputs. The parser
  reads the OpenXML document body, extracts paragraph and table text, preserves
  paragraph/table counts plus core document properties in the source manifest,
  and keeps extraction under the same source-lineage and permissioned-ingest
  boundary as other local documents.
- EML files are now first-class local document-ingestion inputs. The parser
  reads RFC 822/MIME messages, extracts selected headers and body text, uses the
  shared HTML extractor when only an HTML body is available, extracts bounded
  text-like TXT, Markdown, HTML, CSV, and JSON attachments under explicit
  attachment markers, and records skipped attachments as source-manifest
  metadata and parser warnings rather than silently treating them as ingested
  evidence.
- MSG files now have a bounded local text-probe ingestion path. The parser
  records `outlook_msg_text_probe_v1`, extracted text segment counts, byte
  counts, source hashes, and parser warnings while keeping full Outlook
  structured-property extraction marked as a future parser upgrade.
- `mucaro release state` classifies bounded release progress across
  `CANONICAL_LOCAL`, `LOCAL_RELEASE_CERTIFIED`, `FRESH_CLONE_CERTIFIED`,
  `SOURCE_ARTIFACT_PACKAGING`, `SOURCE_ARTIFACT_CERTIFIED`,
  `PYTHON_DIST_CERTIFIED`, `ANSWER_TRACE_QUALITY_CERTIFIED`,
  `RETRIEVAL_QUALITY_CERTIFIED`, `GIS_QUALITY_CERTIFIED`, `OPERATOR_WORKFLOW_CERTIFIED`,
  `GITHUB_GOVERNANCE_OPEN`, `GOVERNED_RELEASE_READY`, and
  `PRODUCTION_READY`. It keeps heavy local and fresh-clone certification states
  `UNVERIFIED` unless matching exact-HEAD receipts exist under
  `data/exports/release_receipts`.
- `mucaro release receipts` and `mucaro release receipt RECEIPT_ID` expose the
  local release/fresh-clone certification receipt ledger through the supported
  CLI boundary, including schema validity, bound head SHA, status, and computed
  receipt file SHA-256. Receipt listings also summarize valid/invalid counts by
  receipt type and status.
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
  lexical, vector, BM25, hybrid, persistent, and semantic strategies across 25
  chunks, 31 queries, 25 positive queries, 6 no-context queries, 28 expected
  bindings, 25 source IDs, 3 multi-binding queries, and 20 query categories.
- Large retrieval eval gate: `RETRIEVAL_EVAL_OK` with
  `RETRIEVAL_FIXTURE_OK` for the larger deterministic fixture across 51 chunks,
  65 queries, 55 positive queries, 10 no-context queries, 60 expected bindings,
  51 source IDs, 5 multi-binding queries, and 20 query categories.
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
- Historical marker: commit `17d9198c25f03b361b52db0a05e17adecc478e7f` added generated
  local-corpus answer-trace evaluation cases. The evaluator now writes isolated
  Markdown/TXT fixture sources into a temp project, ingests them through the
  normal source-lineage path, asks through mock retrieval, and verifies cited
  source bindings, snippet terms, citation records, and export paths.
- The generated answer-trace baseline is tagged as
  `v0.1.23-generated-answer-trace-eval`. Those receipts remain historical
  evidence for that exact commit, not current-head proof for later release
  candidates.
- Local release certification for commit
  `17d9198c25f03b361b52db0a05e17adecc478e7f` reports
  `CERTIFY_RELEASE_OK`, including `181 passed`, answer-trace quality,
  retrieval quality, default and large retrieval eval gates, JSON validation,
  desktop package self-test, source artifact build/verify, npm audit, and
  frontend build.
- Exact-SHA fresh-clone certification reports `FRESH_CLONE_CERT_OK` for commit
  `17d9198c25f03b361b52db0a05e17adecc478e7f`, including clean clone install,
  CLI help, launcher self-test, and desktop smoke test.
- Hosted `CI` run `34002438261` and `CI Startup Probe` run `34002438271` both
  report `startup_failure` with `0` jobs for commit
  `17d9198c25f03b361b52db0a05e17adecc478e7f`; GitHub governance remains open at
  the hosted startup/account/repository-runner layer.
- `mucaro release governance-report --save` produced
  `data/exports/governance_reports/governance-report-17d9198c25f0.json` with
  SHA-256
  `094bd7f44e1073596f37506f9f951423ee623750b42209d639ea35017c24eb99`.
  That artifact belongs to the historical `17d9198c25f0` checkout; do not use
  `--require-current-head` against later heads.
- `mucaro certify operator-workflow` now certifies the local operator workflow
  boundary in an isolated temp project. It proves external directory permission
  gating, source ingestion and index rebuilds, mock cited answer generation with
  active memory, trace exports, run-history persistence, memory
  disable/export/delete controls, and approval-gated local tool execution with
  audit events.
