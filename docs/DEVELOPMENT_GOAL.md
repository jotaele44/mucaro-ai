# Development Goal

## Goal Statement

Transform Mucaro AI from a validated local prototype into a governed,
release-grade, local-first AI workbench with hardened CLI workflows,
measurable retrieval quality, permissioned source ingestion, cited answer
traceability, explicit memory controls, approved tool execution, repeatable
fresh-clone certification, and a clear path toward production-quality
packaging.

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

## Required Proof Boundaries

| Boundary | Required proof |
|---|---|
| CLI workflows | Stable commands, JSON/text output contracts, exit codes, error payloads, and tests for normal and failure paths |
| Retrieval quality | Fixture, large-corpus, mixed-format, category, binding, precision, multi-binding, and distractor gates |
| Source ingestion | Permissioned local inputs, parser metadata, source IDs, chunk IDs, file-format evidence, and no silent parser success |
| Answer traceability | Citation order, retrieval rank, source ID, chunk ID, source format, score, matched terms, snippets, and export paths |
| Memory controls | Explicit opt-in, inspect, export, disable, delete, and local-only persistence behavior |
| Tool execution | Deny-by-default request, approve or deny, execute only after approval, and append-only local audit events |
| Fresh clone | Exact-ref clone, install, CLI help, release check, launcher self-test, and desktop smoke certification |
| Packaging | Source artifact manifest, archive SHA-256, member hashes, distribution policy, and package status gate |
| Hosted governance | Current-head hosted CI, startup probe, branch-protection visibility, tag binding, and clear external blocker classification |

## Current Baseline

As of commit `17d9198c25f03b361b52db0a05e17adecc478e7f`, the local baseline is
certified for the generated answer-trace evaluation cycle:

- `ANSWER_TRACE_QUALITY_CERT_OK`
- `CERTIFY_RELEASE_OK`
- `RETRIEVAL_QUALITY_CERT_OK`
- `PYTHON_DIST_CERT_OK`
- `SOURCE_ARTIFACT_CERT_OK`
- `FRESH_CLONE_CERT_OK`
- `181 passed` backend tests
- tag `v0.1.23-generated-answer-trace-eval`

The saved governance handoff artifact is:

```text
data/exports/governance_reports/governance-report-17d9198c25f0.json
sha256=094bd7f44e1073596f37506f9f951423ee623750b42209d639ea35017c24eb99
```

That artifact verifies against the current checkout and records the hosted
governance lane as open.

## Open Finish-Line Gaps

| Gap | State | Next proof |
|---|---|---|
| Hosted GitHub Actions | OPEN | Hosted `CI` and `CI Startup Probe` currently fail before jobs at current head; inspect Actions settings, billing/minutes, runner availability, and repository/account policy |
| Private branch protection | OPEN | GitHub API currently reports a plan gate for private-repo branch protection; prove protection after plan/visibility changes or keep the release scope local |
| Larger real-corpus retrieval | OPEN | Add non-fixture corpus snapshots with frozen manifests, source counts, source-format coverage, and repeatable eval thresholds |
| Production packaging | OPEN | Convert local package artifacts into the chosen distribution path and certify install behavior from the produced artifact |
| Sustained workflow validation | OPEN | Run end-to-end operator workflows across ingest, ask, trace review, memory, tools, receipts, and recovery paths |

## Development Vector

1. Keep the CLI as the primary operator boundary for release-grade behavior.
2. Expand retrieval and answer-trace evidence before introducing heavier vector
   stores or model-backed embeddings.
3. Treat all future execution adapters as subordinate to Skill Fabric identity,
   permission, audit, and certification boundaries.
4. Preserve local-first default behavior and fail closed whenever live provider,
   hosted runner, optional dependency, or index state is missing.
5. Keep release claims scoped: local certification can pass while hosted
   governance remains open.

## Readiness Rule

Do not call Mucaro AI production-ready until every required boundary has
current-head proof and no unresolved residue inside the claimed scope. A passing
local release is a strong local baseline, not proof that hosted governance,
distribution, real-corpus quality, or sustained production operations are
complete.
