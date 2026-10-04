# Mucaro AI Product Audit

**Audit date:** 2026-09-30  
**Repository baseline:** `main` at `2b78fcf3e6ff577b065f4396f9ffc874174b0c7c`  
**Scope:** product behavior, engineering, security/release posture, and rendered desktop/mobile UI. Findings below distinguish committed baseline evidence from fixes in the current uncommitted worktree.

## Executive assessment

Mucaro is a substantial local-first workbench, not a blank prototype: its repository includes a CLI, local ingestion and cited retrieval, approval-gated tools, conversations and explicit memory, desktop launch/setup paths, release ledgers, and extensive automated tests. The current rendered interface is cohesive and its main workflows are usable; the evidence supports focused polish rather than a wholesale visual redesign.

It is not yet a finished or release-certified product. `mucaro audit product` reports **16 PASS / 7 OPEN / 0 BLOCKED (69.6%)**. The open product domains are advanced GIS, state-of-the-art OCR, governed web browsing, coding expertise, transparency/objectivity validation, hosted release governance, and final-phase GUI/operator workspace proof. `mucaro release scope status` reports **55 PASS, 49 OPEN, 7 BLOCKED, 26 PROVISIONAL, and 1 NOT_APPLICABLE** out of 138 requirements; the release status is **PROVISIONAL**. These denominators are separate and neither should be summarized as a single completed-product claim.

## Findings and actions

| Priority | Finding | Confidence | Evidence and status |
|---|---|---:|---|
| P1 — behavior | The UI supplied `model_provider: "mock"` by default, overriding `MUCARO_AI_MODEL_PROVIDER` and contradicting the existing UX audit's stated fix. The settings page also displayed the mock choice instead of the service-configured provider when the user had not explicitly chosen one. | 98% | Fixed in the worktree: omit the implicit provider from request options, migrate legacy implicit mock settings while preserving explicitly selected providers, and use `/config` for the settings fallback. Added rendered regression coverage. Evidence: `frontend/src/App.jsx:17,36-40`, `frontend/src/components/ChatView.jsx:191-197`, `frontend/src/components/workspaces/SettingsWorkspace.jsx:39`, `qa/rendered/rendered_qa.mjs:168-179`. |
| P2 — first-run UX | The source file picker accepted any file even though the API rejects unsupported suffixes, and the UI did not explain supported formats or metadata-only media handling. | 98% | Fixed in the worktree with a matching `accept` list and a concise formats/limitations hint. Evidence: `frontend/src/components/workspaces/SourcesWorkspace.jsx:6,96,102`, `backend/app/main.py:116,137-140`, `backend/app/ingestion.py:235`. |
| P2 — mobile/accessibility | WebKit rendered the retrieval `<select>` below the 44px touch-target bar. Keyboard focus indicators were also missing on the skip-link destination and hidden file-input label; modal scrolling could propagate to the page. Several text fields also lacked explicit form names/autocomplete hints. | 95% | Fixed in the worktree with explicit mobile select height, visible focus states, contained sheet scrolling, and named/autocomplete-disabled text controls. The rendered QA activates citation sheets with the keyboard. Evidence: `frontend/src/styles.css:241,429,841,924,1106-1110`, `frontend/src/components/Composer.jsx:126`, `qa/rendered/rendered_qa.mjs:183-205,396-405`. |
| P2 — data safety | Source removal and saved-memory deletion were immediate despite being irreversible; a prior UX audit's broad statement that deletes required confirmation was not true for these workspace controls. Memory toggle/delete and source reindex failures also lacked complete user-facing mutation handling. | 99% | Fixed in the worktree with consequence-specific confirmation sheets, cancel-preserves-data coverage, pending/busy states, visible mutation errors, and confirmation-and-delete browser coverage. Evidence: `frontend/src/components/workspaces/SourcesWorkspace.jsx:56-82,146-158`, `frontend/src/components/workspaces/MemoryWorkspace.jsx:40-68,117-126`, `qa/rendered/rendered_qa.mjs:352-363,375-384,430-438`. |
| P2 — test reliability | The macOS app package test fixture omitted three required runtime inputs, and its expected verification label no longer matched the implementation. This caused one failure in the baseline full suite. | 100% | Fixed in the worktree by completing the fixture and updating the assertion. Evidence: `backend/tests/test_cli.py:1688-1690,1794`. Full suite passed afterward. |
| P2 — completion evidence | The baseline had no valid exact-HEAD local release receipts. A security-gates receipt passes for the audited Git HEAD, but the worktree contains uncommitted changes and required release receipts remain missing; that receipt is not proof for the changed source tree. Hosted governance was not queried. | 100% | Keep release and hosted status open until checks run on a clean, exact final commit and all external gates have their own evidence. `docs/RELEASE_STATUS.md:6,45`; `mucaro audit completion` showed required exact-head receipts missing. |
| P3 — maintainability | `backend/app/cli.py` is 8,395 lines. A broad `ruff check` over `backend/app`, `backend/tests`, and `scripts` reports 253 diagnostics, including import ordering, blind exception handling, and default-argument warnings. There is no dedicated lint target in the Makefile; bulk auto-fixing would create a high-risk, hard-to-review change. | 100% | Establish and document the intended Ruff rule profile, then remediate in scoped batches with behavior tests. Decompose the CLI by command domain only behind stable CLI contracts. Evidence: `backend/app/cli.py:251,2895`, `pyproject.toml:56`; measured at this audit. |
| P3 — external proof | Live Anthropic/Brave paths, actual iPhone/iPad Safari and VoiceOver, physical double-click, Apple signing/notarization, and private-repository governance require credentials, hardware, or owner decisions not available in this audit. | 100% | Leave these gates explicitly OPEN/BLOCKED; do not substitute emulation, empty credentials, or runner launches for the required evidence. Evidence: `docs/RELEASE_SCOPE_2026-09-29.md:7,19-21`. |

