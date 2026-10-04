# Mucaro CLI

The `mucaro` CLI is the operator surface for local ingestion, retrieval-backed
answers, Planning Gate analysis, validation, and local serving.

## Install

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

After installation, use:

```bash
mucaro --help
mucaro --version
```

For one-off development without installing the console entry point:

```bash
PYTHONPATH=.:backend python3 -m backend.app.cli --help
```

## Commands

| Command | Purpose | Success Exit | Failure Exit |
|---|---|---:|---:|
| `mucaro doctor` | Check local assistant readiness | 0 | 2 with `--strict` warnings/failures |
| `mucaro` / `mucaro tui` / `mucaro agent` | Open the persistent terminal workspace over canonical conversations, runs, agent DAGs, approvals, GitHub receipts, and context telemetry | 0 | 2 on terminal/projection failure |
| `mucaro tui --snapshot` | Render one deterministic headless terminal frame without requiring a TTY | 0 | 2 |
| `mucaro release state` | Classify bounded release-state progress | 0 | 2 with `--strict` until governed release is ready |
| `mucaro release dashboard` | Summarize release state, receipts, handoff artifacts, remote binding, and blockers | 0 | 2 on local evidence failure, or with `--strict` until ready |
| `mucaro release hosted-governance` | Print hosted GitHub governance blocker evidence and exact operator remediation commands | 0 | 2 with `--strict` until hosted governance is ready |
| `mucaro release incident-packet` | Build or verify a hosted-governance escalation packet with support-ready copy lines | 0 | 2 on packet build or verification failure |
| `mucaro release next-actions` | Print or save prioritized release actions, current run handles, handoff artifact paths, support-copy lines, and verification commands | 0 | 2 with `--strict` while actions remain open, or on failed artifact verification |
| `mucaro release handoff` | Save and verify the current-head distribution manifest, governance report, support bundle, and next-actions runbook in order | 0 | 2 on local artifact failure, or with `--strict` while hosted governance remains open |
| `mucaro release support-bundle` | Build or verify a support-ready release evidence bundle | 0 | 2 |
| `mucaro release receipts` | List local release/fresh-clone certification receipts | 0 | 2 |
| `mucaro release receipt RECEIPT_ID` | Show one local certification receipt | 0 | 2 |
| `mucaro certify status` | Inspect release readiness before running full certification gates | 0 | 2 on failures, or with `--strict` warnings/failures |
| `mucaro audit completion` | Map the active release-grade goal to evidence-backed PASS/OPEN/BLOCKED/FAIL/UNVERIFIED rows | 0 | 2 on failure/blocker, or with `--strict` until ready |
| `mucaro audit product` | Audit product gaps against the full assistant objective | 0 | 2 with `--strict` while gaps remain |
| `mucaro audit roadmap` | Group product-audit rows into development stages | 0 | 2 with `--strict` while stages remain open |
| `mucaro eval retrieval` | Evaluate local retrieval quality against fixture suites | 0 | 2 |
| `mucaro eval answer-trace` | Evaluate cited-answer trace records against positive and negative fixtures | 0 | 2 |
| `mucaro eval gis` | Evaluate local GIS receipt behavior against positive and negative spatial fixtures | 0 | 2 |
| `mucaro certify retrieval-quality` | Write an exact-head retrieval-quality release receipt | 0 | 2 |
| `mucaro certify answer-trace-quality` | Write an exact-head cited-answer traceability release receipt | 0 | 2 |
| `mucaro certify gis-quality` | Write an exact-head GIS fixture-quality release receipt | 0 | 2 |
| `mucaro certify operator-workflow` | Write an exact-head local operator-workflow release receipt | 0 | 2 |
| `mucaro capabilities contracts` | List advanced GIS, OCR, browsing, coding, and transparency/objectivity proof contracts | 0 | 2 |
| `mucaro capabilities validate-gis-report REPORT_PATH` | Validate a saved GIS receipt against the advanced capability output contract | 0 | 2 |
| `mucaro capabilities validate-ocr-report REPORT_PATH` | Validate a saved OCR receipt against the state-of-the-art OCR output contract | 0 | 2 |
| `mucaro gis inspect GEOJSON_PATH` | Inspect a GeoJSON FeatureCollection and write a CRS/geometry/topology receipt | 0 | 2 |
| `mucaro gis point-in-polygon POINTS_PATH POLYGONS_PATH` | Classify point features against polygon features and write a spatial join receipt | 0 | 2 |
| `mucaro gis compare LEFT_PATH RIGHT_PATH` | Compare GeoJSON geometries and write an equality/topology receipt | 0 | 2 |
| `mucaro gis overlay LEFT_PATH RIGHT_PATH` | Overlay matched polygon GeoJSON features and write a derived-output receipt | 0 | 2 |
| `mucaro gis reproject GEOJSON_PATH --target-crs CRS` | Reproject GeoJSON between supported local CRS pairs and write a transformation receipt | 0 | 2 |
| `mucaro gis reproject-crs GEOJSON_PATH --target-crs CRS` | Reproject GeoJSON through pyproj/PROJ with explicit axis policy and frozen transformer identity | 0 | 2 |
| `mucaro gis csv-points CSV_PATH --lat-column LAT --lon-column LON` | Convert CSV coordinate rows into GeoJSON point features and write a row-accounted GIS receipt | 0 | 2 |
| `mucaro gis filter-bbox GEOJSON_PATH --min-x X --min-y Y --max-x X --max-y Y` | Filter whole GeoJSON features by bbox extent and write retained/excluded accounting | 0 | 2 |
| `mucaro gis clip-bbox GEOJSON_PATH --min-x X --min-y Y --max-x X --max-y Y` | Clip GeoJSON geometries to a bbox and write clipped/unchanged/excluded accounting | 0 | 2 |
| `mucaro gis measure GEOJSON_PATH` | Measure GeoJSON feature area/length with planar or EPSG:4326 geodesic receipts | 0 | 2 |
| `mucaro gis repair-topology GEOJSON_PATH` | Explicitly repair planar XY topology with before/after geometry hashes and retained/excluded accounting | 0 | 2 |
| `mucaro gis measure-ellipsoidal GEOJSON_PATH` | Measure EPSG:4326 features on the WGS84 ellipsoid with pyproj Geod | 0 | 2 |
| `mucaro gis inspect-vector VECTOR_PATH` | Inspect GeoJSON, GeoPackage, or Shapefile source manifestations without inferring cross-format identity | 0 | 2 |
| `mucaro gis inspect-raster RASTER_PATH` | Inspect raster dimensions, CRS, bands, dtypes, and NODATA accounting | 0 | 2 |
| `mucaro gis sample-raster RASTER_PATH --point X Y` | Sample explicit coordinates in the raster CRS while separating values, NODATA, and outside states | 0 | 2 |
| `mucaro gis clip-raster-bbox RASTER_PATH --min-x X --min-y Y --max-x X --max-y Y` | Transactionally clip a raster bbox and hash the derived artifact | 0 | 2 |
| `mucaro gis vector-raster VECTOR_PATH RASTER_PATH` | Summarize raster cells covered by GeoJSON features with exact-CRS and NODATA controls | 0 | 2 |
| `mucaro gis zonal-stats VECTOR_PATH RASTER_PATH` | Compute cell-center zonal count/sum/min/max/mean/median/population-standard-deviation receipts with zero/NODATA separation | 0 | 2 |
| `mucaro gis resample-raster RASTER_PATH --width N --height N --method METHOD` | Transactionally resample to explicit dimensions while preserving extent, CRS, band count, and NODATA declarations | 0 | 2 |
| `mucaro gis derive-terrain RASTER_PATH --elevation-units meters` | Derive two-band slope/aspect output from a projected metre elevation grid with complete 3x3-neighborhood accounting | 0 | 2 |
| `mucaro gis derive-hillshade RASTER_PATH --elevation-units meters` | Derive terrain-lineage-bound 0..255 hillshade with explicit azimuth/altitude and zero/NODATA separation | 0 | 2 |
| `mucaro gis condition-depressions RASTER_PATH --elevation-units meters --maximum-fill-depth-m M` | Fill bounded enclosed depressions to their lowest reachable spill elevation with explicit outlet, no-breach, depth-limit, and volume receipts | 0 | 2 |
| `mucaro gis derive-d8-flow RASTER_PATH --elevation-units meters` | Derive four-band D8 candidates, accumulation, contributing area, and explicit terminal-state output on a bounded projected-metre grid | 0 | 2 |
| `mucaro gis validate-corpus MANIFEST_PATH` | Validate frozen authoritative GIS corpus bytes, provenance, explicit asset types, vector identities/geometries, and raster grids/value accounting | 0 | 2 |
| `mucaro gis map-export-manifest OUTPUT_PATH --layer PATH CRS ...` | Bind an existing map/export artifact to renderer identity, extent, CRS, and source-layer hashes | 0 | 2 |
| `mucaro ocr analyze IMAGE_PATH` | Run local image OCR and write a source-image, confidence, and layout receipt | 0 | 2 |
| `mucaro ingest PATH` | Ingest one supported file or a folder | 0 | 2 |
| `mucaro ask QUERY` | Answer from ingested local sources | 0 | 2 or 3 |
| `mucaro sources list` | List ingested sources, persistent index status, and semantic-store status | 0 | 2 |
| `mucaro sources reindex` | Rebuild the local persistent index and semantic store | 0 | 2 |
| `mucaro sources delete SOURCE_ID` | Delete one ingested source and rebuild the index | 0 | 2 |
| `mucaro index status` | Inspect local persistent index and semantic-store health | 0 | 2 |
| `mucaro conversations list` | List saved local chat sessions | 0 | 2 |
| `mucaro conversations create` | Create a saved local chat session | 0 | 2 |
| `mucaro conversations ask CONVERSATION_ID QUERY` | Ask with saved conversation context | 0 | 2 or 3 |
| `mucaro conversations show CONVERSATION_ID` | Show a saved conversation and its turns | 0 | 2 |
| `mucaro conversations rename CONVERSATION_ID TITLE` | Rename a saved conversation | 0 | 2 |
| `mucaro conversations delete CONVERSATION_ID` | Delete a saved conversation while preserving runs | 0 | 2 |
| `mucaro runs list` | List persisted answer traces | 0 | 2 |
| `mucaro runs summary` | Summarize persisted answer traces | 0 | 2 |
| `mucaro runs show RUN_ID` | Show one persisted answer trace | 0 | 2 |
| `mucaro memory list` | List explicit local memory items | 0 | 2 |
| `mucaro memory remember CONTENT` | Save an explicit memory item | 0 | 2 |
| `mucaro memory enable MEMORY_ID` | Re-enable one memory item | 0 | 2 |
| `mucaro memory disable MEMORY_ID` | Disable one memory item without deleting it | 0 | 2 |
| `mucaro memory delete MEMORY_ID` | Delete one memory item | 0 | 2 |
| `mucaro memory export` | Export all explicit memory items | 0 | 2 |
| `mucaro security status` | Inspect local or production security readiness | 0 | 2 |
| `mucaro package status` | Inspect packaging and distribution readiness | 0 | 2 on failures, or with `--strict` warnings/failures |
| `mucaro package build` | Build the governed source ZIP and artifact manifest | 0 | 2 |
| `mucaro package verify MANIFEST` | Verify a governed source ZIP against its artifact manifest | 0 | 2 |
| `mucaro package macos-app` | Build or verify the double-clickable local macOS Application bundle | 0 | 2 |
| `mucaro package app-store-audit` | Audit the Mac App Store source policy or one signed candidate bundle | 0 for valid open/provisional state | 2 on failure, or always with `--strict` until external Store gates close |
| `mucaro package distribution-manifest` | Build or verify the local distribution handoff manifest | 0 | 2 |
| `mucaro sources ingest-web-snapshot SNAPSHOT_PATH` | Ingest one governed web snapshot as a citeable local source | 0 | 2 |
| `mucaro tools list` | List locally allowlisted tools | 0 | 2 |
| `mucaro tools request TOOL_NAME` | Create a pending tool-call request | 0 | 2 |
| `mucaro tools approve TOOL_CALL_ID` | Approve a pending tool call | 0 | 2 |
| `mucaro tools deny TOOL_CALL_ID` | Deny a pending tool call | 0 | 2 |
| `mucaro tools run TOOL_CALL_ID` | Execute an approved tool call | 0 | 2 |
| `mucaro tools audit` | Inspect local tool runtime audit events | 0 | 2 |
| `mucaro plan REQUEST` | Analyze a task with the Planning Gate | 0 | 2 |
| `mucaro permissions grant PATH` | Grant bounded user-directory access | 0 | 2 |
| `mucaro permissions list` | List directory access grants | 0 | 2 |
| `mucaro permissions check PATH` | Check directory access | 0 | 2 |
| `mucaro permissions revoke PATH` | Revoke directory access | 0 | 2 |
| `mucaro permissions lease-list` | List bounded run-scoped tool permission leases | 0 | 2 |
| `mucaro permissions lease-grant RUN_ID` | Grant an explicit run-scoped tool permission lease | 0 | 2 |

