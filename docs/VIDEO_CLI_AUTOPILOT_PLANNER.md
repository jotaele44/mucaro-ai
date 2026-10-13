# VIDEO_CLI_PARITY_V1 — model-driven Autopilot planning

VCP-010 uses model output only as an **untrusted proposal**. The canonical
checkpoint/DAG/tool/lease layers remain authoritative.

## Initial plan

Natural-language submission in terminal Autopilot mode requires a governed
`coding.repo_status` receipt scoped to the current terminal conversation. The
receipt supplies the exact repository root and 40-character HEAD identity used as
the checkpoint source binding.

The planner:

1. Routes only to generative text backends (`openai_compatible_local` or an
   explicitly enabled remote Anthropic backend). The cited-QA mock is not a
   planning backend.
2. Supplies the objective, bounded repository context, and only tools that have
   explicit Autopilot success contracts.
3. Requires strict JSON with no markdown wrapper.
4. Validates `mucaro-autopilot-plan-proposal/1.0`.
5. Requires byte-for-byte objective preservation after the API boundary has
   normalized outer whitespace.
6. Rejects unknown tools, tools without success contracts, oversized/non-JSON
   arguments, invalid dependencies, missing dependencies, duplicate IDs, and
   cycles.
7. Converts the proposal through the existing deterministic
   `build_task_dag` path.
8. Stores a hash-bound planning receipt before transitioning the checkpoint to
   `READY_FOR_APPROVAL`.

No plan-generation step approves or executes a tool. Permission leases and
per-call approvals remain separate authority.

## Replanning

Replanning never overwrites an existing canonical DAG. It creates a **successor
checkpoint** with a new run identity.

A replan is admissible only when:

- the predecessor has explicit `FAILED` or `BLOCKED` task residue;
- there is no `RUNNING`, `AWAITING_APPROVAL`, or `AWAITING_INPUT` task;
- no contradiction remains unresolved;
- `max_replans` has not been exhausted;
- no successor already exists for the exact trigger hash; and
- the terminal supplies a fresh governed repository-status receipt obtained
  after the predecessor stopped.

The trigger binds predecessor checkpoint/run identity, sequence, source SHA, DAG
definition SHA, failure/block residue, evidence hashes, artifact hashes, and
contradiction hash.

The successor carries the prior planning-receipt history plus the new replan
receipt, increments the canonical replan counter, and starts
`READY_FOR_APPROVAL` with authorization `PENDING`. The predecessor remains immutable
historical evidence and no prior lease/approval is promoted into successor
authority.

## Certification boundary

`implementation_state=IMPLEMENTED` means the complete source path exists and is
regression-covered. It does **not** mean VIDEO_CLI_PARITY_V1 has runtime PASS.
Exact-head hosted execution, long-horizon behavior, and the remaining
certification rows must still close before parity can be certified.
