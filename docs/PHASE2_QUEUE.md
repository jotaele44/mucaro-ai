# Phase 2 Queue

| Item | Purpose | Status |
|---|---|---|
| Release-grade development goal | Scope the finish line across CLI, retrieval quality, source ingestion, answer traceability, memory, tools, packaging, fresh clone, and hosted governance | Current anchor in `docs/DEVELOPMENT_GOAL.md` |
| Local hybrid retrieval | Deterministic lexical plus TF-IDF vector scoring | Complete in V1 |
| Retrieval eval harness | recall@k, top-1, and no-context gate for retrieval changes | Complete in V1 |
| Persistent local TF-IDF index | Local stored vector index with explicit `persistent` strategy | Complete in V1.4 |
| Local BM25 retrieval | Opt-in BM25 ranking for stronger local term matching | Complete candidate in V1.14 |
| Explicit local memory | Opt-in preference/profile memory with inspect, disable, delete, export, and answer-context wiring | Complete candidate in V1.20 |
| Production security profile | Single-operator production auth, explicit CORS, public-bind guard, startup validation | Complete candidate in V1.21 |
| Intelligence Plane descriptor | Machine-readable provisional repo role/capability declaration | Candidate in Intelligence Plane foundation |
| Federation Skill contract | Immutable skill identity/version/digest/runtime/capability schema | Candidate in Intelligence Plane foundation |
| Skill mount runtime | Mount → authorize → activate → execute lifecycle with local execution ledger | Candidate in Intelligence Plane foundation |
| Execution contract | Runtime/execution identity and status schema | Candidate in Intelligence Plane foundation |
| Model runtime contract | Provider/model/mode/capability schema independent of provider implementation | Candidate in Intelligence Plane foundation |
| Provider-neutral model runtime | Replace narrow provider branching with adapter registry while preserving fail-closed live-mode behavior | Complete candidate |
| Local worker runtime adapter | Isolate general skills from the app process | Next |
| Container runtime adapter | Reproducible heavy GIS/OCR/media execution with pinned runtime identity | Queued |
| Execution router | Choose IN_PROCESS / LOCAL_WORKER / CONTAINER / SERVICE / FEDERATION_WORKER by policy, never by skill fiat | Queued |
| Capability policy engine | Bind filesystem/network/database/subprocess/model permissions to explicit policy | Next |
| Artifact identity/provenance | Input/output handles, SHA-256 identity, staged commit, execution receipts | Next |
| Exact-head release handoff | Distribution manifest, governance report, and support bundle verify against the canonical head while preserving hosted governance as OPEN | Current baseline is CLI-bound; verify with `mucaro audit completion --include-github` |
| Hosted governance remediation | Resolve GitHub Actions startup-before-jobs, billing visibility, and private branch-protection plan gate | Open external lane |
| Advanced capability contracts | Shared evidence, permission, uncertainty, contradiction, and falsification schema for GIS, OCR, browsing, and coding outputs | Complete candidate; inspect with `mucaro capabilities contracts` |
| GIS output contract validator | Executable receipt validation for advanced GIS provenance, warnings, conservation invariants, topology repair, horizontal CRS transforms, ellipsoidal measurement, vector/raster manifests, vector-raster analysis, and map-export bindings | Complete bounded candidate; inspect saved GIS reports with `mucaro capabilities validate-gis-report` |
| OCR output contract validator | Executable receipt validation for local image OCR source metadata, engine identity, confidence scale, text tiers, warning consistency, and span/confidence accounting | Complete candidate; inspect saved OCR reports with `mucaro capabilities validate-ocr-report` |
| GUI operator workspace | Clean prompt-and-response task surface that keeps grants, source evidence, answer traces, memory, tools, maps, tables, code, artifacts, and readiness visible without hiding uncertainty | Final phase; CLI remains the certification boundary until backend capability gates, packaging, and hosted governance are stronger |
| GIS processing contract and evals | CRS, geometry/topology, joins, vector/raster manifests, raster sampling/clipping/resampling, zonal analysis, authoritative vector/raster snapshots, terrain and bounded hydrology measurement, map/export provenance, and positive/negative spatial fixtures | Bounded V1 CLI/eval/contract plane now runs 64 cases across 25 operations, including explicit topology repair, pyproj/PROJ always-XY transforms with offline-only candidate/grid provenance, one exact-source-bound public-domain NOAA Puerto Rico datum grid with tamper/path/network fail-closed controls, WGS84 ellipsoidal measurement with bounded antimeridian/polar line and polygon controls, GeoJSON/GeoPackage/Shapefile manifests, GeoTIFF NODATA accounting, transactional raster clipping/resampling, exact-CRS zonal statistics, byte-frozen Census/USGS vector corpus validation, one frozen USGS 3DEP raster export, projected-metre slope/aspect derivation, terrain-lineage-bound hillshade, bounded priority-flood depression conditioning with explicit outlet, no-breach, maximum-depth, and fill-volume controls, bounded D8 direction/accumulation/contributing area with explicit terminal ambiguity, and renderer/layer-bound export provenance. Queue additional regional datum grids, independent-engine datum validation, vertical/time cases, broader axis-order and large-geometry controls, dimensional mutation, topology/overlay policy, fractional zonal coverage, independent rendered-map validation, larger real vector/raster corpora, remote sensing, terrain algorithms beyond bounded slope/aspect/hillshade, hydrology beyond bounded priority-flood conditioning and D8, bathymetry, and temporal GIS. |
| OCR analysis contract and evals | Source-image hashes, OCR engine identity, layout/text preservation, confidence thresholds, and OCR regression fixtures | Local image OCR and PDF/page receipt candidates plus executable saved-report validation exist; image receipts now include engine-derived layout and confidence ledgers with span accounting. Labeled calibration, semantic table/form/handwriting coverage, and optional provider policy remain queued |
| Governed web browsing contract and evals | URL policy, fetch snapshots, parser metadata, content hashes, retrieval UTC, cache/robots posture, source-selection receipts, and web citation fixtures | Candidate fetch/snapshot/citation wiring with web answer-trace fixtures, operator-supplied multi-page session receipts, robots/cache metadata, redirect-target robots evidence, HTML body/structure traces, PDF/non-text parser traces, immutable snapshot-only cache policy, and explicit HTTP error-page citation policy; automated search/discovery, rendered-browser capture, and gated autonomous link-following queued |
| Coding capability contract and evals | Repository read policy, patch/test/review action boundary, sandbox posture, diff receipts, and code-quality regression fixtures | Queued |
| Cross-capability output validators | Enforce evidence taxonomy, warning consistency, conservation/arithmetic closure, unresolved residue, limitations, and falsification criteria across OCR, browsing, retrieval, and coding outputs | GIS and OCR report validators exist as executable candidates; browsing, retrieval, and coding validators queued |
| Agent/workflow DAG runtime | Durable multi-step plans with explicit state, restart/resume, and receipts | Queued |
| Federation membership discovery | Consume canonical membership and repo capability descriptors once federation environment is available | Blocked on federation execution environment |
| FEDERATION LOCKSTEP client | Compatibility/impact gate for skill activation and federation-visible contracts | Blocked on federation execution environment |
| Cross-repo orchestration | Invoke contract-bound domain capabilities while preserving repo autonomy | Blocked on federation execution environment |
| Chroma integration | Persistent embedding/vector store for larger corpora | Planned in `docs/RETRIEVAL_UPGRADE_PLAN.md`; queued after larger eval corpus |
| LlamaIndex ingestion | Richer document ingestion and node parsing | Queued |
| Text-like EML attachment extraction | Bounded extraction for TXT, Markdown, HTML, CSV, and JSON email attachments with explicit source-lineage markers | Complete candidate |
| Outlook MSG text probe | Bounded local text-segment extraction for MSG files with explicit heuristic parser metadata | Complete candidate |
| Unstructured parser expansion | Better PDFs, richer DOCX/HTML extraction, full Outlook MSG property extraction, binary email attachment parsing, and model-backed media understanding beyond metadata | Queued |
| GraphRAG entity layer | People/org/place/event graph extraction | Queued |
| MCP adapter | External protocol adapter beneath the Skill Fabric; not a separate authority model | Queued |
| PostGIS/GIS service adapter | Spatial source lineage and map exports behind explicit service capability | Queued |
| FOIA folder automation | Request packets, exhibits, response ledgers | Queued |
| PR-IIS/Spiderweb connector | Puerto Rico investigation federation layer | Blocked on federation execution environment |

## Architecture rule

Tool protocols, model providers, containers, local workers, and future federation workers are execution adapters. They do not bypass Skill Fabric identity, capability authorization, activation, execution-ledger, or certification boundaries.
