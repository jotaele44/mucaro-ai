# Development Goal

## Goal Statement

Transform Mucaro AI from a validated local prototype into a governed,
release-grade, local-first AI workbench with hardened CLI workflows,
measurable retrieval quality, permissioned source ingestion, cited answer
traceability, explicit memory controls, approved tool execution, repeatable
fresh-clone certification, and a clear path toward production-quality
packaging. Capabilities must include advanced GIS processing,
state-of-the-art OCR analysis, powerful web browsing abilities, coding and
programming expertise, all governed by maximum transparency and objectivity.

The canonical source of truth is the `mucaro-ai` repository. The previous
prototype lineage remains useful as historical evidence, but new development,
release receipts, tags, governance checks, and packaging work must start from
the canonical repo.

## Current Position

Mucaro AI has crossed the prototype-promotion line. The local product surface
now includes a real CLI, local-first retrieval, source-lineage ingestion,
cited answers, explicit memory controls, an approval-gated tool runtime,
release receipts, package artifacts, and fresh-clone certification.

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
| Operator workflow | Isolated proof that permission grants, ingestion, retrieval, answer traces, memory controls, run history, and approved tools work together |
| Fresh clone | Exact-ref clone, install, CLI help, release check, launcher self-test, and desktop smoke certification |
| Packaging | Source artifact manifest, archive SHA-256, member hashes, Python wheel/sdist receipts, local distribution handoff manifest, and package status gate |
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
checkout has seven valid release receipts:

- `ANSWER_TRACE_QUALITY_CERT_OK`
- `CERTIFY_RELEASE_OK`
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
| Hosted GitHub Actions | OPEN | Hosted `CI` and `CI Startup Probe` have failed before jobs on current release-candidate heads; inspect Actions settings, billing/minutes, runner availability, and repository/account policy |
| Private branch protection | OPEN | GitHub API currently reports a plan gate for private-repo branch protection; prove protection after plan/visibility changes or keep the release scope local |
| Larger real-corpus retrieval | OPEN | Add non-fixture corpus snapshots with frozen manifests, source counts, source-format coverage, and repeatable eval thresholds |
| Advanced GIS processing | OPEN | Define GIS capability contract, local engine/runtime adapter, CRS/geometry invariants, and spatial eval fixtures |
| State-of-the-art OCR analysis | OPEN | Define OCR capability contract, local/optional model engine boundary, source-image trace schema, confidence thresholds, and OCR eval corpus |
| Governed web browsing | OPEN | Candidate fetch/snapshot/citation path, operator-supplied multi-page session receipts, robots/cache metadata, redirect-target robots evidence, HTML body/structure traces, PDF/non-text parser traces, immutable snapshot-only cache policy, and explicit HTTP error-page citation policy exist; add automated search/discovery controls, rendered-browser capture policy, and gated autonomous link-following before certification |
| Coding/programming expertise | OPEN | Define read/patch/test/review action boundary, sandbox policy, diff receipts, and code-quality eval fixtures |
| Transparency/objectivity across capabilities | OPEN | Require provenance, uncertainty, limitations, contradictory evidence handling, and falsification criteria in every advanced capability output |
| Production packaging | OPEN | Verify the local distribution handoff manifest, then convert certified local artifacts into the chosen publish/install channel |
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
7. Use support bundles to escalate hosted governance blockers without
   weakening local certification boundaries.
8. Treat all future execution adapters as subordinate to Skill Fabric identity,
   permission, audit, and certification boundaries.
9. Preserve local-first default behavior and fail closed whenever live provider,
   hosted runner, optional dependency, or index state is missing.
10. Keep release claims scoped: local certification can pass while hosted
   governance remains open.

## Readiness Rule

Do not call Mucaro AI production-ready until every required boundary has
current-head proof and no unresolved residue inside the claimed scope. A passing
local release is a strong local baseline, not proof that hosted governance,
distribution, real-corpus quality, or sustained production operations are
complete.