For consequential actions, add `--arguments-sha256 <64-hex>` to bind the lease to one exact canonical tool-argument payload. A changed argument set will not match that lease.
| `mucaro permissions lease-revoke LEASE_ID` | Revoke one exact run-scoped permission lease | 0 | 2 |
| `mucaro validate` | Validate JSON artifacts and schemas | 0 | 2 |
| `mucaro serve` | Start the local FastAPI server | 0 | 2 |

Global flags:

- `--version` prints the installed package version for fresh-clone and support
  receipts.

## Terminal workspace

Running `mucaro`, `mucaro tui`, or `mucaro agent` opens the same persistent
full-screen terminal workspace. The terminal is a projection over canonical
backend state; it does not maintain a second conversation, run, approval,
checkpoint, GitHub, or context database.

The left rail contains **Current**, **Sessions**, **Issues**, **Pull Requests**,
and **Gists**. GitHub views never fetch directly from the renderer. They display
only results from governed `github.*` tool calls and preserve each result's
`retrieved_utc`.

Useful terminal commands:

```text
:mode plan
:mode interactive
:mode auto
:mode autopilot

:new Optional title
:open CONVERSATION_ID

:issues OWNER/REPO
:prs OWNER/REPO
:gists [OWNER]
:approve
:approve lease
:deny

:answer OPTION_ID
:answer-text FREEFORM TEXT
:stop

:autopilot authorize CHECKPOINT_ID
:autopilot run CHECKPOINT_ID
```

The terminal does not silently turn a natural-language prompt into an Autopilot
plan. In `autopilot` mode, natural-language submission fails closed until
model-driven plan generation/replanning is separately implemented and
certified. Existing canonical Autopilot checkpoints can be authorized and run
explicitly.

Keyboard controls are `Ctrl-L` Current, `Ctrl-S` Sessions, `Ctrl-O` Issues,
`Ctrl-R` Pull Requests, `Ctrl-G` Gists, `Ctrl-P` command palette, Tab to
cycle views, Escape to clear the input buffer, and `Ctrl-C` to stop the active
run or exit when no run is active. `Ctrl-O` is used for Issues because legacy
terminal protocols encode `Ctrl-I` and Tab as the same byte.

For deterministic CI and non-interactive inspection:

```bash
mucaro tui --snapshot --width 120 --height 40
mucaro agent --snapshot --view sessions
```

The status bar reports configured/active model, execution mode, run/agent state,
provenance-classified context status, and Git branch/HEAD only when a governed
`coding.repo_status` receipt exists. Unknown token or cost telemetry is shown
as unknown; it is never silently estimated.

## Run-scoped permission leases

`mucaro permissions lease-grant` is the bounded equivalent of a session/run
"allow" mode. A lease is always tied to one exact `run_id` and one declared
scope: `RUN_READ_ONLY`, `EXACT_TOOLS`, or `TOOL_PREFIXES`. Mutation and
network authority are independent opt-ins; an optional `--workspace-root`
binds repository actions to one canonical path. Leases also have a TTL and a
maximum-use ceiling.

Grant and revoke commands require interactive confirmation unless `--yes` is
supplied. Grant identity, use reservations, and revocation are hash-chain
audited. Overlapping leases that both authorize the same pending call fail
closed rather than choosing one deterministically. Expired, exhausted, revoked,
tampered, wrong-run, wrong-workspace, or insufficient mutation/network leases
cannot authorize a call.

Example:

```bash
mucaro permissions lease-grant RUN_ID \
  --scope EXACT_TOOLS \
  --tool coding.patch_apply \
  --allow-mutating \
  --workspace-root /path/to/repo \
  --ttl-seconds 900 \
  --max-uses 4 \
  --yes

mucaro permissions lease-list --run-id RUN_ID
mucaro permissions lease-revoke LEASE_ID --yes
```

Supported ingest inputs are TXT, MD, HTML, HTM, DOCX, EML, MSG, CSV, JSON, PDF, PNG,
JPEG, GIF, WEBP, WAV, MP3, M4A, MP4, MOV, and WEBM. HTML files are parsed as
local documents with visible text, title metadata, and link counts. DOCX files
are parsed as local OpenXML documents with paragraph/table text and core
properties. EML files are parsed as local RFC 822/MIME messages with selected
headers, body text, and attachment metadata. Text-like EML attachments are
extracted only for bounded allowlisted types: TXT, MD, HTML, CSV, and JSON.
Other attachments remain listed with skip reasons instead of being silently
ingested. MSG files are parsed with a bounded local binary text probe that can
recover cited text segments from Outlook message files when present, while full
structured Outlook property extraction remains a future parser upgrade. Media
files are metadata-only in this release: they preserve source lineage, byte
counts, dimensions or container facts where available, and parser warnings, but
do not perform semantic image/audio/video understanding.

CSV point conversion is intentionally part of the GIS command family, not
ordinary source ingestion. `mucaro gis csv-points CSV_PATH --lat-column LAT
--lon-column LON` reads UTF-8 CSV headers, rejects duplicate or missing required
columns, records the input SHA-256, preserves raw row values as feature
properties, writes a derived GeoJSON FeatureCollection, and writes a GIS receipt
with source row count, output feature count, skipped row count, CRS, and row
accounting. Invalid rows fail closed by default; `--skip-invalid-rows` must be
selected explicitly and every skipped row is listed in the receipt.

`mucaro gis inspect GEOJSON_PATH` records CRS, source hash, geometry type,
geometry syntax state, bbox, coordinate dimensionality, Z/M presence, and
Shapely planar topology state for each feature. Topology inspection distinguishes
valid planar XY topology, invalid topology, unresolved topology, null/empty
geometry, and topology that is only valid after ignoring Z. M coordinates remain
unresolved because the local Shapely adapter cannot prove M-dimensional topology.
The command does not repair invalid topology or transform geometries.

`mucaro gis repair-topology GEOJSON_PATH` is the separate, explicit mutation
boundary. It uses Shapely `make_valid` only for planar XY geometries, writes
source and output geometry hashes per feature, and preserves
`UNCHANGED_VALID`, `REPAIRED`, `NULL_EMPTY`, `UNRESOLVED`, and
`UNSUPPORTED` as distinct states. Z/M geometries are not flattened; they
remain unresolved. Inspection never invokes repair implicitly.

`mucaro gis filter-bbox GEOJSON_PATH --min-x X --min-y Y --max-x X --max-y Y`
retains whole GeoJSON features whose coordinate extent intersects the query
bbox. Use `--mode within` when a feature's entire bbox must fit inside the
query bbox. The command preserves CRS, writes a derived GeoJSON output, records
per-feature filter states, and closes retained/excluded accounting. It does not
clip or rewrite geometries.

`mucaro gis compare LEFT_PATH RIGHT_PATH` compares GeoJSON features by stable ID
by default, or by feature index with `--match-on index`. The receipt separates
exact coordinate equality, canonical topology equality, vertex Hausdorff
distance, vertex symmetric-difference count, unmatched feature residue, and
coordinate dimensionality. For Polygon and MultiPolygon pairs, it also records
local Shapely planar overlay metrics: left area, right area, intersection area,
and symmetric-difference area in source CRS units. Non-polygon rows are marked
`NOT_APPLICABLE`; Z/M polygon overlay remains unresolved instead of being
flattened.

`mucaro gis overlay LEFT_PATH RIGHT_PATH` applies a Shapely planar overlay
operation to matched Polygon and MultiPolygon feature pairs. The default
operation is `intersection`; `--operation difference`, `--operation union`, and
`--operation symmetric_difference` are also supported. Features match by stable
ID by default, or by index with `--match-on index`; duplicate match keys and CRS
mismatches fail closed. The command preserves both input hashes, writes a
derived GeoJSON output hash, records the overlay adapter, separates emitted
features from empty results, unsupported geometry, Z/M dimensional residue,
unmatched feature keys, invalid topology, and lower-dimensional overlay results,
and closes feature-key and pair-state accounting. This is a narrow local
matched-polygon overlay boundary, not a full GIS-engine overlay certification.

`mucaro gis clip-bbox GEOJSON_PATH --min-x X --min-y Y --max-x X --max-y Y`
clips Point, MultiPoint, LineString, MultiLineString, Polygon, and MultiPolygon
GeoJSON geometries against an axis-aligned bbox with the local Shapely
intersection adapter. The command preserves CRS, writes a derived GeoJSON
output, records per-feature `RETAINED_CLIPPED`, `RETAINED_UNCHANGED`,
`EXCLUDED_OUTSIDE`, `EXCLUDED_NULL_EMPTY`, and `EXCLUDED_UNRESOLVED` states,
records source/output coordinate counts, and closes retained/excluded/clipped
accounting. Z and M dimensions are not flattened; they are recorded as
unresolved residue until a dimension-preserving clipping adapter is added.
Broader clipping/overlay operation support, dimension-preserving mutation, and
independent planar overlay validation remain future GIS work.

`mucaro gis measure GEOJSON_PATH` measures Point, MultiPoint, LineString,
MultiLineString, Polygon, and MultiPolygon features. The default
`--measurement-mode planar` measures in the source coordinate units; use
`--coordinate-units` to label those units. `--measurement-mode geodesic`
requires EPSG:4326 longitude/latitude coordinates and reports area/length in
meters using the local spherical geodesic adapter. The command records
per-feature measurement state, bbox, coordinate count, polygon hole count,
aggregate area, aggregate length, null/invalid/unsupported geometry residue,
adapter identity, measurement mode, units, and row-accounting closure. Planar
polygon area uses local shoelace math with interior rings subtracted. Geodesic
mode remains the compatibility spherical adapter. Use `mucaro gis
measure-ellipsoidal GEOJSON_PATH` for WGS84 `pyproj.Geod` distance, perimeter,
and area receipts. The ellipsoidal command still requires EPSG:4326 and does
classify antimeridian crossings, coordinates at or beyond +/-85 degrees, and
pole contact. Frozen line and polygon controls cover bounded antimeridian and
polar cases; ring-orientation and general large-geometry corpus certification
remain open.

`mucaro gis reproject-crs GEOJSON_PATH --target-crs CRS` is the broader
horizontal CRS adapter. It accepts CRS identifiers parseable by pyproj/PROJ,
forces `always_xy=True`, records the source and target CRS, axis-order policy,
transformer description/definition and definition hash, reported accuracy,
output hash, and per-feature state. The default network policy is
`offline_only`: every available and unavailable PROJ candidate is inventoried,
remote-only grids are skipped, and selected local grids are frozen by byte
count and SHA-256. A lower-accuracy offline candidate therefore produces an
explicit warning rather than silently downloading a grid. Z is passed through
without vertical transformation and is warned; M fails closed. Independently
frozen local datum-grid transformations, broader axis-order coverage, and
vertical or time-dependent CRS certification remain outside the V1 claim.

