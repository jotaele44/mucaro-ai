# Research upgrade: isolated development, stage 1

Baseline: `621e722e3412df711e41c7afa27859751b8abb61` (0.1.24).
This branch does not activate research in the production UI, change release
authorization, or claim model-quality parity. Its first increment extends the
existing snapshot and artifact identity components, with no added runtime dependencies.

## Implemented boundary

`backend.app.research_evidence` reads existing `mucaro_web_snapshot_v1` captures
against hashes supplied by an acquisition manifest. It checks outer bytes,
decoded payload bytes, length, UTC retrieval metadata, raw byte spans, source
and claim identifiers, path confinement, and declared/processed counts.
Duplicate JSON keys and nonfinite JSON numbers fail closed. Files are read only.
The 32 MiB per-snapshot replay ceiling is explicit; larger captures fail rather
than silently truncate. Decoded retained payloads also have a 128 MiB packet budget;
this is not a bound on total process RSS. A new adapter will be needed for larger or non-web inputs.

Successful replay proves consistency with the supplied frozen manifest, not the
manifest's authenticity, claim entailment, canonical identity, or source truth.
HTTP error captures remain preservable but are not affirmative evidence here.
Offsets address raw bytes, not Unicode characters or OCR/normalized text.
This is a local trusted-workspace auditor, not a sandbox against concurrent
adversarial filesystem mutations or an immutable archive service.

`backend.app.research_benchmark` prepares two-system paired cases, randomizes
labels independently by case, preserves original answer text and hashes, and
separates identity mapping from reviewer packets. The CLI creates the private
mapping exclusively with mode 0600 (POSIX); Windows ACL verification remains open.
Do not publish mapping files with reviewer packets. Prose may reveal a system;
reviewers must explicitly record identity leakage. Scores require complete paired
coverage. Random-label win counts are diagnostic, not system performance scores.
No paid model calls, external reviewers, or actual ChatGPT comparison are run by
these tools. Synthetic cases test the harness only.

## Commands (each is one line)

Run from this checkout using a prepared Python environment:

`python -m pytest backend/tests/test_research_evidence.py backend/tests/test_research_benchmark.py backend/tests/test_research_snapshot_integration.py`

`python scripts/research_audit.py audit packet.json --root frozen_sources`

`python scripts/research_audit.py blind paired_answers.json --private-key private_mapping.json > reviewer_packet.json`

`python scripts/research_audit.py review reviewer_packet.json reviewer_scores.json`

Research packet shape: schema_version=mucaro-research-packet/1; sources contain
source_id, relative path, snapshot_sha256; claims contain claim_id, text,
source_id, byte_start, byte_end (exclusive), span_sha256.
Paired input: array of case_id, question, responses; each response contains
system_id, run_id, answer. Reviewer rows: case_id, scores={A:0..100,B:0..100},
identity_leakage:boolean. Rubrics must be frozen before producing scores.

## Predeclared comparative protocol

1. Freeze scope, questions, source-access conditions, budgets, rubric weights,
   critical errors, abstention policy, and acceptance margins before runs.
2. Maintain disjoint development and held-out cases. A custodian keeps held-out
   answers and label mappings unavailable to developers and reviewers respectively.
3. Separate frozen-corpus, live-web, and end-to-end product tracks. Record dates,
   actual model identifiers/settings, source access, retries, failures, and cost.
4. Compare baseline, strong single-model control, candidate, and contemporaneous
   ChatGPT Deep Research. An API model is a different comparator.
5. Include English/Spanish, general topics, Puerto Rico GIS, identities with
   homonyms and historical changes, contradictions, missing sources, and OCR.
6. Freeze reviewer scores before unblinding. At least two independent reviewers
   adjudicate consequential disagreements. A second model alone is not independent
   source verification. Record reviewer access and identity leakage.
7. Report coverage and abstention with accuracy, citation support, false merges,
   spatial errors, replay rate, cost, latency, memory, and storage. Failures remain
   in the denominator. Report warm and cold cache separately.
8. Use paired uncertainty intervals clustered by question for repeated runs;
   predeclare minimum detectable effects/sample size after a development pilot.
   Do not select favorable metrics or reruns after seeing held-out results.
9. Superiority requires the predeclared bounded claim to pass, with no critical
   safety/evidence regression. General research noninferiority and specialist
   superiority are separate hypotheses. No numeric quality claim exists yet.

## Remaining implementation and gates

| Stage | Existing value to reuse | Remaining work | State |
|---|---|---|---|
| Preservation | snapshots, artifact_identity, provenance | durable acquisition manifests, claim registry and transformations | PARTIAL |
| Orchestration | checkpoints, work_runs, web traversal | resumable research DAG, marginal-value budgeting, stopping rules | OPEN |
| Models | router, permissions, failover | role-specific bakeoff and provider adapters chosen from results | OPEN |
| Geography | CRS, topology, raster, GIS evaluation | research claim bindings, uncertainty and temporal fixtures | OPEN |
| Reconciliation | artifact identity | source manifestation comparisons, candidate graph, dated binding decisions | OPEN |
| Verification | release receipts, evidence auditor | independent implementation/review, contradiction adjudication | OPEN |
| Comparison | new blind packet harness | held-out corpus, comparator runs, independent scoring and inference | OPEN |
| Consumers | snapshot producer integration test | actual research UI/API and federation consumer integration | OPEN |
| Release | current release system | exact candidate hashes, all required tests, platform runs and rollback rehearsal | BLOCKED |

## Release and rollback

All outputs remain AUDIT_ONLY; release certification, deployment, federation
activation and skill promotion are withheld. These offline commands cannot issue
a production certificate. No existing release issuer or consumer accepts their
integrity PASS as a release receipt. Before integration, add an explicit research
capability contract and exact-release acceptance lane to the existing gate system.

Baseline remains on main. Development rollback consists of returning to the
baseline checkout; this increment makes no data migration or production change.
Deployment rollback and native macOS verification still require actual rehearsals.
The earlier `mucaro_isolated_test_evidence.zip` is historical: it declares
exact_head=false, reconstructed-source tests, and native macOS NOT_RUN. It is not
release evidence for this branch.