## Verification performed

| Check | Result |
|---|---|
| Backend tests | `992 passed, 1 skipped`; 448 dependency/deprecation warnings remain. |
| Rendered Chromium UI QA | `86/86` passed at desktop and iPhone-emulated viewports, including cancel/confirm flows for source and memory deletion; axe reported no violations in the empty, answer, sources, or settings views. |
| Rendered WebKit UI QA | `86/86` passed at desktop and iPhone-emulated viewports, including cancel/confirm flows for source and memory deletion; axe reported no violations in the tested views. |
| Frontend build | `npm run build` passed. |
| Frontend dependency audit | `npm audit --audit-level=moderate`: 0 vulnerabilities. |
| Security gates | `SECURITY_GATES_PASS`; this receipt is bound to the committed baseline HEAD, not the dirty worktree changes. |
| Startup and repository checks | Desktop launch smoke passed; strict preflight reported `PREFLIGHT_OK` with `.env` absent (expected for this fresh checkout); JSON validation passed for 36 files. |
| Product/release audit | Product audit: 16 PASS / 7 OPEN. Exact-HEAD completion remains open; before implementation changes it had no release receipts, and the security-gates receipt alone does not satisfy the release receipt set. |

Rendered iPhone results use browser emulation, not physical iOS Safari. Live-provider behavior and hosted GitHub governance were not exercised. Backend warnings and the broad Ruff findings are recorded as follow-up debt, not silently treated as release failures or as passing gates.

## Recommended next sequence

1. Preserve the current local-first, single-operator, approval-gated product boundary; obtain or formally resolve the missing Release-Grade Workbench Specification before changing the release denominator.
2. Close the small confirmed behavior and UX regressions in this worktree and keep their regression tests.
3. Use `docs/DEVELOPMENT_GOAL.md`, `docs/MUCARO_FLAGPOLE_V1.md`, and the current release ledgers to order the seven open product domains. For each, require a contract, positive/negative fixtures, reproducible evidence, and an explicit state before claiming completion.
4. Complete credentialed, hardware, hosted, and signed-distribution gates only in their proper environments. Re-run exact-head certification after changes land.
5. Continue the UI finish pass using rendered screenshots and keyboard/accessibility checks; reserve new specialized workspaces for validated operator workflows rather than adding surfaces for appearance alone.

## Addendum (2026-10-03): coding CLI checkpoint review

**Scope.** The `mucaro code`, `mucaro web` and `mucaro mcp` checkpoint (commit `4f49330`, on branch `feature/cli-coding-agent`) was reviewed against this repository's own rules: deny-by-default tools, the existing approval-and-audit path, loopback-first provider access, and the contract that engine/image failures are `BLOCKED`, never `FAIL` (`docs/CODING_CAPABILITY_STATUS.md`). Every finding below was confirmed by reading the code and is fixed on the branch with regression tests. **No product-audit domain changes status: `mucaro audit product` still reports 16 PASS / 7 OPEN, and `coding_programming_expertise` remains OPEN.**