`inspect-vector`, `inspect-raster`, `sample-raster`,
`clip-raster-bbox`, `vector-raster`, `zonal-stats`, `resample-raster`,
`derive-terrain`, `derive-hillshade`, `derive-d8-flow`, and `validate-corpus` expose the bounded V1
vector/raster plane. GeoPackage inspection is metadata-first; Shapefile
identity includes sidecar manifestations; raster sampling requires coordinates
already in the raster CRS; clipping does not reproject; vector-raster analysis
requires exact declared CRS agreement and uses cell-center inclusion. Zonal
statistics add count, sum, minimum, maximum, mean, median, and population
standard deviation without treating NODATA as zero. Resampling is bounded to
nearest and bilinear methods, writes transactionally, and preserves the source
extent, CRS, band count, and NODATA declaration; it does not claim pixel-level
semantic equivalence. The committed real-corpus manifest freezes a bounded
Census TIGERweb polygon subset and fixed-window USGS earthquake points by raw
bytes, query provenance, retrieval UTC, schema, stable IDs, and geometry
accounting. It also freezes one USGS 3DEP dynamic-service TIFF manifestation,
including the exact export query, response headers, byte identity, embedded
projected CRS, grid, NODATA, and band-value statistics. Terrain derivation
requires explicit metre elevation units and a projected metre CRS, outputs
slope and clockwise-from-north aspect, and emits values only for complete valid
3x3 neighborhoods; flat-cell aspect is NODATA. Hillshade binds that exact
terrain report and raster hash, requires explicit light azimuth and altitude,
and writes float32 values from 0 through 255 so a zero-valued shadow remains
distinct from -9999 NODATA. It is a bounded direct-light visualization model,
not measured radiance or cast-shadow analysis. Depression conditioning requires
the same explicit metre units and projected, axis-aligned grid. Its 8-connected
priority flood treats valid raster-edge and NODATA-adjacent cells as unchanged
outlets, raises enclosed cells only to the lowest reachable spill elevation,
and writes conditioned elevation plus fill depth. The required
`--maximum-fill-depth-m` guard fails before publication when exceeded; the
receipt closes raised/unchanged cells and affine-pixel fill volume. It does not
breach barriers or add epsilon gradients. D8 flow derivation requires the
same explicit metre units and projected, axis-aligned grid. Its four-band
float64 output records an ESRI-compatible candidate-direction bitmask, upstream
cell count including self, affine contributing area in square metres, and an
explicit state code. Only complete valid 3x3 neighborhoods can route. Perimeter
or NODATA-adjacent cells are boundary terminals, and sinks, flats, or tied top
gradients remain terminal `UNRESOLVED` states rather than receiving synthetic
fill or arbitrary direction. The terminal accumulated-cell total must equal the
valid source-cell count. These bounded hydrology derivatives are not watershed
or stream delineation, runoff, flood, groundwater, or hydraulic modeling. The
real corpus, terrain, conditioning, and D8 operations remain bounded and do not
establish broad raster, remote-sensing, hydrology, or bathymetry readiness.
Numeric zero, NODATA, outside, null/empty, and unresolved states remain distinct.

`mucaro gis map-export-manifest` does not render a map. It binds an existing
non-empty artifact to its output hash, extent, CRS, renderer name/version, and
one or more source-layer hashes plus declared CRS. This proves artifact
provenance, not visual correctness.

OCR analysis is intentionally separate from ingestion. `mucaro ocr analyze
IMAGE_PATH` runs a local Tesseract engine against one image, preserves the
source image SHA-256, engine path/version, language, layout mode, raw text,
normalized text, canonical text, per-span confidence state, source-coordinate
bounding boxes, warnings, and a JSON receipt under `data/exports/ocr_reports`.
It is a narrow local image receipt candidate; PDF/page rendering, calibrated
document layout, handwriting, tables/forms, and optional external OCR providers
remain future governed-document-intelligence work.

Use `mucaro capabilities validate-ocr-report REPORT_PATH` to check a saved OCR
receipt against the executable state-of-the-art OCR output contract. The
validator checks report schema, source-image hashes and dimensions, local engine
identity, confidence scale, raw/normalized/canonical text tiers, span bounding
boxes, warning consistency, limitations, falsification criteria, and computed
span/confidence accounting. It returns `CAPABILITY_OUTPUT_CONTRACT_FAILED` when
a report omits source or engine proof, claims text without saved spans, misses a
required no-text or low-confidence warning, or asserts span/confidence counts
that do not match the saved spans.

## Output Contracts

Use `--format json` when automation needs structured output.

Error payloads use:

```json
{
  "status": "ERROR_CODE",
  "detail": "human-readable detail"
}
```

Retrieval and answer failures keep the same top-level shape as successful
retrieval payloads where practical:

```json
{
  "status": "ERROR_NO_SOURCES",
  "query": "What is source lineage?",
  "retrieved_chunks": []
}
```

Successful cited answers include `cited_chunk_ids` plus `cited_chunks` trace
records. Each cited chunk record binds the citation order to the retrieved
chunk ID, source ID, chunk index, retrieval rank, retrieval strategy, score,
matched terms when available, source format when available, and a bounded text
snippet. Text output prints compact `citation=N chunk_id=... source_id=...
rank=...` lines before the answer so operator logs can prove source binding
without opening the JSON export.

`mucaro eval answer-trace` validates the committed answer-trace fixture. The
fixture includes positive cases plus negative cases for uncited answers,
invented citations, missing source bindings, empty snippets, and invalid
retrieval ranks. It also includes generated local-corpus cases that create an
isolated temp source library, ingest Markdown/TXT sources, ask through the
normal mock cited-answer path, and verify the produced citation records,
snippets, required source bindings, and export paths. `mucaro certify
answer-trace-quality` writes the same result as an exact-head
`answer_trace_quality` release receipt.

`mucaro certify operator-workflow` runs an isolated end-to-end local operator
workflow. It proves external-source permission is required before ingestion, a
granted directory can be ingested, retrieval indexes are rebuilt, mock `ask`
produces cited trace exports while using active memory, run history persists,
memory can be disabled/exported/deleted, and local tools cannot execute until
approved and audited.

## Expected Failure States

