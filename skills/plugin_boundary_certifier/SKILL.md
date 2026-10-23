---
name: plugin-boundary-certifier
version: 1.0.0
status: candidate
---

# Independent Plugin Boundary Certifier

**Purpose:** inspect independently installable, locally stored Múcaro plugin packages without loading or running untrusted code. This is a repo-local skill; it is not an installed global ChatGPT skill.

## Inputs

- Exact absolute plugin-store path and its immediate child directories.
- Frozen `mucaro-plugin.json` manifest in each plugin directory, with exact v1 fields: `schema_version`, `id`, `version`, `entrypoint`, `capabilities`, `network`.
- An explicit versioned `CapabilityPolicy`, an offline-mode boolean, and the operator's approval decision.
- The tracked `backend/app/plugin_boundary.py` implementation; do not assume API completeness beyond read-only certification and snapshot authorization.

## Run

1. Freeze repository commit, store scope, policy ID and offline-mode setting. Explicitly distinguish the GitHub snapshot from any unpushed Work-session tree.
2. Enumerate all immediate store children. Count excluded non-plugin regular files separately; preserve invalid, blocked, duplicate-ID and eligible candidates.
3. For each plugin: reject symlinks and special members, require strict UTF-8 JSON with no duplicate keys, validate exact v1 manifest fields and stable raw plugin ID, enforce member and resource limits, hash full member bytes and bind an exact canonical snapshot receipt.
4. Use the canonical capability policy without inventing permissions. Network-required plugins remain BLOCKED while offline. No plugin imports or execution occur in this phase.
5. Preserve *all* duplicate stable-ID candidates as unresolved; never choose the nearest, first-sorted, or name-normalized winner.
6. On explicit operator approval, re-inspect the exact current bytes and settings, then reject a stale or denied snapshot. `APPROVED_SNAPSHOT_ONLY` never means execution approved, sandboxed or certified.
7. Run positive and deliberate negative regressions (byte tampering, policy drift, duplicate IDs, Unicode confusables, symlinks, traversal, unknown capabilities, inconsistent network class, non-boolean offline setting and conservation).
8. Publish the frozen commit, test environment and bounded result. A local candidate test pass does not certify a different remote commit, plugin runtime isolation, packaging, iPhone Safari, or a completed Múcaro release.

## Mandatory security and integration gates

- The plugin execution broker **must** independently enforce OS file and network isolation, existing permission-engine decisions, execution time/memory limits, crash containment and a fresh content identity check. Python `-I`, a declared `network: none`, metadata inspection and approval alone are not sandboxing.
- Keep plugin installation and activation independent from GitHub/Floot/online catalogs. Fetch/install/update are optional, explicitly online operations.
- Before merging against Work output, compare Git blob SHA and imports; if Work independently implemented a loader, integrate the inspection boundary without overwriting or bypassing its existing approvals.

**Current certification:** standalone inspection regression candidate; full repository, actual unpushed Work changes, native packaging and runtime isolation remain OPEN.
