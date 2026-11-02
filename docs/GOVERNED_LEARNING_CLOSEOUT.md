# Governed learning: release and effectiveness closeout

This is the successor to `GOVERNED_LEARNING.md`; preserve that earlier report and
all original checkpoint evidence as historical. Publication is a draft PR, not a
production release or a main-branch merge.

## Source and publication identity

- Historical implementation/evidence checkpoint: `6751d77b2b9f15e411f5c776cd6c5d5825bc38c3`.
- Tested local source: `f0c18f390f8b457389575b1bac878ad0fc9d84eb`.
- API-authored published source: `fb28614600f0b966e849543a2397be6059bab4b9`.
- Both source trees: `df008a94d3992533a6e21975563e8da99901ea2c`.
- Draft PR: https://github.com/jotaele44/mucaro-ai/pull/229.

These commits are different Git manifestations with byte-identical path/blob/mode
trees. The upload verified every changed blob SHA and the complete resulting Git
tree SHA. The subsequent evidence-only commit adds this report and its receipt
bundle; the local release receipt remains bound to the tested source above.

## Closed local gates

The canonical local release certifier returned **CERTIFY_RELEASE_OK**: **21/21 gates
PASS**, including **1,296 Python tests passed, zero failures and zero skips**. The
source package built and verified at **563/563 members**. Answer trace, operator
workflow, retrieval, GIS, JSON validation, inventory, desktop-package self-test,
Python dependency audits, npm audit and frontend build gates passed. The complete
canonical receipt and raw command log are preserved in the closeout evidence folder.

The inherited self-socket failure was a PID namespace discrepancy: `os.getpid()` and
the numeric IDs exposed by mounted `/proc` did not refer to the same process. Own-PID
lookup now uses `/proc/self`; every other PID keeps its explicit numeric path. A
real socket test plus a separate-process-path negative regression pass.

All seven earlier skips required PowerShell 7. Provisioning a hash-verified official
PowerShell 7.6.7 Linux asset let those parser/target-classification checks execute.
This is not physical Windows or device verification. The original source's 393px,
430px and desktop Learning lifecycle/reward-interface evidence is reused because
no frontend file changed in this tranche.

## Bounded training-effectiveness result

The evaluator binds two synthetic workflow consumers in each of three exact
contexts: preserve stable IDs, conserve nullable rows, and preserve tied candidates.
It evaluates actual outputs against frozen fixture expectations.

- 12 training fixtures, two actions each: **24 reward episodes**.
- 12 held-out fixtures: training/held-out ID intersection **0**, union **24**.
- Held-out results never enter training feedback; the feedback hash is unchanged.
- Trained selection: **12/12 correct**; uniform-choice expected accuracy: **50%**.
- Difference: **+50 percentage points** on these finite synthetic cases.
- Untrained policy abstains in all three contexts; unseen context remains UNRESOLVED.
- Reward-inversion negative control: **0/12 correct**.
- The full report reproduced byte-for-byte in a second isolated run.

This proves the bounded reward-conditioned selector responds to feedback and can
choose the successful bound evaluator workflow for these held-out fixtures. It
also demonstrates that wrong rewards can teach wrong choices. It does not establish
real-world transfer, automatic executable-skill installation, external agent training,
model-weight training, or resistance to feedback poisoning. Explicit exceptions keep
split/leakage gates active even under optimized Python.

## Remaining certification boundary

The broader service-network exposure gate remains **BLOCKED in this local runner**:
independent inspection of the launched service's numeric PID is unavailable. It
must not be replaced with the certifier's own sockets or inferred from source code.
The certifier now retains a failed gate with `observation_status=BLOCKED`, then cleans
up the service. Positive loopback, public-bind rejection and observation-failure
regressions pass. A separate security adjudication records the real blocked attempt.

Dependency audit snapshots from the earlier run are reused only after proving the
Python lock, npm lock and vulnerability dispositions byte-identical. Source secret,
static-security and permission-boundary checks pass. Hosted CI, rendered QA and
macOS jobs are independent gates; their final statuses must be read from their exact
head before any overall release certification or merge. No queued/running job is PASS.

The reusable `mucaro-governed-learning` skill was extended, validated and saved with
the PID-identity and held-out-evaluation lessons. This is procedural skill retention,
not neural-model training. Overall product release remains **PROVISIONAL** until
hosted/runtime/native and real-task training claims independently close.
