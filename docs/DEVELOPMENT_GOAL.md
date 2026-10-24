# Development Goal

## Goal Statement

Transform Mucaro AI from a validated local prototype into a governed,
release-grade, local-first AI workbench with hardened CLI workflows,
measurable retrieval quality, permissioned source ingestion, cited answer
traceability, explicit memory controls, approved tool execution, repeatable
fresh-clone certification, and a clear path toward production-quality
packaging. Capabilities must include advanced GIS processing,
state-of-the-art OCR analysis, powerful web browsing abilities, coding and
programming expertise, all governed by maximum transparency and objectivity
within a clean, simple, efficient, and powerful GUI for prompt-and-response task
execution.

The canonical source of truth is the `mucaro-ai` repository. The previous
prototype lineage remains useful as historical evidence, but new development,
release receipts, tags, governance checks, and packaging work must start from
the canonical repo.

## Flagpole End State

`MUCARO_FLAGPOLE_V1` is the frozen umbrella development denominator. Its normative human-readable contract is `docs/MUCARO_FLAGPOLE_V1.md`; its machine-readable denominator and dependency DAG are `_architecture/mucaro_flagpole_v1.json`.

The flagpole supersedes undefined "100% functionality" as the completion target without superseding narrower passed receipts. Development must inventory and reuse existing evidence, classify each required domain as IMPLEMENTED | PARTIAL | ABSENT | UNVERIFIED, and close only evidenced gaps. Mucaro is not the underlying model provider; session, context, and agent-run lifecycles remain separate; tool presence is not capability proof; and no aggregate readiness percentage can conceal a required-domain OPEN, BLOCKED, FAIL, or UNRESOLVED state.

The frozen pre-flagpole baseline is main SHA `f8fb2e9fbf6ed2675b3ff4f3eb51e858d394c586`. This SHA is historical baseline identity, not a claim that later flagpole commits retain that HEAD.

## Current Position

Mucaro AI has crossed the prototype-promotion line. The local product surface
now includes a real CLI, local-first retrieval, source-lineage ingestion,
cited answers, explicit memory controls, an approval-gated tool runtime,
release receipts, package artifacts, fresh-clone certification, and a
conversation-first local GUI. The GUI now exposes Chat as the canonical primary
surface with dedicated Sources, Directory Access, Memory, Planning, and Settings
workspaces; rendered desktop/mobile/accessibility proof remains a certification
task rather than an unimplemented source feature. The conversational run bus now
also exposes a bounded `agent` mode that infers up to four allowlisted actions
from explicit user intent while preserving approval, interruption, streaming,
and receipt boundaries. An explicit `provider_native` planner path now
normalizes Anthropic and OpenAI-compatible tool proposals, preserves provider
call identity, and fail-closes before the shared approval ledger. Agent work
runs now also create exact-head checkpoints for inferred steps, bind checkpoint
step identity into tool provenance, close each step idempotently through
approval, execution, interruption, and terminal-run paths, and validate
deterministic dependency DAGs with fail-closed cycle/unknown-edge handling and
downstream blocking, and explicit non-executed failure closure for skipped
steps. Agent runs now execute dependency-independent approved steps in bounded
parallel batches, defaulting to two workers and rejecting values above four;
batch size and exact step identities are persisted in checkpoint metadata and
tool-batch events. Explicit provider-native model tool loops now persist the
provider message history, return terminal tool results to the provider, enforce
a bounded turn count, and route continuation calls through the same approval
and checkpoint ledger. Subagents, fleet scheduling, and broader crash-resume
certification remain open.

The finish line is no longer "make the repo exist." The finish line is to make
the repo repeatedly prove its behavior from a clean clone through ingestion,
retrieval, answer generation, trace export, tool approval, memory inspection,
packaging, and hosted governance.

The expanded capability finish line is also not "add adapters." GIS, OCR,
web browsing, and coding-agent behavior must each be permissioned, local-first
where possible, receipt-backed, evaluated against positive and negative
fixtures, and explicit about uncertainty, contradictions, source identity, and
limits.

## Required Proof Boundaries