| State | Meaning |
|---|---|
| `ERROR_INPUT_PATH_NOT_FOUND` | `ingest` path does not exist |
| `ERROR_INPUT_PATH_UNSUPPORTED` | `ingest` path is neither a file nor folder |
| `ERROR_DIRECTORY_PERMISSION_REQUIRED` | direct filesystem ingest needs a prior directory grant |
| `ERROR_PERMISSION_CONFIRMATION_REQUIRED` | a directory grant was requested without `--yes` in a non-interactive terminal |
| `ERROR_DIRECTORY_PERMISSION_TOO_BROAD` | a directory grant targeted the filesystem root |
| `ERROR_NO_SOURCES` | `ask` ran before any source chunks were available |
| `ERROR_NO_RETRIEVAL_CONTEXT` | sources exist but no chunk matches the query |
| `ERROR_NO_VALID_CHUNKS` | available chunks are malformed or empty |
| `ERROR_PERSISTENT_INDEX_MISSING` | `--strategy persistent` was requested before an index was built |
| `ERROR_PERSISTENT_INDEX_STALE` | `--strategy persistent` index does not match current chunks |
| `ERROR_SEMANTIC_STORE_MISSING` | `--strategy semantic` was requested before a semantic store was built |
| `ERROR_SEMANTIC_STORE_STALE` | `--strategy semantic` store does not match current chunks |
| `ERROR_INVALID_TOP_K` | `--top-k` is outside the supported range |
| `TOP_K_RANGE:1-20` | CLI argument validation rejected `ask --top-k` before retrieval ran |
| `TOP_K_MUST_BE_INTEGER` | CLI argument validation rejected a non-integer `ask --top-k` |
| `INDEX_MISSING` | `index status` found no persistent index |
| `INDEX_STALE` | `index status` found an index that no longer matches current chunks |
| `ERROR_SOURCE_NOT_FOUND` | `sources delete` was given an unknown source ID |
| `ERROR_CONVERSATION_NOT_FOUND` | a conversation command was given an unknown conversation ID |
| `ERROR_RUN_NOT_FOUND` | `runs show` was given an unknown run ID |
| `ERROR_MEMORY_NOT_FOUND` | a memory command was given an unknown memory ID |
| `ERROR_MEMORY_KIND_UNSUPPORTED` | memory kind is not one of the supported types |
| `ERROR_MEMORY_CONTENT_REQUIRED` | memory content was empty after normalization |
| `ERROR_MEMORY_CONTENT_TOO_LONG` | memory content exceeded the local limit |
| `ERROR_SECURITY_PROFILE_NOT_READY` | production or public-bind security checks failed |
| `ERROR_TOOL_NOT_ALLOWED` | requested tool is not in the local allowlist |
| `ERROR_TOOL_APPROVAL_REQUIRED` | tool execution was attempted before approval |
| `ERROR_TOOL_CALL_NOT_FOUND` | tool command was given an unknown tool call ID |
| `ERROR_TOOL_CALL_NOT_PENDING` | approval or denial was attempted after the pending state |
| `ERROR_TOOL_CALL_DENIED` | denied tool call cannot be executed |
| `ERROR_TOOL_CALL_ALREADY_EXECUTED` | tool call execution was attempted more than once |
| `DOCTOR_WARN` | readiness check found missing or incomplete local setup |
| `DOCTOR_FAIL` | readiness check found unsupported or invalid local setup |
| `RELEASE_STATE_OPEN` | release-state classifier found open or unverified release states |
| `RELEASE_STATE_READY` | release-state classifier found the governed release state ready |
| `HOSTED_GOVERNANCE_OPEN` | hosted governance is not ready; output includes run IDs, job counts, Actions policy, billing scope, branch-protection state, and support-bundle status |
| `HOSTED_GOVERNANCE_READY` | hosted governance diagnostic, dashboard, and remediation steps are all passing |
| `HOSTED_INCIDENT_PACKET_READY` | hosted governance is open and an escalation packet was built for the current evidence |
| `HOSTED_INCIDENT_PACKET_NOT_REQUIRED` | hosted governance is already ready, so an incident packet was not required |
| `HOSTED_INCIDENT_PACKET_VERIFIED` | saved hosted-governance incident packet passed schema, hash, payload, copy-line, command, and optional current-head checks |
| `HOSTED_INCIDENT_PACKET_VERIFY_FAILED` | saved hosted-governance incident packet is missing, malformed, hash-mismatched, stale for current `HEAD`, or otherwise invalid |
| `MACOS_APP_BUNDLE_READY` | Local source-bound `Mucaro AI.app` bundle was built and verified |
| `MACOS_APP_BUNDLE_FAILED` | Local macOS Application bundle could not be built from the current source/runtime inputs |
| `MACOS_APP_BUNDLE_VERIFIED` | Existing local `Mucaro AI.app` bundle passed structure, executable, receipt, and launcher-target checks |
| `MACOS_APP_BUNDLE_VERIFY_FAILED` | Existing local macOS Application bundle is missing or does not pass required structure/launcher checks |
| `RELEASE_NEXT_ACTIONS_OPEN` | one or more release or hosted-governance actions remain open; output includes prioritized operator commands and verification commands |
| `RELEASE_NEXT_ACTIONS_READY` | no release next actions remain open |
| `RELEASE_NEXT_ACTIONS_VERIFIED` | saved release next-actions runbook passed schema, hash, payload, and requested head-binding checks |
| `RELEASE_NEXT_ACTIONS_VERIFY_FAILED` | saved release next-actions runbook is missing, malformed, hash-mismatched, stale for current `HEAD`, or otherwise invalid |
| `COMPLETION_AUDIT_OPEN` | completion audit found at least one active-goal requirement open or unverified |
| `COMPLETION_AUDIT_READY` | completion audit found every active-goal requirement proven by current evidence |
| `COMPLETION_AUDIT_BLOCKED` | completion audit found a requirement blocked by an explicit external or policy blocker |
| `COMPLETION_AUDIT_FAIL` | completion audit found a hard failure such as invalid current-head release evidence |
| `GOVERNANCE_REPORT_VERIFIED` | saved governance report artifact passed schema, hash, report, and requested head-binding checks |
| `GOVERNANCE_REPORT_VERIFY_FAILED` | saved governance report artifact is missing, malformed, hash-mismatched, stale for current `HEAD`, or otherwise invalid |
| `RELEASE_RECEIPTS_OK` | release receipt listing completed |
| `RELEASE_RECEIPT_OK` | release receipt inspection completed |
| `ERROR_RELEASE_RECEIPT_NOT_FOUND` | requested release receipt does not exist |
| `CERTIFICATION_WARN` | release readiness has warnings such as local-mode auth disabled, missing tag, missing upstream, unavailable Git metadata, or unenforced GitHub governance |
| `CERTIFICATION_FAIL` | release readiness has a hard blocker such as product audit failure, JSON validation failure, or dirty worktree |
| `GITHUB_ACTIONS_DIAGNOSTIC_WARN` | hosted Actions diagnostic found incomplete read-only evidence but no hard failure |
| `GITHUB_ACTIONS_DIAGNOSTIC_FAIL` | hosted Actions diagnostic found a hard failure, such as current-head CI startup failure before jobs |
| `PRODUCT_AUDIT_OPEN` | product audit found remaining governed-product or advanced-capability gaps |
| `PRODUCT_AUDIT_BLOCKED` | product audit found a blocked requirement |
| `ADVANCED_CAPABILITY_CONTRACTS_READY` | GIS, OCR, browsing, coding, and transparency/objectivity proof contracts are structurally complete |
| `ADVANCED_CAPABILITY_CONTRACTS_FAILED` | one or more advanced capability contracts is missing required proof fields |
| `GIS_INSPECT_OK` | GeoJSON inspection completed with no invalid geometry or topology residue |
| `GIS_INSPECT_WARN` | GeoJSON inspection completed while recording invalid/unsupported geometry, invalid topology, unresolved topology, or planar-XY-only topology residue |
| `GIS_SPATIAL_JOIN_OK` | Point-in-polygon classification completed and wrote a spatial receipt |
| `GIS_GEOMETRY_COMPARE_OK` | GeoJSON geometry comparison completed without CRS, topology, overlay, dimensionality, or unmatched-key warnings |
| `GIS_GEOMETRY_COMPARE_WARN` | GeoJSON geometry comparison completed and recorded CRS, topology, overlay, dimensionality, or unmatched-key warnings |
| `GIS_OVERLAY_OK` | Matched polygon overlay completed with derived output hash and closed accounting |
| `GIS_OVERLAY_WARN` | Matched polygon overlay completed while recording unmatched features, empty results, unsupported geometry, topology, dimensionality, or non-polygon result residue |
| `GIS_REPROJECT_OK` | GeoJSON reprojection completed with supported CRS, output hash, and row/coordinate conservation |
| `GIS_REPROJECT_WARN` | GeoJSON reprojection completed while preserving no-op or null/empty geometry warnings |
| `GIS_CSV_POINTS_OK` | CSV point conversion completed with required columns, output GeoJSON hash, and closed row accounting |
| `GIS_CSV_POINTS_WARN` | CSV point conversion completed after explicitly skipped invalid rows were recorded in the receipt |
| `GIS_BBOX_FILTER_OK` | GeoJSON bbox filtering completed with CRS preserved and retained/excluded feature accounting closed |
| `GIS_BBOX_FILTER_WARN` | GeoJSON bbox filtering completed while recording empty output, null/empty, or unresolved exclusions |
| `GIS_CLIP_BBOX_OK` | GeoJSON bbox clipping completed with CRS preserved and clipped/unchanged/excluded feature accounting closed |
| `GIS_CLIP_BBOX_WARN` | GeoJSON bbox clipping completed while recording empty output, null/empty, or unresolved dimensional/topology exclusions |
| `GIS_MEASURE_OK` | GeoJSON planar or geodesic measurement completed with feature accounting closed and no residue warnings |
| `GIS_MEASURE_WARN` | GeoJSON planar or geodesic measurement completed while recording null, invalid, unsupported, coordinate, or malformed polygon-hole residue |
| `GIS_CRS_REPROJECT_OK` | pyproj/PROJ reprojection completed with explicit always-XY policy, frozen transformer identity, output hash, and feature conservation |
| `GIS_CRS_REPROJECT_WARN` | Reprojection completed with a frozen fallback operation or another explicit CRS warning; inspect `best_available`, engine warnings, and report warnings before relying on material accuracy |
| `GIS_TOPOLOGY_REPAIR_OK` | explicit planar XY topology repair completed with before/after geometry hashes and closed accounting |
| `GIS_TOPOLOGY_REPAIR_WARN` | topology repair completed while retaining unresolved or unsupported geometry residue |
| `GIS_ELLIPSOIDAL_OK` | WGS84 ellipsoidal measurement completed with per-feature and aggregate meter-unit receipts |
| `GIS_ELLIPSOIDAL_WARN` | ellipsoidal measurement completed while retaining unresolved feature residue |
| `GIS_VECTOR_FORMAT_OK` | supported vector source manifestation inspection completed |
| `GIS_RASTER_INSPECT_OK` | raster inspection completed with dimension and band accounting |
| `GIS_RASTER_SAMPLE_OK` | raster sampling completed with sampled/NODATA/outside accounting |
| `GIS_RASTER_CLIP_OK` | raster bbox clipping completed with source/output hashes and preserved CRS |
| `GIS_RASTER_RESAMPLE_OK` | raster resampling completed with exact dimensions and preserved extent, CRS, band count, and NODATA declaration |
| `GIS_VECTOR_RASTER_OK` | vector-raster cell-center analysis completed with exact CRS and feature accounting |
| `GIS_VECTOR_RASTER_WARN` | vector-raster analysis completed while retaining unresolved features |
| `GIS_ZONAL_STATS_OK` | cell-center zonal statistics completed with feature/cell accounting and no unresolved features |
| `GIS_ZONAL_STATS_WARN` | zonal statistics completed while retaining unresolved features |
| `GIS_CORPUS_VALIDATION_OK` | frozen authoritative GIS corpus bytes, provenance, schema, IDs, and geometries validated |
| `GIS_MAP_EXPORT_MANIFEST_OK` | map/export artifact provenance was bound to renderer, extent, CRS, and layer hashes |
| `GIS_EVAL_OK` | GIS fixture manifest validation and every configured spatial case passed |
| `GIS_EVAL_FAILED` | GIS fixture validation failed or one or more spatial cases regressed |
| `GIS_QUALITY_CERT_OK` | exact-head GIS fixture-quality certification wrote a release receipt |
| `GIS_QUALITY_CERT_FAILED` | exact-head GIS fixture-quality certification failed or could not write a receipt |
| `CAPABILITY_OUTPUT_CONTRACT_OK` | Saved advanced-capability output passed the executable contract validator |
| `CAPABILITY_OUTPUT_CONTRACT_FAILED` | Saved advanced-capability output is missing required provenance, invariant, warning, limit, falsification, or operation-specific contract fields |
| `OCR_ANALYSIS_OK` | Local image OCR completed with at least one extracted text span and wrote an OCR receipt |
| `OCR_ANALYSIS_NO_TEXT` | Local image OCR completed without extracting text; no text was synthesized |
| `ERROR_GIS_INPUT_NOT_FOUND` | GeoJSON input path does not exist |
| `ERROR_GIS_INVALID_JSON` | GeoJSON input could not be parsed as JSON |
| `ERROR_GIS_UNSUPPORTED_GEOJSON` | GIS command received a JSON object that is not a FeatureCollection |
| `ERROR_GIS_FEATURES_NOT_ARRAY` | GeoJSON FeatureCollection has a malformed `features` member |
| `ERROR_GIS_CRS_MISMATCH` | Point and polygon inputs declared different CRS values and mismatch override was not selected |
| `ERROR_GIS_COMPARE_MATCH_MODE` | GIS comparison received an unsupported feature matching mode |
| `ERROR_GIS_PLANAR_OVERLAY_INVALID_GEOMETRY` | GIS comparison could not load a polygon geometry through the local Shapely overlay adapter |
| `ERROR_GIS_PLANAR_OVERLAY_INVALID_TOPOLOGY` | GIS comparison found invalid polygon topology and recorded unresolved overlay residue instead of repairing it silently |
| `ERROR_GIS_PLANAR_OVERLAY_RUNTIME` | GIS comparison hit an unexpected local Shapely overlay runtime failure and recorded unresolved residue |
| `ERROR_GIS_OVERLAY_OPERATION` | GIS standalone overlay received an unsupported overlay operation |
| `ERROR_GIS_OVERLAY_MATCH_MODE` | GIS standalone overlay received an unsupported feature matching mode |
| `ERROR_GIS_OVERLAY_FEATURES_NOT_OBJECTS` | GIS standalone overlay found a non-object member in the feature array |
| `ERROR_GIS_OVERLAY_CRS_MISMATCH` | GIS standalone overlay inputs declared different CRS values |
| `ERROR_GIS_OVERLAY_DUPLICATE_FEATURE_KEYS` | GIS standalone overlay found duplicate match keys in one input |
| `ERROR_GIS_OVERLAY_INVALID_GEOMETRY` | GIS standalone overlay could not load a polygon geometry through the local Shapely adapter |
| `ERROR_GIS_OVERLAY_INVALID_TOPOLOGY` | GIS standalone overlay found invalid polygon topology and recorded unresolved residue instead of repairing it silently |
| `ERROR_GIS_OVERLAY_RUNTIME` | GIS standalone overlay hit an unexpected local Shapely runtime failure and recorded unresolved residue |
| `ERROR_GIS_INSPECT_TOPOLOGY_INVALID_GEOMETRY` | GeoJSON inspection could not load a geometry through the local Shapely topology adapter |
| `ERROR_GIS_INSPECT_TOPOLOGY_INVALID` | GeoJSON inspection found invalid topology and recorded warning-state residue instead of repairing it silently |
| `ERROR_GIS_REPROJECT_UNSUPPORTED_CRS` | GIS reprojection received a CRS or CRS pair outside the local supported set |
| `ERROR_GIS_REPROJECT_INVALID_COORDINATES` | GIS reprojection found non-finite, malformed, or out-of-domain coordinates |
| `ERROR_GIS_REPROJECT_UNSUPPORTED_GEOMETRY` | GIS reprojection received an unsupported geometry object or type |
| `ERROR_GIS_REPROJECT_FEATURES_NOT_OBJECTS` | GIS reprojection found a non-object member in the feature array |
| `ERROR_GIS_CRS_UNSUPPORTED` | pyproj/PROJ could not parse or construct the requested CRS transformation |
| `ERROR_GIS_CRS_FEATURES` | pyproj/PROJ reprojection found malformed feature rows |
| `ERROR_GIS_CRS_GEOMETRY` | pyproj/PROJ reprojection found an unsupported geometry object |
| `ERROR_GIS_CRS_M_UNSUPPORTED` | pyproj/PROJ reprojection found M coordinates without an explicit semantic transformation policy |
| `ERROR_GIS_CRS_TRANSFORM` | pyproj/PROJ failed while executing the selected transform |
| `ERROR_GIS_REPAIR_FEATURES` | topology repair found malformed feature rows |
| `ERROR_GIS_GEOD_CRS` | ellipsoidal measurement received a source CRS other than EPSG:4326 |
| `ERROR_GIS_GEOD_FEATURES` | ellipsoidal measurement found malformed feature rows |
| `ERROR_GIS_GEOD_GEOMETRY` | ellipsoidal measurement found malformed geometry coordinates |
| `ERROR_GIS_GEOD_UNSUPPORTED` | ellipsoidal measurement received an unsupported geometry type |
| `ERROR_GIS_VECTOR_NOT_FOUND` | vector source path does not exist |
| `ERROR_GIS_VECTOR_FORMAT` | vector source extension is outside the bounded supported set |
| `ERROR_GIS_VECTOR_GEOJSON` | GeoJSON vector manifestation is malformed |
| `ERROR_GIS_VECTOR_GPKG` | GeoPackage metadata could not be read |
| `ERROR_GIS_VECTOR_SHP` | Shapefile header is truncated or invalid |
| `ERROR_GIS_RASTER_ENGINE_UNAVAILABLE` | rasterio is unavailable |
| `ERROR_GIS_RASTER_NOT_FOUND` | raster source path does not exist |
| `ERROR_GIS_RASTER_OPEN` | raster source could not be opened or inspected |
| `ERROR_GIS_RASTER_POINTS` | raster sampling received no points or malformed/non-finite coordinates |
| `ERROR_GIS_RASTER_SAMPLE` | raster sampling failed in the raster engine |
| `ERROR_GIS_RASTER_BBOX` | raster clipping received malformed, non-finite, or inverted bounds |
| `ERROR_GIS_RASTER_CLIP_OUTSIDE` | raster clipping bbox does not intersect the raster extent |
| `ERROR_GIS_RASTER_CLIP_EMPTY` | raster clipping resolved to an empty pixel window |
| `ERROR_GIS_RASTER_CLIP` | raster clipping failed before transactional output publication |
| `ERROR_GIS_RASTER_RESAMPLE_DIMENSIONS` | raster resampling received non-positive or excessive output dimensions |
| `ERROR_GIS_RASTER_RESAMPLE_METHOD` | raster resampling received a method outside nearest or bilinear |
| `ERROR_GIS_RASTER_RESAMPLE_CRS` | raster resampling received a source without a declared CRS |
| `ERROR_GIS_RASTER_RESAMPLE` | raster resampling or output verification failed before a governed receipt was returned |
| `ERROR_GIS_TERRAIN_BAND` | terrain derivation received an invalid one-based elevation band |
| `ERROR_GIS_TERRAIN_CRS` | terrain derivation received a missing, geographic, or unresolvable projected CRS |
| `ERROR_GIS_TERRAIN_UNITS` | terrain derivation did not receive explicit metre vertical units or a projected one-metre horizontal unit |
| `ERROR_GIS_TERRAIN_TRANSFORM` | terrain derivation received a rotated, sheared, or zero-resolution grid |
| `ERROR_GIS_TERRAIN_DIMENSIONS` | terrain derivation received a grid smaller than 3x3 or larger than the bounded cell limit |
| `ERROR_GIS_TERRAIN` | terrain derivation or output verification failed before transactional publication |
| `ERROR_GIS_HILLSHADE_AZIMUTH` | hillshade azimuth is not finite or falls outside `[0,360)` |
| `ERROR_GIS_HILLSHADE_ALTITUDE` | hillshade altitude is not finite or falls outside `(0,90]` |
| `ERROR_GIS_HILLSHADE_TERRAIN` | the intermediate slope/aspect manifestation violates the certified terrain semantics |
| `ERROR_GIS_HILLSHADE_EMPTY` | no complete valid terrain neighborhood exists from which to derive hillshade |
| `ERROR_GIS_HILLSHADE` | hillshade derivation or output verification failed before transactional publication |
| `ERROR_GIS_DEPRESSION_FILL_NOT_FOUND` | depression conditioning could not find the declared elevation raster |
| `ERROR_GIS_DEPRESSION_FILL_ENGINE_UNAVAILABLE` | Rasterio or NumPy is unavailable for depression conditioning |
| `ERROR_GIS_DEPRESSION_FILL_BAND` | depression conditioning received an invalid one-based elevation band |
| `ERROR_GIS_DEPRESSION_FILL_CRS` | depression conditioning received a missing, geographic, or unresolvable projected CRS |
| `ERROR_GIS_DEPRESSION_FILL_UNITS` | depression conditioning did not receive explicit metre vertical units or a projected one-metre horizontal unit |
| `ERROR_GIS_DEPRESSION_FILL_TRANSFORM` | depression conditioning received a rotated, sheared, or zero-resolution grid |
| `ERROR_GIS_DEPRESSION_FILL_DIMENSIONS` | depression conditioning received a grid smaller than 3x3 or larger than 1,000,000 cells |
| `ERROR_GIS_DEPRESSION_FILL_EMPTY` | depression conditioning received no valid elevation cells |
| `ERROR_GIS_DEPRESSION_FILL_OUTLETS` | depression conditioning could not identify a valid raster-edge or NODATA-adjacent outlet |
| `ERROR_GIS_DEPRESSION_FILL_LIMIT` | the configured depth guard is invalid or the required fill exceeds it |
| `ERROR_GIS_DEPRESSION_FILL_GRAPH` | the priority-flood traversal did not classify every valid source cell |
| `ERROR_GIS_DEPRESSION_FILL` | depression conditioning or output verification failed before transactional publication |
| `ERROR_GIS_D8_NOT_FOUND` | D8 derivation could not find the declared elevation raster |
| `ERROR_GIS_D8_ENGINE_UNAVAILABLE` | Rasterio or NumPy is unavailable for D8 derivation |
| `ERROR_GIS_D8_BAND` | D8 derivation received an invalid one-based elevation band |
| `ERROR_GIS_D8_CRS` | D8 derivation received a missing, geographic, or unresolvable projected CRS |
| `ERROR_GIS_D8_UNITS` | D8 derivation did not receive explicit metre vertical units or a projected one-metre horizontal unit |
| `ERROR_GIS_D8_TRANSFORM` | D8 derivation received a rotated, sheared, or zero-resolution grid |
| `ERROR_GIS_D8_DIMENSIONS` | D8 derivation received a grid smaller than 3x3 or larger than 1,000,000 cells |
| `ERROR_GIS_D8_TOLERANCE` | D8 derivation received a negative or non-finite elevation/gradient tolerance |
| `ERROR_GIS_D8_EMPTY` | D8 derivation received no valid elevation cells |
| `ERROR_GIS_D8_NO_COMPLETE_NEIGHBORHOOD` | no complete valid 3x3 neighborhood exists for bounded D8 routing |
| `ERROR_GIS_D8_GRAPH` | a routed D8 edge violates strict descent beyond the declared tolerance |
| `ERROR_GIS_D8` | D8 derivation or output verification failed before transactional publication |
| `ERROR_GIS_VECTOR_RASTER_NOT_FOUND` | vector-raster analysis could not find both inputs |
| `ERROR_GIS_VECTOR_RASTER_VECTOR` | vector-raster analysis received malformed GeoJSON |
| `ERROR_GIS_VECTOR_RASTER_RASTER` | vector-raster analysis could not open the raster |
| `ERROR_GIS_VECTOR_RASTER_CRS_MISMATCH` | vector and raster CRS are absent or do not match exactly |
| `ERROR_GIS_VECTOR_RASTER_BAND` | requested raster band is outside the available one-based range |
| `ERROR_GIS_CORPUS_MANIFEST_NOT_FOUND` | GIS corpus manifest path does not exist |
| `ERROR_GIS_CORPUS_MANIFEST` | GIS corpus manifest is malformed or violates the supported schema |
| `ERROR_GIS_CORPUS_PATH` | GIS corpus asset path escapes the configured project root |
| `ERROR_GIS_CORPUS_ASSET_NOT_FOUND` | GIS corpus asset is absent |
| `ERROR_GIS_CORPUS_HASH` | GIS corpus asset bytes or SHA-256 differ from the frozen manifest |
| `ERROR_GIS_CORPUS_ASSET_TYPE` | GIS corpus asset type is absent or outside the explicit GeoJSON/raster registry |
| `ERROR_GIS_CORPUS_GEOJSON` | GIS corpus asset is not a GeoJSON FeatureCollection |
| `ERROR_GIS_CORPUS_SOURCE_RESPONSE` | GIS corpus asset contains a source-service error response |
| `ERROR_GIS_CORPUS_COUNT` | GIS corpus feature count differs from the frozen manifest |
| `ERROR_GIS_CORPUS_ID` | GIS corpus feature lacks its declared stable ID |
| `ERROR_GIS_CORPUS_DUPLICATE_ID` | GIS corpus stable IDs are not unique |
| `ERROR_GIS_CORPUS_SCHEMA` | GIS corpus feature lacks required source properties |
| `ERROR_GIS_CORPUS_FEATURE` | GIS corpus contains a malformed feature row |
| `ERROR_GIS_CORPUS_GEOMETRY` | GIS corpus geometry is malformed, empty, or invalid |
| `ERROR_GIS_CORPUS_GEOMETRY_COUNTS` | GIS corpus geometry-type accounting differs from the frozen manifest |
| `ERROR_GIS_CORPUS_DIMENSIONS` | GIS corpus XY/XYZ/XYZM feature accounting differs from the frozen manifest |
| `ERROR_GIS_CORPUS_RASTER_ENGINE` | rasterio is unavailable for raster corpus validation |
| `ERROR_GIS_CORPUS_RASTER` | GIS corpus raster could not be opened or inspected |
| `ERROR_GIS_CORPUS_RASTER_METADATA` | GIS corpus raster driver, grid, transform, bounds, dtype, or NODATA differs from the frozen manifest |
| `ERROR_GIS_CORPUS_RASTER_CRS` | GIS corpus raster embedded CRS differs from the frozen declaration |
| `ERROR_GIS_CORPUS_RASTER_BANDS` | GIS corpus raster per-band value accounting or statistics differ from the frozen manifest |
| `ERROR_GIS_CORPUS_RASTER_UNITS` | GIS corpus elevation raster lacks frozen metre units or authoritative unit evidence |
| `ERROR_GIS_CORPUS_PROVENANCE` | GIS corpus authority, URL, retrieval UTC, or declared CRS evidence is absent |
| `ERROR_GIS_EXPORT_NOT_FOUND` | map/export artifact path does not exist |
| `ERROR_GIS_EXPORT_EMPTY` | map/export artifact exists but contains no bytes |
| `ERROR_GIS_EXPORT_EXTENT` | map/export extent is malformed, non-finite, inverted, or zero-area |
| `ERROR_GIS_EXPORT_IDENTITY` | output CRS or renderer identity/version is absent |
| `ERROR_GIS_EXPORT_LAYER` | bound layer metadata or source path is malformed |
| `ERROR_GIS_EXPORT_LAYER_CRS` | a bound source layer is missing its declared CRS |
| `ERROR_GIS_CSV_DELIMITER_INVALID` | CSV point conversion received a delimiter that is not exactly one character |
| `ERROR_GIS_CSV_CRS_INVALID` | CSV point conversion received an empty CRS value |
| `ERROR_GIS_CSV_ENCODING` | CSV point conversion received bytes that are not valid UTF-8 |
| `ERROR_GIS_CSV_INVALID` | CSV point conversion could not parse the CSV structure |
| `ERROR_GIS_CSV_HEADER_MISSING` | CSV point conversion received a file with no header row |
| `ERROR_GIS_CSV_DUPLICATE_HEADERS` | CSV point conversion found duplicate header names |
| `ERROR_GIS_CSV_REQUIRED_COLUMN_MISSING` | CSV point conversion was missing the requested latitude, longitude, or ID column |
| `ERROR_GIS_CSV_INVALID_COORDINATES` | CSV point conversion found malformed, non-finite, or out-of-range EPSG:4326 coordinates |
| `ERROR_GIS_CSV_NO_VALID_POINTS` | CSV point conversion skipped or rejected every row and produced no features |
| `ERROR_GIS_BBOX_INVALID` | GeoJSON bbox filtering received non-finite coordinates or inverted min/max extents |
| `ERROR_GIS_BBOX_FILTER_MODE` | GeoJSON bbox filtering received an unsupported mode |
| `ERROR_GIS_BBOX_FEATURES_NOT_OBJECTS` | GeoJSON bbox filtering found a non-object member in the feature array |
| `ERROR_GIS_CLIP_FEATURES_NOT_OBJECTS` | GeoJSON bbox clipping found a non-object member in the feature array |
| `ERROR_GIS_CLIP_INVALID_GEOMETRY` | GeoJSON bbox clipping could not load a geometry through the local Shapely adapter |
| `ERROR_GIS_CLIP_INVALID_TOPOLOGY` | GeoJSON bbox clipping found invalid topology and recorded unresolved residue instead of repairing it silently |
| `ERROR_GIS_MEASURE_FEATURES_NOT_OBJECTS` | GeoJSON measurement found a non-object member in the feature array |
| `ERROR_GIS_MEASURE_UNITS_REQUIRED` | GeoJSON measurement received an empty coordinate-units label |
| `ERROR_GIS_MEASURE_MODE_UNSUPPORTED` | GeoJSON measurement received an unsupported measurement mode |
| `ERROR_GIS_MEASURE_GEODESIC_CRS` | Geodesic measurement was requested for a source CRS other than EPSG:4326 |
| `ERROR_GIS_MEASURE_INVALID_COORDINATES` | Geodesic measurement found malformed, non-finite, or out-of-range longitude/latitude coordinates |
| `ERROR_OCR_INPUT_NOT_FOUND` | OCR input path does not exist |
| `ERROR_OCR_UNSUPPORTED_INPUT` | OCR command received an unsupported input type; current OCR analysis supports local images only |
| `ERROR_OCR_ENGINE_UNAVAILABLE` | local Tesseract executable was not found and no explicit `--tesseract-path` was supplied |
| `ERROR_OCR_LANGUAGE_INVALID` | OCR language code was empty or had surrounding whitespace |
| `ERROR_OCR_LAYOUT_INVALID` | OCR layout mode was not one of the supported adapter modes |
| `ERROR_OCR_TIMEOUT` | OCR engine exceeded the configured timeout |
| `ERROR_OCR_ENGINE_FAILED` | OCR engine exited with a non-zero status |
| `ERROR_INVALID_RETRIEVAL_STRATEGY` | retrieval strategy is outside the supported set |
| `PORT_RANGE:1-65535` | `serve --port` is outside the TCP port range |
| `CITATION_ERROR` | answer trace failed citation validation |
| `ERROR_CITATION_TRACE_MISSING` | answer cited chunk IDs but did not include expandable citation trace records |
| `ERROR_CITED_CHUNK_NOT_TRACED` | one or more cited chunk IDs were retrieved but missing from `cited_chunks` |
| `ANSWER_TRACE_EVAL_FAILED` | answer-trace fixture validation failed, thresholds failed, or no negative cases were present |
| `ANSWER_TRACE_QUALITY_CERT_FAILED` | exact-head cited-answer traceability certification failed |

