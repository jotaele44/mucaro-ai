---
name: prompt-settings-certifier
version: 1.0.0
status: candidate
---

# Prompt Settings + Conversation Thread Certifier

**Purpose:** certify Múcaro's persistent settings plane and chat-mediated settings mutations without confusing a UI control, prompt parse, successful HTTP response, or passing historical commit with exact-head product certification. This is a repo-local skill, not a globally installed ChatGPT skill.

## Inputs

- Frozen repository HEAD and merge base.
- `backend/app/preferences.py`, `backend/app/work_runs.py`, preference API models/routes, frontend Settings/Chat components, and rendered QA.
- Exact persistent setting denominator and restricted credential denominator.
- Baseline `main` CI evidence for separating inherited failures from candidate regressions.

## Mandatory denominator

Managed settings:
`model_provider | model_name | retrieval_strategy | top_k | allow_mock_fallback | allow_live | require_offline | theme | reduced_motion`.

Restricted prompt credentials include operator/access tokens, API keys, passwords and secrets. Restricted values must never be copied into a persisted work-run prompt, conversation turn, preference receipt, or generated chat title.

## Run

1. Freeze HEAD, base SHA, changed-file set and GitHub Actions run IDs. Do not substitute a newer or older commit.
2. Validate the persisted preference schema, complete known-key denominator, revision type/range, value types/ranges and SHA-256 value identity. Unknown persisted keys fail closed.
3. Verify locked atomic read-modify-write behavior and optimistic revision conflicts. A stale revision must not partially mutate state.
4. Verify semantic invariants:
   - `allow_live=true` and `require_offline=true` cannot coexist.
   - a positive prompt enabling either live or offline mode explicitly clears the opposing mode.
   - a provider change also selects that provider's default model unless an explicit model name is supplied in the same update.
5. Exercise both mutation surfaces against the same backend state:
   - Settings UI / PATCH `/preferences`.
   - deterministic chat syntax, including natural phrases and `/settings key=value`.
6. For chat settings requests, require evidence that the run bypasses model routing, retrieval and tool execution, persists a user/assistant receipt only after successful mutation, and refreshes the Settings workspace.
7. Run negative fixtures for malformed values, unknown keys, stale revisions, corrupted stored schema, secret-setting natural language and explicit `/settings` secret assignments.
8. Verify new-chat handling does not derive a title from an unaccepted prompt before the message is validated.
9. Render the actual built UI in Chromium and WebKit at desktop and iPhone widths. Check the single scrollable conversation log, person-to-person bubbles, prompt mutation receipt, Settings rehydration, appearance changes, no serious/critical accessibility violations, no horizontal overflow and no console errors.
10. Run backend tests and frontend build at exact HEAD. Compare any failing release gates with the exact `main` baseline before classifying a regression.
11. Preserve inherited failures as BASELINE/BLOCKED; do not relabel them as candidate failures. Conversely, a candidate-specific failure is not excused by unrelated baseline defects.
12. Keep plugin-runtime certification independent unless this branch actually changes the plugin boundary.

## Certification states

- **PASS:** exact-head test/render/build gate passed for the defined row.
- **FAIL:** exact-head candidate-specific assertion failed.
- **BLOCKED:** infrastructure or inherited prerequisite prevents the gate from executing or closing.
- **OPEN:** required evidence has not run or has not been adjudicated.
- **PROVISIONAL:** bounded evidence exists but a stronger required gate remains open.

A green older commit is never evidence for a later head. Script success is not whole-release certification.
