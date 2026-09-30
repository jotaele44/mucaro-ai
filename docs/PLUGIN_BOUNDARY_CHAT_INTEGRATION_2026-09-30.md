# Múcaro AI — exact-source in-chat plugin boundary integration (2026-09-30)

## GOAL and source boundary

Resumed the paused Work-session plugin-security vector in normal chat without modifying `main`, rewriting its Work branch, claiming access to unpushed work files, or promoting metadata inspection to executable plugin isolation.

- Repository: `jotaele44/mucaro-ai`
- Work branch, frozen base: `hardening/independent-plugin-boundary-20260930` at `2894227476e623ea528496e94d117b1f65a3d48b`; original draft PR #152 targets `main`.
- Separate integration branch: `hardening/plugin-boundary-integration-20260930`; its changes are intended as a stacked review against the frozen Work branch, **not** a competing replacement for PR #152.
- Snapshot/test receipt UTC: `2026-09-30T16:02:26Z`.

## Bounded equivalence and reconciliation

- **INTERSECTION:** Work and chat retain the same independent read-only local manifest inspector, byte-bound snapshot and capability declarations. Both retain full candidate-set audit, explicit offline mode and zero code execution.
- **WORK_ONLY before integration:** symlinked-ancestor rejection, casefold collision detection, negative policy tests, and Work's separate recovery receipt, backed by 27 exact-file Python tests. These remain intact.
- **CHAT_ONLY before integration:** full-policy entitlement fingerprint (not just policy name), empty-directory snapshot identity, complete store re-inspection at approval, refusal of duplicate plugin-ID approval, bounded 256-child store census and negative regressions.
- **NEW integration hardening:** NFC/NFD portable member-path collision detection. Preserve raw path spellings; normalization is for ambiguity rejection, not identity substitution.
- **UNION:** all above hardening and explicit valid-candidate nested snapshot identity regression.
- **SYMMETRIC_DIFFERENCE after integration:** zero intended capability loss; new behavior and test cases are additive relative to the frozen Work baseline. A file-level Git diff is not proof of semantic equivalence; exact-file regression evidence below is binding only to tested files.

## Local exact-file regression evidence

A self-contained copy of the **exact fetched Git blob bytes** was compiled and executed under Linux x86_64, Python 3.13.5 and pytest 9.0.2. The direct test imports the identical committed capability-policy dependency. The local files' calculated Git blob SHA-1 matched their current repository blob identifiers exactly.

| File | Git blob SHA-1 | Local SHA-256 |
| --- | --- | --- |
| `backend/app/plugin_boundary.py` | `b21290124a30abf92b9f37569cd6051555575f0f` | `35f1a81b7cdc163c493e83e58ef9b4809e25f5eab041434286b23ecde600cc5c` |
| `backend/tests/test_plugin_boundary.py` | `71cce1fe16c724f82cb205afabaee98a38938d6a` | `da82ef33777737bf2ade2dacd155b216dbf34e0d44c56f4121a438b4d4faa781` |
| `backend/app/capability_policy.py` (unchanged) | `b6553852acddca77541c291ce6de5f5e2ce67dde` | `12870b76e6fbb5183b6675a1220aef44c88b6c2f82626bab0ca6a78357d6ede4` |
| `skills/plugin_boundary_certifier/SKILL.md` (repo-local) | `7193d7492f94caf99706ff8239b86b10bb5756c7` | Not part of Python test imports; its committed blob is pinned separately. |

**PASS:** `python -m compileall -q backend/app backend/tests`; `python -m pytest -q backend/tests/test_plugin_boundary.py --maxfail=1` returned `34 passed in 0.11s`. Maximum line lengths for the two executed source/test files and dependency: 116, 110 and 113 (all <= 120).

**CONTRADICTION (TEST):** initial exact-file run failed its positive approval fixture because a valid `inspect_store` candidate keeps `root_name` under `candidate['snapshot']`, whereas the first chat patch incorrectly looked for a top-level error-envelope key. The narrow source correction changed only the candidate lookup. Added the permanent `test_valid_store_candidate_binding_uses_nested_snapshot_identity` regression. Reconstructed the corrected Git blob exactly and reran the full targeted suite to 34/34 PASS. The failed attempt is retained as falsification evidence, not silently discarded.

## Certification boundary and unfinished work

- **PASS (bounded):** these three exact Python source/test/dependency blobs; 34 test cases on Linux; syntax compilation and line-width check.
- **OPEN:** complete-repository pytest, Ruff, integrated plugin discovery/UI/install/update, governed tool approval wiring, prompt-configurable settings and conversation history interface, macOS native build/lifecycle, rendered browser tests and physical iPhone Safari.
- **SECURITY OPEN:** no dynamic loading or execution broker is implemented here; metadata approval is **not** a sandbox. OS-level directory/network isolation, safe process boundary, timeout/memory limits, independent revalidation and cross-device runtime parity require separate tests.
- **EXTERNAL/BLOCKED when applicable:** previous hosted GitHub Actions runner-admission zero-step failures cannot be converted into a software PASS or FAIL. Recheck current CI when a new PR head has a running job.
- **UNAVAILABLE:** ephemeral, uncommitted Work-session code and the interrupted `/tmp` full-suite log are not available from this GitHub snapshot. The screenshot alone does not establish a suite result.

## MERGE GATES

1. Keep draft PR #152 as the Work baseline; review this additive stacked branch against its exact source before incorporating it.
2. Reconcile any newly committed Work settings/plugin code using source/binary differences, never name or count equality.
3. Rerun the full repository test matrix and dedicated failure fixtures after integration, on an exact commit with actual runner steps, and audit API/UI + standalone macOS behavior separately.
4. Promote only bounded PASS rows; `APPROVED_SNAPSHOT_ONLY` never implies installation, arbitrary-code safety or full product certification.