| Boundary | Required proof |
|---|---|
| CLI workflows | Stable commands, JSON/text output contracts, exit codes, error payloads, and tests for normal and failure paths |
| Retrieval quality | Fixture, large-corpus, mixed-format, category, binding, precision, multi-binding, and distractor gates |
| Source ingestion | Permissioned local inputs, parser metadata, source IDs, chunk IDs, file-format evidence, and no silent parser success |
| Answer traceability | Citation order, retrieval rank, source ID, chunk ID, source format, score, matched terms, snippets, and export paths |
| Memory controls | Explicit opt-in, inspect, export, disable, delete, and local-only persistence behavior |
| Tool execution | Deny-by-default request, approve or deny, execute only after approval, and append-only local audit events |
| Advanced GIS processing | CRS preservation, geometry type and validity, spatial operation receipts, topological assertions, map/export provenance, and spatial regression fixtures |
| OCR analysis | Source-image hashes, OCR engine identity, layout/text preservation, confidence evidence, negative fixtures, and no silent text synthesis |
| Web browsing | URL policy, retrieval UTC, byte snapshots, parser metadata, content hashes, citation binding, robots/cache posture, and failure-preserving fetch logs |
| Coding expertise | Codebase read policy, patch proposal receipts, diff/test evidence, sandbox boundaries, review output, and no hidden workspace mutation |
| Transparency/objectivity | FACT/INFERENCE/ASSUMPTION/UNKNOWN separation, limitations, contradiction handling, falsification criteria, and reproducible verification hooks |
| GUI/operator workflow | Final-phase clean prompt-and-response surface, efficient task execution, synchronized work feed, specialized workspaces, and isolated proof that permission grants, ingestion, retrieval, answer traces, memory controls, run history, and approved tools work together after backend/CLI gates are strong enough to expose |
| Fresh clone | Exact-ref clone, install, CLI help, release check, launcher self-test, and desktop smoke certification |
| Packaging | Source artifact manifest, archive SHA-256, member hashes, Python wheel/sdist receipts, source-bound local macOS Application bundle, local distribution handoff manifest, package status gate, and a separate fail-closed Mac App Store policy/candidate audit with sandbox, privacy, entitlement, provisioning, and external-review boundaries |
| Hosted governance | Current-head hosted CI, startup probe, branch-protection visibility, tag binding, and clear external blocker classification |

## Current Baseline

The authoritative current baseline is the exact Git `HEAD` reported by the
CLI, not a hard-coded commit in this tracked document. Because editing this file
changes `HEAD`, use the commands below after every commit before making a
current-baseline claim:

```bash
mucaro release receipts --current-head --format text
mucaro package distribution-manifest --verify data/exports/distribution_manifests/distribution-manifest-HEADPREFIX.json --require-current-head --format text
mucaro release governance-report --verify data/exports/governance_reports/governance-report-HEADPREFIX.json --require-current-head --format text
mucaro release support-bundle --verify data/exports/support_bundles/support-bundle-HEADPREFIX.json --require-current-head --format text
mucaro audit completion --include-github --format text
```

The exact-head local release baseline is certified only when the current
checkout has eight valid release receipts:

- `ANSWER_TRACE_QUALITY_CERT_OK`
- `CERTIFY_RELEASE_OK`
- `GIS_QUALITY_CERT_OK`
- `RETRIEVAL_QUALITY_CERT_OK`
- `OPERATOR_WORKFLOW_CERT_OK`
- `PYTHON_DIST_CERT_OK`
- `SOURCE_ARTIFACT_CERT_OK`
- `FRESH_CLONE_CERT_OK`

The current local release receipt should also record backend tests, secret
scanning, generated-artifact scanning, frontend build, frontend audit, JSON
validation, package artifact build and verify, and desktop launcher checks.

Saved handoff artifacts under `data/exports/distribution_manifests`,
`data/exports/governance_reports`, and `data/exports/support_bundles` are the
byte-pinned proof layer for a particular commit. Verify them with
`--require-current-head` before using them as current evidence.

## Open Finish-Line Gaps