## Examples

```bash
mucaro ingest data/input --format text
mucaro --version
mucaro doctor
mucaro release state --include-github --format text
mucaro release dashboard --include-github --format text
mucaro release hosted-governance --format text
mucaro release incident-packet --save --format text
mucaro release incident-packet --verify data/exports/hosted_incident_packets/incident-packet-HEADPREFIX.json --require-current-head --format text
mucaro release next-actions --format text
mucaro release handoff --format text
mucaro release next-actions --save --format text
mucaro release next-actions --verify data/exports/release_next_actions/next-actions-HEADPREFIX.json --require-current-head --format text
mucaro release support-bundle --save --format text
mucaro release support-bundle --verify data/exports/support_bundles/support-bundle-HEADPREFIX.json --require-current-head --format text
mucaro release governance-report --save --format text
mucaro release governance-report --verify data/exports/governance_reports/governance-report-HEADPREFIX.json --require-current-head --format text
mucaro release receipts --current-head --format text
mucaro release receipt local_release-HEADPREFIX --format json
mucaro audit completion --include-github --format text
mucaro certify status --format text
mucaro certify status --include-github --format text
mucaro audit product --format text
mucaro eval retrieval --format text
mucaro eval answer-trace --format text
mucaro eval gis --format text
mucaro gis inspect data/input/example.geojson --format text
mucaro gis point-in-polygon data/input/points.geojson data/input/polygons.geojson --format text
mucaro gis reproject-crs data/input/example.geojson --target-crs EPSG:32161 --format text
mucaro gis repair-topology data/input/example.geojson --format text
mucaro gis measure-ellipsoidal data/input/example.geojson --format text
mucaro gis inspect-vector data/input/example.gpkg --format text
mucaro gis inspect-raster data/input/elevation.tif --format text
mucaro gis sample-raster data/input/elevation.tif --point -66.1 18.4 --format text
mucaro gis clip-raster-bbox data/input/elevation.tif --min-x -67 --min-y 17 --max-x -65 --max-y 19 --format text
mucaro gis vector-raster data/input/zones.geojson data/input/elevation.tif --band 1 --format text
mucaro gis zonal-stats data/input/zones.geojson data/input/elevation.tif --band 1 --format text
mucaro gis resample-raster data/input/elevation.tif --width 1024 --height 1024 --method bilinear --format text
mucaro gis derive-terrain data/input/elevation-utm.tif --band 1 --elevation-units meters --format text
mucaro gis derive-hillshade data/input/elevation-utm.tif --band 1 --elevation-units meters --azimuth 315 --altitude 45 --format text
mucaro gis derive-d8-flow data/input/elevation-utm.tif --band 1 --elevation-units meters --elevation-tolerance-m 0 --gradient-tie-tolerance 1e-12 --format text
mucaro gis validate-corpus _architecture/gis_real_corpus_manifest.json --format text
mucaro gis map-export-manifest data/exports/map.png --layer data/input/zones.geojson EPSG:4326 --extent -67 17 -65 19 --output-crs EPSG:4326 --renderer qgis --renderer-version 3.40 --format text
mucaro certify retrieval-quality --format text
mucaro certify answer-trace-quality --format text
mucaro certify gis-quality --format text
mucaro certify operator-workflow --format text
mucaro permissions grant ~/Documents/mucaro-sources --yes --format json
mucaro permissions list --format text
mucaro ingest ~/Documents/mucaro-sources --format json
mucaro sources list --format text
mucaro index status --format json
mucaro sources reindex --format text
mucaro conversations create --title "Source lineage" --format json
mucaro conversations list --format text
mucaro conversations ask CONVERSATION_ID "How does source lineage work?" --strategy persistent --format json
mucaro runs summary --format text
mucaro runs show RUN_ID --format json
mucaro models list --format text
mucaro models status --format json
mucaro models status anthropic --format text
mucaro memory remember "Prefer concise technical answers" --kind preference --format json
mucaro memory list --format text
mucaro memory disable MEMORY_ID --format json
mucaro memory export --format json
mucaro security status --host 127.0.0.1 --format text
mucaro package status --format text
mucaro package build --format text
mucaro package verify data/exports/packages/MANIFEST.json --require-current-head --format text
python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json
python3 scripts/certify_python_distribution.py
mucaro tools list --format text
mucaro tools request source_library.status --reason "readiness check" --format json
mucaro tools request web.fetch --arguments '{"url":"https://example.com","max_bytes":1000000}' --reason "source snapshot" --format json
mucaro tools approve TOOL_CALL_ID --format json
mucaro tools run TOOL_CALL_ID --format json
mucaro sources ingest-web-snapshot data/exports/web_snapshots/WEBSNAP.json --format text
mucaro tools audit --format text
mucaro capabilities contracts --format text
mucaro ask "How does source lineage work?" --format json --no-persist
mucaro ask "How does source lineage work?" --strategy persistent --format text
mucaro ask "How does source lineage work?" --strategy semantic --format text
mucaro plan "Add CLI capability" --input mucaro-ai --format text
mucaro validate --format text
mucaro serve --host 127.0.0.1 --port 8000
```