| ID | Finding (at the checkpoint) | Evidence | Confidence | Status |
|---|---|---|---:|---|
| G1 | The agent's approval prompt cut arguments at 600 characters, so an operator could approve a patch tail they never saw. | `backend/app/cli_code.py:207` | 100% | Fixed `3cb80f7` |
| G2 | The model, not the operator, supplied the live-search opt-in. | `backend/app/coding_agent.py:113` | 100% | Fixed `3cb80f7` |
| G3 | Agent `read` ran unprompted with no credential guard (`.env` holds `MUCARO_AI_OPERATOR_TOKEN`); contents are sent to a remote provider. | `coding_agent.py:39,91`; `coding_workspace.py` blocks only `.git` | 100% | Fixed `3cb80f7` |
| G4 | Tool output re-entered the prompt unfenced, with no untrusted-data rule (INV-014). | `coding_agent.py:141-145` | 100% | Fixed `3cb80f7` |
| G5 | Agent and CLI approvals never reached the hash-chained tool audit log. | `coding_agent.py:190`; `tools.py:353` | 100% | Fixed `3cb80f7` (agent, `code`, `web`, `mcp`); `348d53a`, `3a9ea1c` (push, PR, plugins) |
| G6 | `parse_action` rejected any prose around the JSON; MCP server stderr was discarded; `select` on pipes fails on Windows. | `coding_agent.py:65-78`; `mcp_client.py:73,87` | 100% | Fixed `3cb80f7` |
| G7 | `provider_chat("anthropic")` skipped the `MUCARO_AI_LIVE_MODE=1` opt-in that `mucaro ask` requires, so repository content could go to a third party with live mode off; it also asked for only 2000 tokens, ignored refusals and truncation, and let SDK errors escape as tracebacks. | checkpoint `provider_chat` vs `model_router.py:246-262` | 100% | Fixed `4045003` |
| G8 | `run_coding_container_plan` recorded a stopped daemon or missing image as a failing check, contradicting the documented BLOCKED contract. | `coding_container.py:313` | 100% | Fixed `0505428` |
| G9 | Regression introduced by the G8 fix and found by auditing every caller: `scripts/certify_coding_mutation_live.py` would end with a traceback and no receipt when the engine is unavailable. | `scripts/certify_coding_mutation_live.py:25` | 100% | Fixed with the closeout commit |

**Added on the branch (hermetically tested; none of it is certified evidence):** opt-in step events (`--stream`); `code push` and `code pr-create`, bound to the preserved PR request and credential-last; plugin bundles (`mucaro plugin ...`); `code checks --engine` and `--profile`; `scripts/certify_coding_agent_live.py`. Reference: `docs/CLI.md`. Procedure for the live evidence: `docs/CODING_LIVE_EVIDENCE.md`.

### Verification performed

| Check | Result |
|---|---|
| Backend tests | Baseline `1008 passed, 1 skipped`; at the last code commit `1170 passed, 1 skipped` (162 tests added by this review in seven new files, all passing); three more tests were added afterwards for G9 and run separately. |
| Mutation checks | 70 deliberate breakages of the new guards, each confirmed to fail a test. Four survived at first; each exposed a missing test, which was added and re-verified. |
| Ruff | 28 changed Python files: no new findings (the four that remain, and the 13 in `cli.py`, exist at the base commit and were deliberately not auto-fixed). |
| Static security triage | `PASS`: 113 findings (107 at the baseline), 103 LOW and 10 NOT_APPLICABLE, 0 untriaged, 0 stale; the six new findings are covered by the existing rule-wide S603/S607 dispositions. |
| JSON validation, strict preflight | `JSON_OK checked=38`; `PREFLIGHT_OK`. |
| Product audit | Unchanged: `PRODUCT_AUDIT_OPEN`, 16 PASS / 7 OPEN. |

### Not run, and still open

- **Live container receipts:** Docker Desktop's daemon was stopped on the reviewing machine and no pinned coding image was present, so nothing was executed in the isolated boundary. The runtime now reports that state as BLOCKED instead of FAIL.
- **Live-provider PASS:** no API key and no local model server were available. An empty or absent input is not evidence. The certifier reports BLOCKED until they exist.
- **Authenticated push and pull request:** no live remote action was performed, by design (credential-last, owner-approved).
- **`make security-gates`** (needs network for dependency audits) and **hosted CI**: not run. The frontend was not rebuilt or re-tested after the earlier UX commit.
- **Document drift for the owner to adjudicate, not edited here:** `docs/CODING_GOVERNANCE_BLOCKERS.md` CODE-B03 still says the branch/commit/PR lifecycle is not implemented, while `docs/CODING_CAPABILITY_STATUS.md` records the local lifecycle as merged source.
