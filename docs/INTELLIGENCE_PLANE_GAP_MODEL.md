# Mucaro Intelligence Plane Gap Model

Baseline frozen for this development vector: `3de52aa0bf0e5514dee183e999685e6b04479382`.

Mucaro is being developed independently as an `INTELLIGENCE_PLANE_CANDIDATE`. This document does not claim federation admission or production authority.

## Target capability comparison

| Capability | Current classification | Target |
| --- | --- | --- |
| Local cited retrieval | PRESENT | Preserve and expose through contracts |
| Local tool runtime | PRESENT | Preserve; keep deny-by-default approval gate |
| Planning/execution gate | PRESENT | Preserve as UX/planning layer, not authoritative capability policy |
| Explicit memory controls | PRESENT | Preserve |
| Workspace profiles | PRESENT | Preserve |
| Model routing | PARTIAL | Provider-neutral model runtime with explicit provider capabilities |
| Agent runtime | PARTIAL | Durable workflow/agent execution with explicit state and receipts |
| Skill registry | CANDIDATE | Immutable version/digest registry with certification metadata |
| Skill mount runtime | CANDIDATE | Mount → authorize → activate → execute lifecycle |
| Skill execution ledger | CANDIDATE | Expand with input/output artifact digests and runtime identity |
| Capability authorization | CANDIDATE | Expand from local capability sets to policy-backed repo/runtime authorization |
| Execution router | ABSENT | LOCAL / REPO_BACKEND / CONTAINER / SERVICE / FEDERATION_WORKER routing |
| Federation repository discovery | ABSENT | Consume canonical federation membership/capability contracts |
| FEDERATION LOCKSTEP client | ABSENT | Activation and compatibility impact gates |
| Cross-repo orchestration | ABSENT | Contract-bound orchestration without direct domain-data ownership |
| Federation worker plane | ABSENT | Optional heavy execution target; domain repos remain autonomous |
| Advanced GIS processing | CANDIDATE | A shared CLI/eval/contract boundary covers bounded GeoJSON operations, explicit topology repair, pyproj/PROJ horizontal CRS transformations with offline-only candidate/grid provenance, WGS84 ellipsoidal measurement with bounded antimeridian/polar controls, GeoJSON/GeoPackage/Shapefile manifestations, GeoTIFF inspection/sampling/transactional clipping/resampling, exact-CRS zonal statistics, frozen Census/USGS vector snapshots, and map/export provenance through 45 cases across 21 operations. Independently frozen local datum-grid transformations, vertical/time-dependent CRS, broader axis-order and large-geometry controls, dimensional mutation, topology/overlay policy, fractional zonal coverage, rendered-map validation, real raster sources, larger real spatial corpora, vector/raster algorithms and formats, remote sensing, terrain, hydrology, bathymetry, and temporal GIS remain open. |
| State-of-the-art OCR analysis | CANDIDATE | Narrow local image OCR receipts exist with source-image hashes, engine identity, raw text, confidence states, and source-coordinate spans; PDF/page rendering, layout calibration, table/form/handwriting coverage, provider policy, and OCR eval corpus remain open |
| Governed web browsing | CANDIDATE | Approved fetch/session snapshot family exists with URL policy, byte snapshots, parser traces, and citations; automated search, rendered capture, and autonomous link-following remain open |
| Coding/programming expertise | ABSENT | Governed code read/patch/test/review capability with sandbox and diff receipts |
| Transparency/objectivity governance | CANDIDATE | Cross-capability evidence taxonomy, uncertainty, contradiction, and falsification rules |

## Intersection

The intersection between current Mucaro and the target Intelligence Plane includes:

- model invocation abstraction (currently narrow)
- planning-to-execution separation
- approval-gated local tool calls
- persistent audit/run state
- source lineage and cited retrieval
- explicit local memory controls
- workspace/configuration surfaces
- local security posture and release certification
- early evidence-governance foundation through citations, receipts, support bundles,
  and product audits

## Mucaro-only / implementation-specific capability

- existing local retrieval strategies and stores
- current CLI/browser local-assistant UX
- current mock/Anthropic routing semantics
- current planning-mode taxonomy

These remain implementation details unless promoted by a future federation contract.

## Target-only residue

- canonical federation membership discovery
- repo capability discovery
- immutable externally supplied skill packages
- runtime isolation adapters beyond in-process execution
- distributed execution routing
- federation worker execution
- Lockstep-aware skill activation
- cross-repo workflow DAGs
- domain-repo adapters
- federated execution provenance and artifact identity
- provider-neutral model runtime beyond current mock/Anthropic adapters
- full advanced GIS processing and spatial evidence gates beyond the bounded V1 CLI/eval/contract plane, including real-corpus and domain-algorithm certification
- full OCR/layout/confidence document intelligence beyond the current local image receipt boundary
- automated/rendered governed web browsing beyond the current URL-policy and immutable snapshot boundary
- coding-agent read/patch/test/review behavior inside explicit workspace safety boundaries
- cross-capability transparency/objectivity contracts for uncertainty, contradictory
  evidence, and falsification criteria

## Symmetric difference

The symmetric difference is therefore dominated by federation-specific control-plane behavior. Similar names do not prove equivalence: Mucaro's existing `tools.py` runtime is not treated as identical to the new Skill Fabric, and its planning gate is not treated as an authoritative execution-policy engine.

## Development invariants

1. Mucaro remains independently runnable.
2. Federation unavailability cannot break local Mucaro baseline behavior.
3. Federation membership must never implicitly grant filesystem, network, secret, database, repository, or deployment authority.
4. Skill mount is distinct from activation; activation is distinct from execution.
5. Skill identity is digest/version bound, never name-only.
6. Capability authorization fails closed.
7. A failed candidate activation must not replace a known-good active mount.
8. Execution receipts must identify the exact skill digest and runtime class.
9. Cross-repo orchestration will consume explicit contracts rather than direct uncontrolled access to domain repositories.
10. Advanced GIS, OCR, browsing, and coding capabilities remain unavailable until their contracts, permission gates, provenance ledgers, and regression fixtures pass.
11. `FEDERATION_INTELLIGENCE_PLANE_CERTIFIED` remains unavailable until cross-repo and Lockstep certification is performed in the federation execution environment.

## Current bounded claim

If the associated tests and repository certification pass, this branch may claim only:

`MUCARO_INTELLIGENCE_PLANE_FOUNDATION_PROVISIONAL`

It may not claim `FEDERATION_8_MEMBER_CERTIFIED` or `FEDERATION_INTELLIGENCE_PLANE_CERTIFIED`.
