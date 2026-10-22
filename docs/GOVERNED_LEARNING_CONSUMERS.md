# Governed learning: consumer release scope

This extension binds verified skill artifacts to two deterministic local workflow agents. It completes a bounded execution-and-feedback loop through the existing permission and approval gated tool runtime. It does not establish general-purpose pattern learning.

| Consumer | Required skill scope | Supported recipes |
| --- | --- | --- |
| `mucaro/agent/repository-review` | `repository_review` | `summary/1`, `evidence-review/1` |
| `mucaro/agent/release-audit` | `release_audit` | `summary/1`, `evidence-review/1` |

`summary/1` collects repository status. `evidence-review/1` collects status, the comparison diff, the requested file, and a final clean-source status. Neither interprets procedure prose as executable code. Review quality and code repair are outside this release.

## Approval and execution contract

1. Review and verify a skill using the existing candidate lifecycle. Bind its exact version and artifact hash to an explicitly selected agent and recipe.
2. Prepare a plan with the repository root, exact HEAD and comparison revision, relative file path, expected file SHA256 and unique task key. Preserve the supplied root/path strings alongside the resolved root.
3. Review and approve the exact plan hash. The binding alone does not authorize execution or grant directory access.
4. Execute each fixed read-only tool through request, exact-arguments approval and execution. Pin recipe, tool contracts, implementation bytes and physical root identity. Recheck active artifact/binding, source revision, clean state, comparison revision and file bytes.
5. Retain tool audit events, results, output hashes and hash-chained learning receipts. A repeated identical task returns the prior state; conflicting reuse is refused. Failed and interrupted work is retained without silently retrying uncertain steps. Stop/recovery cancels unstarted calls; a running tool ends at its existing boundary.
6. Score only a completed run with its exact output hash, permitted use and explicit outcome criteria. Check the active binding and persist feedback in the same learning transaction. Rewards update scoped contextual-bandit estimates; recommendations grant no execution authority.

The API uses existing operator authentication and strict request bodies. The Learning workspace exposes binding, plan review, execution, feedback, revocation and interruption recovery. Candidate rollback or binding revocation prevents subsequent steps and new feedback.

## Operational evaluation

Run `python scripts/evaluate_consumer_tasks.py --source PATH --output DIR` against a clean worktree at `2110eac33d4338a606e4e917507664e0a0135e95`, with comparison revision `404cf0afe4001c0dc6352db690f09f48dca600ae` available locally. This pins actual Mucaro source and retained release files. The evaluator freezes eight tasks and their blob identities before training: four training files and four held-out files, with disjoint payload hashes.

Eight scored training runs compare the two recipes in the two declared categories. The frozen learned policy and the one-call status baseline each execute four held-out tasks. Independent Git-derived hashes measure repository revision, complete comparison bytes and exact requested file bytes. Held-out execution never adds feedback. Sixteen real runs execute forty actual tools; learning and tool audit chains are checked and exported with the full raw results.

The local replay achieved 100% held-out evidence coverage versus 33.33% for the summary baseline. This is evidence completeness on evaluator-authored tasks using real source, not naturally collected user-success data or a semantic code-review benchmark. The fuller recipe uses four tools versus one; no cost, latency or generalization improvement is established. Model weights and external trainers remain unbound.

## Next release decision

The approved development scope is the two consumers, two fixed recipes, explicit per-run approvals, existing directory permissions, revocation/recovery, actual-run feedback, and the bounded operational replay. This document records that scope and its limits; it does not impersonate an independent reviewer or production release approval.

Release eligibility requires the canonical release checks, authenticated API and adversarial consumer tests, operational replay, and rendered Learning/consumer lifecycle at 1440, 393 and 430 pixels. Hosted CI retains operational results; Chromium QA retains the consumer lifecycle receipt. Publish as a review branch stacked on `feature/governed-learning`, preserving that earlier reviewed source. Retain local and hosted observations separately and bind final claims to exact source trees. Production merge and model-training effectiveness remain outside the demonstrated scope.

## Dependency gate repair

The first consumer release run passed implementation checks but failed current dependency audits. The same audit failures reproduced on the untouched task-source base. Update pypdf to 6.21.1, certifi in all hashed platform locks to 2026.10.19, and Vite to 7.3.7. Regenerate locks with the pinned compiler; preserve the original general cutoff and declare a separate security cutoff for pypdf/certifi. These changes resolve the observed audit blockers without suppressing advisories. Retain the failed run alongside the final source-bound certification.
