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

## Current Position

Mucaro AI has crossed the prototype-promotion line. The local product surface
now includes a real CLI, local-first retrieval, source-lineage ingestion,
cited answers, explicit memory controls, an approval-gated tool runtime,
release receipts, package artifacts, fresh-clone certification, and a
conversation-first local GUI. The GUI now exposes Chat as the canonical primary
surface with dedicated Sources, Directory Access, Memory, Planning, and Settings
workspaces; rendered desktop/mobile/accessibility proof remains a certification
task rather than an unimplemented source feature.

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
| Packaging | Source artifact manifest, archive SHA-256, member hashes, Python wheel/sdist receipts, source-bound macOS Application bundle, local distribution handoff manifest, and package status gate |
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
| Advanced GIS processing | OPEN | The CLI, saved-report validator, and 52-case/23-operation eval denominator now cover bounded GeoJSON operations, explicit topology repair, pyproj/PROJ horizontal CRS transformation with always-XY, offline-only candidate/grid provenance, and pipeline identity, WGS84 ellipsoidal measurement with frozen antimeridian/polar line and polygon controls, GeoJSON/GeoPackage/Shapefile manifestations, GeoTIFF inspection/sampling/transactional clipping/resampling, exact-CRS vector-raster cell-center zonal analysis, frozen Census/USGS vector snapshots, one frozen USGS 3DEP raster export, projected-metre slope/aspect derivation with complete-neighborhood accounting, terrain-lineage-bound hillshade with explicit illumination and zero/NODATA separation, and renderer/layer-bound map-export provenance. Remaining scope includes independently frozen local datum-grid transformations, vertical/time-dependent CRS, broader axis-order and large-geometry adjudication beyond the bounded controls, dimension-preserving mutation, broader topology/overlay policy, fractional zonal coverage, independent rendered-map validation, larger and more diverse real vector/raster corpora, additional vector/raster formats and terrain algorithms beyond bounded slope/aspect/hillshade, remote sensing, hydrology, bathymetry, and temporal GIS. |
| State-of-the-art OCR analysis | OPEN | Local image OCR receipt candidate exists through `mucaro ocr analyze`, and saved image OCR receipts can be checked with `mucaro capabilities validate-ocr-report`; add PDF/page rendering, layout calibration, document/table/form/handwriting fixtures, confidence thresholds, optional provider privacy gates, and broader OCR eval corpus |
| Governed web browsing | OPEN | Governed fetch/session evidence, static discovery, bounded autonomous traversal, credential-last search, and a rendered-browser evidence adapter are merged; execute credentialed search and frozen live Playwright/Chromium corpora before WEB-08/09 promotion |
| Coding/programming expertise | OPEN | Read/diff/patch/review/Git lifecycle, isolated container source, check-suite receipts, and coding certification ledger are merged; close live pinned-container execution and execute the frozen coding regression corpus before certification |
| Transparency/objectivity across capabilities | OPEN | Initial GIS and OCR report validation exists for saved advanced GIS and local image OCR receipts; expand executable validators so provenance, uncertainty, limitations, contradictory evidence handling, falsification criteria, warning states, and arithmetic closure are enforced across browsing, retrieval, and coding outputs |
| Final-phase GUI/operator workspace | OPEN | Conversation-first GUI source is merged; execute rendered desktop/mobile/accessibility and representative-workflow QA, then add only nonduplicative specialized map/table/code/artifact surfaces justified by operator workflows |
| Production packaging | OPEN | Embedded portable runtime, transactional install/reinstall/rollback, fail-closed release-grade matrix, and macOS signing/clean-machine verification commands are source-backed; actual clean-machine execution and signed/notarized distribution receipts remain external blockers |
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
