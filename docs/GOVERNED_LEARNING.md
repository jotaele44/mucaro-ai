# Governed learning and reward feedback

Status: **PROVISIONAL**, implemented on a feature branch based on
`404cf0afe4001c0dc6352db690f09f48dca600ae`. This is a bounded local text-learning
implementation, not general-purpose autonomous learning certification.

## Behavior

Open **Learning** in the existing navigation. Submit a lesson with its exact original
text evidence, source ID and permitted purpose. Alternatively authorize future saved-run
capture, or scan one saved run. Automatic recognition uses explicit lexical cues in the
user's query; model-generated answers are evidence only. Recognition is discovery,
not proof that a lesson is true, reusable or general. No-cue observations produce
NO_CANDIDATE; oversized lessons fail explicitly. Capture is off by default.

Each candidate retains original strings, a UTF-8 evidence hash, all discovery
destinations, outcome declaration, stable curator queue ID, scope and version.
Ambiguous or unmatched candidates remain UNRESOLVED. An operator can resolve the
destination and scope without deleting the original discovery set.

Review records rationale, positive and negative cases, unresolved conflicts, evidence
verification, authority review and approval for the exact addition. Accepted candidates
can be applied to versioned local artifacts. Verification records an operator's
positive/negative evaluation attestation and receipt. Only VERIFIED general instructions
enter the existing bounded memory/prompt-context selector. They do not replace the
base system prompt. Rules exceeding the provider's 320-character normalized item limit
are rejected before acceptance. Other memory limits still apply and record exclusions.
Rollback removes the artifact from subsequent prompt use and policy recommendations
while retaining evidence and receipts. Existing in-flight generations are not revoked.

| Destination | Mechanism |
| --- | --- |
| instruction | Verified, explicitly approved global prompt-context addition |
| skill | Versioned scoped procedure artifact |
| project / knowledge | Scoped artifact available in the learning store |
| evaluation | Regression-case artifact; no generated code is executed |
| dataset | Review queue; application blocked with MODEL_TRAINER_UNBOUND |

Curator IDs identify **local queues**. No external agent workers, model-weight trainers,
automatic executable-skill installation or network submissions are bound. Existing
Skill Fabric execution and permission policies remain independent. Consumers can
inspect verified scoped artifacts through the authenticated learning listing; agent
workers must be bound and tested separately before claiming delivered agent training.

## Reinforcement learning boundary

The initial learner is a bounded contextual bandit value learner for workflow
recommendations. An operator records an episode, a VERIFIED skill artifact, the exact
scope/context, permitted use, a finite reward in [-1, 1], and observed outcome evidence.
Each reward is pinned to the artifact hash. Identical episode replay is idempotent;
conflicting reuse is rejected. Negative rewards are retained.

For each eligible artifact and exact context, the value is `sum(rewards)/(episodes+1)`
(a zero-reward prior). A unique positive top value produces a recommendation. Ties,
missing candidates and nonpositive top values remain UNRESOLVED. The policy digest
binds the eligible artifact hashes, episode counts and computed values. Reward events
and artifact versions are retained in the receipt chain, allowing recomputation.
Recommendations never authorize execution. There is no online exploration, neural
model training, held-out transfer certification or causal improvement claim. Feedback
may be biased or mistaken; review is operator-attested rather than independently
established by the application.

## Persistence and API

`data/processed/learning.sqlite3` stores candidates, feedback, capture policy and
receipts together in atomic SQLite transactions. Each receipt hashes its predecessor
and the resulting complete materialized state. Reads verify the chain and state
before use; stale versions and corruption fail closed. This detects accidental or
unreconciled edits, not an attacker able to rewrite the complete database and hashes.
Keep the database under the same local filesystem protections as the other app data.

The `/learning` API uses the existing operator-authentication boundary. Unconfigured
auth retains the application's trusted-local-operator behavior. Request fields are
strict and unknown fields are rejected; evidence cannot supply a reviewer identity.
OpenAPI includes recognition, run scan, capture configuration, destination resolution,
review, apply, verify, rollback, feedback and scoped recommendation operations.

The frontend static catch-all is mounted after the learning API. Scrollable JSON
regions support keyboard focus. Training permissions apply to the declared purpose;
no external permitted-use inference or sensitive-data classification is performed.
Use only evidence the operator is authorized to retain for that purpose.

## Validation and remaining gates

- Focused learning suite: **20 passed**.
- Full final Python suite: **1,281 passed, 7 skipped, 1 failed**.
- The failure is the inherited Linux `/proc` socket-inspection test. It also fails on
  the pristine pinned baseline; raw observations are preserved separately.
- New learning modules, changed services and tests pass Ruff; `git diff --check` passes.
  The existing main module has baseline lint findings outside this change's scope.
- Frontend production build passes.
- Chromium at 1440, 393 and 430 pixels passes the real recognition → review → apply →
  verify → rollback journey and reward-entry/recommendation interface. Zero horizontal
  overflow, serious/critical accessibility findings in the Learning workspace, or
  browser page errors were observed. Mobile viewport checks are not physical-device proof.
- Fixtures are synthetic and isolated from real user data. They do not prove learning
  effectiveness, correctness of real lessons, or model training.

Release certification remains OPEN on the inherited environment-dependent gate and
its seven skipped checks. External worker/trainer binding, held-out transfer evaluation,
semantic contradiction detection, broader recognition recall, large-store scalability,
retention/deletion policies and binary evidence ingestion remain outside this bounded
implementation. No production merge or publication is part of this checkpoint.

Subjective design scores: execution plan 94/100; hardening strategy 95/100. These are
planning assessments, not measured readiness or certification scores.

Evidence is in `docs/evidence/governed-learning-2026-10-16/`; its manifest binds the
source files and preserved test/rendered artifacts. The reusable
`mucaro-governed-learning` skill was validated and saved separately; skill publication
is not model-weight training.