| Gap | State | Next proof |
|---|---|---|
| Hosted GitHub Actions | OPEN | Hosted CI is again admitting and executing jobs, and exact-SHA CI evidence artifact generation is merged; final release-grade proof still requires a retrievable receipt bound to the exact current `main` head plus required branch/tag governance |
| Private branch protection | OPEN | GitHub API currently reports a plan gate for private-repo branch protection; prove protection after plan/visibility changes or keep the release scope local |
| Larger real-corpus retrieval | OPEN | The deterministic real-world corpus materializer and exact-denominator certification gate are merged; supply qualifying nonsynthetic source bytes/provenance plus a bound evaluation receipt and production-scale stress evidence |
| Advanced GIS processing | OPEN | The CLI, saved-report validator, and 70-case/26-operation eval denominator now cover bounded GeoJSON operations, explicit topology repair, pyproj/PROJ horizontal CRS transformation with always-XY, offline-only candidate/grid provenance, pipeline identity, and one exact-source-bound public-domain NOAA Puerto Rico datum grid with frozen manifest/hash/byte/license provenance and no runtime download, WGS84 ellipsoidal measurement with frozen antimeridian/polar line and polygon controls, GeoJSON/GeoPackage/Shapefile manifestations, GeoTIFF inspection/sampling/transactional clipping/resampling, exact-CRS vector-raster cell-center and projected-planar fractional-coverage zonal analysis with coverage-area closure and weighted statistics, frozen Census/USGS vector snapshots, one frozen USGS 3DEP raster export, projected-metre slope/aspect derivation with complete-neighborhood accounting, terrain-lineage-bound hillshade with explicit illumination and zero/NODATA separation, bounded priority-flood depression conditioning with explicit raster-edge/NODATA-adjacent outlets, no-breach policy, maximum fill depth, and volume accounting, bounded D8 direction/accumulation/contributing-area derivation with explicit boundary/sink/flat/tie terminal states, renderer/layer-bound map-export provenance, and independent image-level rendered-map validation for decoded dimensions, alpha closure, and nonblank visible pixels. Remaining scope includes additional regional datum grids, independent-engine datum validation, vertical/time-dependent CRS, broader axis-order and large-geometry adjudication beyond the bounded controls, dimension-preserving mutation, broader topology/overlay policy, semantic rendered-map correctness and cartographic accessibility, larger and more diverse real vector/raster corpora, additional vector/raster formats, terrain algorithms beyond bounded slope/aspect/hillshade, hydrology beyond bounded priority-flood conditioning and D8, remote sensing, bathymetry, and temporal GIS. |
| State-of-the-art OCR analysis | OPEN | Local image OCR and PDF/page receipts exist through `mucaro ocr analyze`; saved image and PDF receipts are now checked by `mucaro capabilities validate-ocr-report`; `mucaro ocr calibrate` measures exact span labels, confidence coverage, Brier score, reliability bins, and threshold precision with exact report-byte binding. Semantic table/form/handwriting coverage, independently authored representative labels, optional provider privacy gates, and broader OCR eval corpus remain open |
| Governed web browsing | OPEN | Governed fetch/session evidence, static discovery, bounded autonomous traversal, credential-last search, and a rendered-browser evidence adapter are merged; execute credentialed search and frozen live Playwright/Chromium corpora before WEB-08/09 promotion |
| Coding/programming expertise | OPEN | Read/diff/patch/review/Git lifecycle, isolated container source, check-suite receipts, and coding certification ledger are merged; close live pinned-container execution and execute the frozen coding regression corpus before certification |
| Transparency/objectivity across capabilities | OPEN | Shared executable evidence-contract validators now cover saved GIS/OCR reports, frozen web snapshots, operator-selected web sessions, cited answer traces, retrieval output integrity, skill execution lineage, and coding certification reports. Live search/browser/container execution, broader contradiction adjudication, and complete cross-capability certification remain open |
| Final-phase GUI/operator workspace | OPEN | Conversation-first GUI source is merged; execute rendered desktop/mobile/accessibility and representative-workflow QA, then add only nonduplicative specialized map/table/code/artifact surfaces justified by operator workflows |
| Production packaging | OPEN | The x86_64 macOS runtime source is now bound to an exact upstream archive byte count/SHA-256/logical shape and a hash-locked wheel-only dependency graph; materialization verifies imports, relocation, no remaining symlinks, and host user-site exclusion. Portable app construction rejects tracked worktree drift, isolates interpreter startup, prevents bytecode writes, keeps mutable data outside the bundle, and requires exact post-launch member-manifest verification. Transactional install/reinstall/rollback, fail-closed release-grade matrix, macOS signing/clean-machine verification commands, and a machine-readable Mac App Store security profile plus candidate auditor are source-backed. Direct-distribution independently witnessed clean-machine/signing/notarization evidence remains external; arm64/universal runtime coverage remains open; the Store lane additionally requires a self-contained sandboxed build, native security-scoped file access, loopback architecture resolution, final privacy inventory, Mac App Distribution signing/provisioning, App Store Connect processing, and Apple App Review. |
| Sustained workflow validation | OPEN | Run end-to-end operator workflows across ingest, ask, trace review, memory, tools, receipts, and recovery paths |

## Development Vector

1. Keep the CLI as the primary operator boundary for release-grade behavior.
2. Expand retrieval and answer-trace evidence before introducing heavier vector
   stores or model-backed embeddings.
3. Use the release dashboard as the operator's current-state summary instead of
   inferring readiness from individual receipts or artifacts.
4. Use `mucaro audit completion --include-github` as the active-goal audit
   before broad completion claims; it binds CLI, retrieval, answer trace,
   memory, tool, fresh-clone, packaging, and hosted-governance requirements to
   current evidence.
5. Use `mucaro audit product` and `mucaro audit roadmap` to keep expanded GIS,
   OCR, browsing, coding, and transparency/objectivity gaps visible until they
   have implemented contracts and receipt-backed gates.
6. Use `mucaro capabilities contracts` to inspect the shared proof contracts
   that advanced capability adapters must satisfy before availability claims.
7. Leave GUI expansion for the final phase; keep the CLI as the scriptable
   certification boundary while backend capability contracts mature.
8. Use support bundles to escalate hosted governance blockers without
   weakening local certification boundaries.
9. Treat all future execution adapters as subordinate to Skill Fabric identity,
   permission, audit, and certification boundaries.
10. Preserve local-first default behavior and fail closed whenever live provider,
   hosted runner, optional dependency, or index state is missing.
11. Keep release claims scoped: local certification can pass while hosted
   governance remains open.

## Readiness Rule

Do not call Mucaro AI production-ready until every required boundary has
current-head proof and no unresolved residue inside the claimed scope. A passing
local release is a strong local baseline, not proof that hosted governance,
distribution, real-corpus quality, or sustained production operations are
complete.