When `mucaro audit completion --include-github` reports hosted governance as
OPEN, its hosted-governance row includes the support classification,
current-head startup run IDs, check-suite counts, and suspected startup causes
from the GitHub Actions diagnostic.

Use `mucaro release incident-packet --save --format text` when hosted
governance remains open after local release and handoff artifacts are verified.
The command builds a compact escalation artifact under
`data/exports/hosted_incident_packets` with the hosted-governance report,
completion-audit hosted row, current-head startup run IDs, suspected startup
causes, open remediation steps, diagnostic commands, and support-ready copy
lines. Use `--verify PATH`, `--expected-sha256 SHA256`, and
`--require-current-head` to validate a saved packet without rerunning live
GitHub diagnostics.

## Directory Permissions

Project-local paths under the repository root are accessible by default. Direct
filesystem paths outside the project root require an explicit local grant:

```bash
mucaro permissions grant ~/Documents/mucaro-sources --yes
mucaro permissions check ~/Documents/mucaro-sources/source.md
mucaro ingest ~/Documents/mucaro-sources
mucaro permissions revoke ~/Documents/mucaro-sources
```

The ledger is stored under `data/processed/directory_permissions.json`, which is
local runtime state and should not be committed. Interactive operators can use
`mucaro ingest PATH --grant-directory-access` to receive a terminal prompt when
the path needs a new grant.

## Diagnostics

Use `mucaro doctor` to inspect local readiness without ingesting files or
granting access. The report checks project paths, local data directories,
source/chunk counts, persistent index status, semantic-store status, directory
grants, model-provider readiness, API auth posture, conversations, runs,
explicit memory, production-security posture, and the local tool runtime. Add `--strict` when
automation should fail on warnings.

Use `mucaro models list` to inspect the provider registry without making live
model calls. Use `mucaro models status PROVIDER` to diagnose a single provider's
live-mode gate, credential environment variable, optional dependency, default
model, readiness status, and warnings. The mock provider is always local and
ready; live providers remain disabled or not ready until their explicit gates
are satisfied.

Use `mucaro release state` to classify the current repo against the bounded
release path: `CANONICAL_LOCAL`, `LOCAL_RELEASE_CERTIFIED`,
`FRESH_CLONE_CERTIFIED`, `SOURCE_ARTIFACT_PACKAGING`,
`SOURCE_ARTIFACT_CERTIFIED`, `PYTHON_DIST_CERTIFIED`,
`RETRIEVAL_QUALITY_CERTIFIED`, `GIS_QUALITY_CERTIFIED`, `OPERATOR_WORKFLOW_CERTIFIED`,
`GITHUB_GOVERNANCE_OPEN`, `GOVERNED_RELEASE_READY`, and
`PRODUCTION_READY`.
The command reuses existing audit, package, Git,
artifact, Python distribution, retrieval-quality, operator-workflow, and
optional GitHub governance receipts. It does not run heavy certification scripts, so those
states remain `UNVERIFIED` until the operator runs the reported commands and
creates matching exact-HEAD receipts under `data/exports/release_receipts`. Add
`--include-github` when authenticated `gh` evidence is available. The GitHub
governance row reports all non-passing GitHub release gates, including
hosted-runner startup classification when current-head CI fails before jobs are
created. When source-artifact evidence is present but retrieval-quality evidence
is not, the current state is `RETRIEVAL_QUALITY_CERTIFICATION_OPEN`.

