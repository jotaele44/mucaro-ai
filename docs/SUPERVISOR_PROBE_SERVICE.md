# Standalone supervisor probe service (candidate)

This is a bounded durability test fixture, **not** the production agent worker.
The service is deliberately opt-in and never auto-started by FastAPI.

## Invariants
- The queue is persistent SQLite state, separate from ChatGPT response state.
- The worker only claims tasks with `kind=supervisor.read_only_probe` and `mutates_state=false`.
- The permitted handler is built-in SQLite `SELECT 1`; it never imports or executes user-provided commands.
- Expired read-only claims can be requeued; uncertain mutating claims enter `NEEDS_RECONCILIATION`.
- Task completion must include a durable evidence receipt and a valid worker lease.
- A claim is **not** authorization. This module must not be wired to unrestricted tools.

## Bounded validation

From the repository root, with an existing dedicated test database:

```bash
PYTHONPATH=backend python3 -m app.supervisor_service --db /path/to/dedicated-test.db --once
```

An external process manager can run the same module without `--once`, but no
launchd/production service registration is supplied or certified by this patch.
Only use a dedicated supervisor test database. The service does not provide
authentication, authorization grants, run-to-task dependency scheduling,
provider routing, real worker cancellation, or multi-host liveness.

## Remaining certification gaps

1. A forced worker-process kill and fresh-process recovery PASS in the test corpus; independent service-manager restart, sustained unattended operation, and power-loss recovery remain OPEN.
2. Exact-head source/configuration verification for every executable task.
3. Integration with canonical `work_runs`, #340 runtime records, #192/#193 DAG/autopilot, and policy-bound tool dispatch.
4. External-mutation side-effect reconciliation and operation receipt verification.
5. Independent security/CI review; full suite; distribution and power-loss validation.

AFK-95 certification: **WITHHELD**.
