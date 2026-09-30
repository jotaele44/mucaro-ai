# Múcaro AI — in-chat resumption receipt (2026-09-30)

## Source boundary

- GitHub main at resumption: `d86d5bd93ee2dacfa17ea9070ac3fc7c3e18369b` (macOS silent launcher merged by PR #149).
- New independent branch: `hardening/independent-plugin-boundary-20260930`.
- The stopped Work session's post-main changes to prompt-configurable persistent settings, mounted conversation UI and independent plugins were **not available as pushed source**. This branch does not overwrite them, change main, or claim to reproduce them.
- Existing draft GUI PR #148 and separate PRs #150/#151 remain independent.

## Implemented in this bounded vector

- Read-only local plugin-store census with complete `source = retained + excluded` accounting.
- Strict manifest parsing, byte-specific full member snapshots, stable raw plugin IDs, duplicate-ID unresolved states and deliberate rejection of malformed / symlink / case-colliding packages.
- Offline + canonical capability-policy checks with explicit, stale-safe operator approval. Approval is snapshot-only: no plugin code is loaded or executed.
- Versioned repo-local `plugin-boundary-certifier` skill, explicit runtime-isolation and packaging handoff gates.

## Exact-file execution evidence

Executed using Python 3.13.5, pytest 9.0.2 on Linux x86_64. 27 tests passed on the following **exact Git blob identities**; the capability policy is an exact copy from main. The direct test covers this subset, **not the entire repository**.

| Path | Git blob SHA-1 | State |
|---|---|---|
| `backend/app/plugin_boundary.py` | `7c7c69e748316470e970243c9ffa290a2efdce9a` | PASS: exact local/remote blob + local tests |
| `backend/tests/test_plugin_boundary.py` | `4f3be82491031a95f260a8f0b26a460ee1c353b8` | PASS: exact local/remote blob + local tests |
| `backend/app/capability_policy.py` (unchanged main) | `b6553852acddca77541c291ce6de5f5e2ce67dde` | PASS: exact dependency blob matched |
| `skills/plugin_boundary_certifier/SKILL.md` | `3c112b4aa343a6c935d2f230e761f1f2aadd2af1` | PASS: exact local/remote blob |

`compileall` and 120-character source/test line-budget checks passed. Ruff could not execute in the available local runtime (`ruff` not installed): OPEN, not PASS. Full repo tests, GitHub-hosted runner execution, actual plugin sandboxing, settings/UI integration, native macOS packaging and iPhone Safari certification: OPEN/BLOCKED as separately applicable.

## Merge gates

1. Recover a committed snapshot of the stopped Work tree before integrating any overlapping plugin implementation.
2. Compare its manifest and capability assumptions against this standalone boundary; preserve original Work settings, conversation UI and plugin code. Adapt the boundary as necessary rather than silently choosing one implementation.
3. Wire inspection to the existing governed tool/permission execution path while explicitly separating metadata approval from OS-enforced runtime isolation.
4. Execute full repository tests, formatter/linter and positive/negative API/UI/desktop coverage on the exact integration commit. Treat zero-step hosted Actions jobs as infrastructure blockers, not evidence that code passed or failed.
5. Keep this PR draft until those integration gates have actual evidence. Do not certify standalone offline installation on every device from a Python module test.