Use `mucaro release dashboard` for a single read-only operator summary of the
current release lane. It verifies the saved distribution handoff manifest and
governance report for the current Git `HEAD`, summarizes exact-head receipts,
checks worktree, upstream, tag, and `origin/BRANCH` binding, and reports the
current blockers. Add `--include-github` to include live GitHub Actions
diagnostics; omit it for a local/offline dashboard over saved artifacts. Use
`--strict` when automation should fail until `RELEASE_DASHBOARD_READY`.

Use `mucaro release next-actions` when the operator needs the shortest
current-head runbook instead of the full dashboard. It composes the
hosted-governance report, prioritizes non-passing steps, prints current run
IDs and URLs, handoff artifact paths, support-copy lines, and verification
commands, and promotes local release evidence refresh above hosted remediation
when the release dashboard is failing. The saved runbook includes a structured
`support_copy` payload with summary fields, run handles, artifact paths,
verification commands, open actions, and line-oriented text suitable for a
support ticket or issue handoff. When GitHub reports current-head push runs
that failed before workflow jobs were created but the named workflow handles are
stale, the runbook reports `current_head_push_startup` as a separate action and
includes `current_head_startup_run_ids` in the support-copy payload. Add
`--save` to write the runbook under
`data/exports/release_next_actions`, pass `--output PATH` to choose the saved
artifact path, or use `--verify PATH` to validate a saved runbook without
rerunning live GitHub diagnostics. Add `--expected-sha256 SHA256` to pin the
runbook bytes and `--require-current-head` when the runbook must bind to the
current checkout. Verification requires the current runbook schema, including
the support-copy payload, so older saved runbooks should be regenerated for the
current `HEAD`.

Use `mucaro release handoff --format text` after current-head local
certification receipts exist and the operator needs a single restartable release
handoff path. The command saves and verifies the distribution manifest, ensures
the current-head governance report, saves and verifies the support bundle, saves
and verifies the next-actions runbook, and prints the exact verification
commands for all four artifacts. The command returns `RELEASE_HANDOFF_OPEN` when
the local handoff artifacts verify but hosted governance is still open; add
`--strict` when that open hosted state should produce exit code 2. Add
`--refresh-certification` when the command should first rerun current-head local
release, Python distribution, source artifact, and fresh-clone certifiers before
writing handoff artifacts. Use repeated `--refresh-step` values to run a
canonical subset, for example `--refresh-step python_dist --refresh-step
fresh_clone`; selecting a step implies `--refresh-certification`. The allowed
steps are `local_release`, `python_dist`, `source_artifact`, and `fresh_clone`,
and selected steps always run in that release order. Add
`--refresh-timeout-seconds N` to cap each certifier run, where `0` disables the
timeout. Each refresh attempt writes a JSON log under
`data/exports/release_refresh_logs` with the current head prefix and a UTC
attempt stamp; if that refresh fails or times out, the command stops before
writing the handoff artifact set. Refresh reports include per-step
`elapsed_seconds`, total refresh elapsed time, and a `slowest_steps` summary so
operators can identify expensive certifiers without parsing child-script logs.

Use `mucaro release support-bundle --save` when hosted governance is open and
the operator needs a single support-ready evidence artifact. The command runs
the release dashboard with live GitHub diagnostics, records the support
classification, startup blocker, dashboard payload, blockers, reproduction
commands, and next actions, then writes a JSON bundle under
`data/exports/support_bundles`. Before saving the bundle, it verifies the
matching current-head governance report under `data/exports/governance_reports`
and creates it when it is missing or stale, so a support bundle can be generated
directly without first running a separate governance-report save command. Use
`--verify PATH` to validate a saved support bundle without rerunning live GitHub
diagnostics. Add `--expected-sha256 SHA256` to pin the bundle bytes and
`--require-current-head` when the bundle must bind to the current checkout.

Use `mucaro release governance-report` to build a single current-head handoff
payload for governed release work. The report combines release-state
classification, product-audit status, package status, current-head release
receipt summaries, GitHub Actions diagnostics, startup-blocker causes, hosted
run evidence, and exact next actions. Add `--save` to write the JSON artifact
under `data/exports/governance_reports`, or pass `--output PATH` to choose the
artifact path. The saved artifact records a schema version, creation time, and
the report payload; the CLI response includes the artifact path, SHA-256, and
size. Use `--verify PATH` to validate a saved artifact without rebuilding live
GitHub evidence. Verification checks file existence, JSON object shape, schema
version, timestamp, embedded report status, report `head_sha`, receipt summary,
startup-blocker payload, and optional file SHA-256 via `--expected-sha256`.
Add `--require-current-head` when the artifact must bind to the current Git
`HEAD`; omit it when inspecting older archived handoff evidence. Use `--strict`
when automation should fail until `GOVERNANCE_REPORT_READY`.

Use `mucaro release receipts` to list local release and fresh-clone
certification receipts. Add `--current-head` to filter the list to receipts
whose `head_sha` matches the current Git `HEAD`. Each row includes receipt
validity, receipt type, certification status, bound head SHA, and receipt file
SHA-256. The listing also summarizes valid/invalid counts by receipt type and
status. Use `mucaro release receipt RECEIPT_ID` to inspect the full JSON receipt
by file stem or filename.

Use `mucaro certify status` before tagging or pushing release candidates. It is
a read-only readiness receipt over product audit, doctor, JSON validation, Git
HEAD, worktree cleanliness, tag-at-HEAD, and upstream configuration. Add
`--strict` when automation should fail on warnings, then run the reported
`scripts/certify_release.py` and `scripts/certify_fresh_clone.py` commands for
the heavier gates. Add `--include-github` when network access and authenticated
`gh` are available to include branch protection, latest hosted CI, and remote
tag-binding checks. GitHub governance checks are read-only and never mutate
repository settings. The default hosted workflow name is `CI`; use
`--github-workflow NAME` with `--include-github` if the release workflow is
renamed. A completed hosted CI run for the current HEAD must conclude
successfully; an unsuccessful completed run is a failed certification gate. If
the current-head run reports `startup_failure` with `jobs_count=0`, the JSON
payload marks `github.latest_ci.startup_layer` as `before_jobs` and adds a
`github_hosted_runner_startup` failure gate. That gate is diagnostic evidence
for GitHub Actions billing, spending-limit, runner-availability, or account
policy checks; it does not replace the required `github_latest_ci` pass.

Use `mucaro certify github-actions` when the GitHub governance gate fails and
the operator needs a narrower hosted-runner diagnostic receipt. The command
checks repository Actions permissions, workflow token permissions, private
branch-protection API availability, self-hosted runner inventory, workflow
registration, the latest named release workflow, the optional startup probe
workflow, job counts, and account billing endpoint access. It is read-only and
returns `GITHUB_ACTIONS_DIAGNOSTIC_FAIL` when hosted startup is isolated outside
the project build/test stack. Text output includes current CI and startup-probe
run IDs, job counts, head SHAs, and run URLs so account-policy or GitHub-support
follow-up can reference exact failed runs. It also summarizes commit check
suites, including check-suite IDs, app slugs, conclusions, and check-run counts,
so zero-job workflow failures can be cross-checked against zero-check-run GitHub
Actions suites. If private branch protection is plan-gated, the diagnostic
reports a `branch_protection_api` warning separately from hosted CI startup so
governance work can distinguish repository policy from build failures.
The JSON output also includes a `startup_blocker` object with
`isolated_before_jobs`, `suspected_causes`, and `next_actions` fields plus a
compact `support_summary` object with run handles, check-suite counts, branch
protection availability, billing visibility, suspected causes, and next actions.
Governance reports copy that object to `github_support_summary` so the saved
handoff artifact has a single support-ready block.

Use `mucaro audit product` to classify the broader governed assistant objective
as requirement rows with `PASS`, `OPEN`, or `BLOCKED` status. The audit includes
assistant capability rows plus release-governance rows, so an otherwise working
local build still reports `PRODUCT_AUDIT_OPEN` while hosted CI, branch
protection, or governed tag binding remains unresolved. Add `--strict` when
automation should fail while any product gap remains open.

Use `mucaro audit roadmap` to group those same product-audit rows into
development stages. The roadmap preserves the audit denominator and adds
stage-level status for the canonical operator surface, permissioned source
grounding, controlled assistant behavior, release packaging certification, and
hosted release governance, plus a final operator GUI stage that remains open
until backend and CLI evidence can support it. Text output includes the next
recommended stage, next recommended vector, per-stage counts, open requirement
IDs, gaps, and next steps so a development cycle can continue from the
supported CLI boundary without pulling GUI work forward prematurely.

Use `mucaro capabilities validate-gis-report REPORT_PATH` to check a saved GIS
receipt against the executable advanced-capability output contract. The
validator checks report schema, status, operation, source/input provenance,
source hashes, CRS where present, warning structure, asserted row/conservation
invariants, and operation-specific fields for GeoJSON inspection topology,
geometry comparison, bbox filtering, bbox clipping, and planar/geodesic measurement reports. It returns
`CAPABILITY_OUTPUT_CONTRACT_FAILED` when a report claims a
warning status without warnings, asserts false accounting, omits hashes, or
misses required inspect, compare, measurement, or bbox fields. This is an enforcement bridge between
the descriptive capability contract and concrete GIS receipts; broader GIS
operation-specific checks plus web, retrieval, and coding validators remain
future work.

Use `mucaro eval retrieval` to run the committed retrieval-quality fixture from
the supported CLI surface. The command reports the fixture path, chunk/query
counts, positive/no-context query counts, expected binding counts, explicit
distractor binding counts, source-format counts, fixture validation checks,
evaluated strategy count, recall thresholds, per-strategy `recall@k`,
expected-binding recall, context precision, top-1 hit rate, full-binding hit
rate, multi-binding full-hit rate, distractor-free rate, no-context accuracy, and
per-category quality slices for each strategy. The category slices preserve the
fixture's query categories so broad aggregate scores cannot hide a weak
retrieval area. The `--min-no-context-accuracy`, `--min-expected-binding-recall`, and
`--min-multi-binding-full-hit` thresholds apply across evaluated strategies so
unrelated context, partial multi-source retrieval, and missing expected
bindings fail the gate instead of passing as weak context. Use
`--min-distractor-free-rate` to fail cases where an explicitly marked
wrong-but-related distractor chunk outranks the expected binding at top-1.
Hybrid and semantic retrieval also have dedicated precision thresholds. Use
`--min-category-recall` and `--min-category-binding-recall` to promote
per-category positive-query metrics into hard gates once the category
expectations are release-ready. Use `--min-source-formats` to require a minimum
number of distinct `source_format` values in the fixture, which is useful when
mixed-format coverage itself is part of the release claim. It returns
`RETRIEVAL_EVAL_INVALID` for malformed benchmark fixtures and
`RETRIEVAL_EVAL_FAILED` when configured thresholds are not met, so release
automation can fail on invalid or regressed retrieval evidence.

Use `mucaro eval gis` to run the committed GIS fixture manifest from the
supported CLI surface. The command validates the manifest shape, required case
count, operation diversity, negative-case coverage, and then materializes
temporary GeoJSON and CSV inputs to exercise the same public GIS functions used by
operators. Current cases cover coordinate dimensionality, invalid topology
inspection without repair, point-in-polygon
holes and join multiplication, geometry comparison with rotated polygon rings,
geometry comparison with planar overlay area differences, limited EPSG:4326 to
EPSG:3857 reprojection, CSV point conversion with explicit
invalid-row skipping, bbox filtering with null-residue accounting, fail-closed
invalid bbox extents, bbox clipping with null and dimensional residue
accounting, fail-closed invalid Web Mercator latitude handling, offline-only
PROJ candidate/grid selection, an exact-source-bound public-domain NOAA Puerto
Rico datum grid with manifest/hash/byte/license provenance, path-escape and
tamper rejection, and no runtime download, explicit EPSG axis-order controls, bounded WGS84
antimeridian and polar line/polygon controls, and unresolved invalid
longitude/latitude residue, a frozen real USGS 3DEP raster manifestation,
bounded projected-metre slope/aspect controls, terrain-lineage-bound hillshade,
bounded priority-flood depression conditioning with explicit depth and volume
accounting, and bounded D8 flow direction/accumulation with explicit terminal
ambiguity. The current committed denominator is 64 cases across 25 operations,
including 26 fail-closed negative cases.
It returns `GIS_EVAL_OK` only when the fixture and every case pass;
`GIS_EVAL_FAILED` keeps broader GIS maturity open and gives automation a hard
regression signal.

