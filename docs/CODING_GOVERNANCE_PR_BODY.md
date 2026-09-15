# Coding governance PR summary

This bounded increment builds on merged PR #21.

It adds:
- approval-gated `coding.patch_apply`,
- stronger patch-transaction path/HEAD/rollback invariants,
- plan-only test/lint/type/build/security/benchmark contracts,
- source/line/hash-bound code-review evidence,
- coding regression manifest and blocker ledger.

It does not add arbitrary repository command execution, writable container source mounts, branch/commit/PR mutation, or release certification.

State: `PROVISIONAL`.