Use `mucaro certify gis-quality` when the release needs exact-head GIS fixture
evidence instead of an ad hoc eval result. The certification wraps
`mucaro eval gis`, records the current `HEAD`, profile, fixture, full eval
report, and failed cases, then writes a `gis_quality` receipt under
`data/exports/release_receipts`. It returns `GIS_QUALITY_CERT_OK` only when the
committed GIS fixture manifest and every spatial case pass. The receipt proves
the current narrow GIS regression boundary; it does not close the broader
advanced GIS product gap.

Use `mucaro certify retrieval-quality` when the release needs exact-head
retrieval evidence instead of an ad hoc score. The certification profile runs
the default fixture with strict category binding recall and the larger fixture
with calibrated multi-binding category recall. It also runs a mixed-format
fixture with strict category binding recall across TXT, Markdown, HTML, DOCX,
EML, MSG text probing, CSV, JSON, PDF, metadata-only media, traceability, and multi-format
queries, including explicit decoy chunks for distractor top-1 avoidance. The
mixed-format case also requires the full committed 13-format fixture spread via
`min_source_formats=13`, so format coverage cannot silently narrow while scores
stay green. It writes a
`retrieval_quality` receipt under `data/exports/release_receipts`, records the
profile, exact `HEAD`, thresholds, fixture counts, category threshold failures,
and full eval reports, and returns `RETRIEVAL_QUALITY_CERT_OK` only when every
case passes. `mucaro release state` consumes that receipt as
`RETRIEVAL_QUALITY_CERTIFIED`.

Use `mucaro package status` to inspect packaging readiness without writing
artifacts. The command checks required package inputs, Python/frontend package
metadata, version consistency, the installed CLI entrypoint, desktop launcher
entrypoint, desktop package manifest readiness, optional generated outputs, and
known distribution blockers. It reports `PACKAGE_FAIL` for broken required
inputs, `PACKAGE_WARN` for incomplete distribution readiness, and
`PACKAGE_READY` only when no packaging warnings remain.

Use `mucaro package app-store-audit --format text` to validate the tracked Mac
App Store security profile, least-privilege entitlement template, privacy
manifest template, candidate/scan JSON Schemas, bundle-path policy, explicit
nonclaims, and Apple-controlled
external gates. Add `--app /path/to/Mucaro\ AI.app` on macOS to inspect one
candidate's bundle metadata, containment, App Transport Security posture,
self-contained candidate receipt, code signature, exact entitlements, and
embedded provisioning profile. The candidate must also contain a CycloneDX
SBOM and fresh vulnerability and malware receipts whose hashes and build
identity match its signed receipt and executable. A passing local candidate is
reported only as
`MAC_APP_STORE_CANDIDATE_PROVISIONAL`; it is not App Store Connect processing
or Apple App Review evidence. `--strict` intentionally exits `2` until those
external gates are certified for the exact submitted build.

Use `mucaro package build` to create the current governed release artifact: a
tracked-source ZIP plus JSON manifest under `data/exports/packages`. The command
refuses dirty worktrees by default, records the exact Git `HEAD`, and includes
SHA-256 receipts for the archive and every tracked source member. Use
`--allow-dirty` only for diagnostics, not governed release tags.

Use `mucaro package verify MANIFEST` to verify a previously built source
artifact. The verifier checks manifest JSON shape, package identity, archive
path existence, archive SHA-256, archive size, ZIP path safety, duplicate
archive members, exact archive member set, and per-member size/hash bindings.
Add `--require-current-head` when the manifest must bind to the current checkout
before a release or distribution decision.

Use `mucaro package macos-app --format text` to build a local double-clickable
`Mucaro AI.app` bundle under `data/exports/desktop_apps`. The bundle is a
source-bound wrapper: it launches this checked-out repository with the Python
executable recorded at build time, then runs `scripts/launch_app.py`, which
starts the local backend and opens the browser UI. Use `mucaro package
macos-app --verify data/exports/desktop_apps/Mucaro\ AI.app --format text` to
verify the bundle structure, executable bit, Info.plist binding, receipt, and
launcher target. This is not yet a portable signed installer with embedded
runtime, reinstall, upgrade, and rollback guarantees, and it is explicitly not
a Mac App Store candidate.

Use `mucaro package distribution-manifest --save` after exact-head release,
fresh-clone, source-artifact, Python-distribution, answer-trace-quality,
retrieval-quality, and operator-workflow receipts exist. The command writes a
local distribution handoff manifest under
`data/exports/distribution_manifests`, records the current Git `HEAD`,
summarizes all required receipt file hashes, summarizes source and Python
distribution artifact hashes, and keeps hosted governance explicitly marked
`OPEN`.

Use `mucaro package distribution-manifest --verify MANIFEST` to verify a saved
handoff manifest. Add `--expected-sha256 SHA256` to pin the manifest file bytes
and `--require-current-head` when the manifest must bind to the current checkout.

Use `python3 scripts/certify_source_artifact.py MANIFEST` after building and
verifying an artifact when the release decision needs installability evidence
from the ZIP itself. The script verifies the manifest against the current Git
HEAD, extracts the source archive to a temporary directory, creates a clean
virtual environment from the extracted source, installs `.[dev]`, installs
frontend dependencies, runs `scripts/release_check.py`, checks the installed
`mucaro` entry point, runs the desktop package self-test, and writes a
`source_artifact` release receipt for the exact manifest `head_sha`.

Use `python3 scripts/certify_python_distribution.py` to build and certify
standard Python distribution artifacts. The script creates a clean build
environment, builds both sdist and wheel artifacts under
`data/exports/python_dist`, installs the built wheel into a separate clean
virtual environment, verifies `mucaro --version`, verifies `mucaro --help`,
imports the installed package modules, verifies wheel `METADATA` name/version
and console-script entry point, verifies required sdist source members, records
SHA-256 and member counts for the wheel and sdist, writes a Python distribution
manifest, and writes a `python_dist` release receipt for the exact current Git
`HEAD`.

## Retrieval Strategies

`mucaro ask` defaults to `--strategy hybrid`, which recomputes deterministic lexical and TF-IDF scores from the current chunk file. Use `--strategy bm25` for local BM25 ranking, `--strategy persistent` to query the local persisted TF-IDF index, or `--strategy semantic` to query the local embedding store built during ingestion. The local semantic hash store requires normalized matched terms before returning a cited chunk and weights rarer matched terms during ranking, reducing false-positive context from hash similarity alone while improving source-lineage recall. Persistent and semantic retrieval fail closed when their stores are missing or stale; run `mucaro sources reindex` after ingesting sources to rebuild them.

Use `mucaro index status` to check whether the persistent index and semantic
store are usable. Use `mucaro sources reindex` to rebuild them from current
chunks without ingesting new files. Use `mucaro sources list` to inspect the local source library and
`mucaro sources delete SOURCE_ID` to remove one source from the assistant's
knowledge base.

## Conversation And Run History

Use `mucaro conversations create` to start a saved chat session and `mucaro
conversations ask CONVERSATION_ID QUERY` to ask follow-up questions with recent
turn context. Conversation deletion only removes the chat session; persisted
answer traces remain inspectable through `mucaro runs list`, `mucaro runs
summary`, and `mucaro runs show RUN_ID`.

## Explicit Memory

Use `mucaro memory remember CONTENT` to save a user-approved preference, profile
detail, instruction, or fact. Memory is never inferred from conversations in
this release. Enabled memory is passed to the model as preference/profile
context and recorded in answer traces by `memory_id`; it is not treated as cited
source evidence.

```bash
mucaro memory remember "Prefer concise technical answers" --kind preference
mucaro memory list --active-only --format text
mucaro memory disable MEMORY_ID
mucaro memory enable MEMORY_ID
mucaro memory export --format json
mucaro memory delete MEMORY_ID
```

## Security Profile

Use `mucaro security status` to inspect whether the app is safe for the chosen
runtime posture. Local mode is loopback-oriented. Non-loopback `mucaro serve
--host` values fail unless `MUCARO_AI_ALLOW_PUBLIC_BIND=1` is set. Production
mode requires a high-entropy `MUCARO_AI_OPERATOR_TOKEN` and explicit
`MUCARO_AI_CORS_ORIGINS`. `MUCARO_AI_DISTRIBUTION_CHANNEL=mac_app_store`
requires production mode, keeps binding on loopback, and rejects subprocess,
local-worker, and container execution before process creation.

```bash
mucaro security status --host 127.0.0.1 --format text
MUCARO_AI_DEPLOYMENT_PROFILE=production \
MUCARO_AI_OPERATOR_TOKEN="replace-with-a-long-random-token" \
MUCARO_AI_CORS_ORIGINS="https://mucaro.example" \
mucaro security status --format json

MUCARO_AI_DISTRIBUTION_CHANNEL=mac_app_store \
MUCARO_AI_DEPLOYMENT_PROFILE=production \
MUCARO_AI_OPERATOR_TOKEN="replace-with-a-long-random-token" \
mucaro security status --host 127.0.0.1 --format json
```

## Tool Runtime

`mucaro tools ...` is the local approval-gated tool surface. The runtime is
deny-by-default: unknown tools are rejected, known tools start as
`PENDING_APPROVAL`, and `mucaro tools run TOOL_CALL_ID` fails until the call is
approved.

Initial allowlisted tools are approval-gated. Most are read-only inspection
tools:

- `source_library.status`
- `product.audit`
- `workspace_profiles.list`
- `memory.list`

`web.fetch` is also allowlisted, but it is state-changing because it fetches one
HTTP(S) URL through the governed URL policy and writes a byte-pinned local
snapshot under `data/exports/web_snapshots`.

`web.fetch` records cache and robots metadata by default, removes URL fragments,
checks robots policy on redirect targets before following them, and emits parser
traces for HTML body/structure, PDF, and non-text responses. Cache headers are
recorded as source evidence, but the runtime remains immutable snapshot-only:
`reuse_allowed=false` until a future revalidation policy explicitly changes
that. HTTP error pages fail closed unless the tool call explicitly sets
`snapshot_http_errors=true`; ingestion still rejects those snapshots unless
`cite_http_error_pages=true` was also set on the fetch.

For an operator-selected multi-page browsing run, request `web.fetch_session`
with a `urls` array. The session tool does not perform autonomous search or
link-following; it validates the supplied candidates, records selected and
rejected URLs, fetches the selected pages through the same policy, and writes a
replay receipt under `data/exports/web_browse_sessions`.

After reviewing a saved snapshot, use
`mucaro sources ingest-web-snapshot SNAPSHOT_PATH` to make that frozen web
artifact available to retrieval and answer citations.

Tool call state is stored under `data/processed/tool_calls.json`. Audit events
are stored under `data/processed/tool_audit_log.json`. These files are local
runtime state and should not be committed.

## Live Model Mode

Mock mode is the default. To use Anthropic live mode, install the optional dependency group and opt in explicitly:

```bash
python -m pip install -e ".[anthropic]"
read -s ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY
export MUCARO_AI_LIVE_MODE=1
mucaro ask "How does source lineage work?" --provider anthropic
```

If the provider is unavailable, `ask` returns a structured provider error. Use `--allow-mock-fallback` only when an explicit mock fallback is acceptable.
