# Three-region navigation contract (proposal implementation)

**Branch origin:** main @ 6f89848afc627097b171ed981afc0af8bdff40fd (2026-09-30).
**Scope:** existing local-first frontend only. No new services, new providers, external icon packages, or speculative pages.
**State:** source changes proposed; browser build and device certification still require exact-head execution.

## Invariants

1. **Primary rail** is the sole canonical destination list. The installed routes are Chat, Sources, Memory, Provenance, and Settings; Background tasks remains an action/drawer, not a duplicate page. Do not advertise Projects, Library, Skills, or Plugins until their native Mucaro pages exist and have independent capability contracts.
2. **Context sidebar** contains only actions/navigation within the selected route. It collapses horizontally and remembers its setting; rail icons remain usable when the context is collapsed. Context links bind directly to existing real workspace panels; they do not manufacture a second workspace implementation.
3. **Main workspace tabs** are ephemeral instances of canonical destinations. Chat supports multiple conversation tabs. Non-chat pages have a single tab per destination. A conversation ID and a UI tab-instance ID must never be conflated. Tabs may be closed without deleting conversations or sources.
4. **No silent draft loss on page switching:** inactive tab components remain mounted, including unfinished chat composer input. Closing a tab intentionally discards that instance's ephemeral UI state, not persistent server content. Ten open tabs is an explicit resource limit; the app refuses an eleventh rather than silently evicting any open tab.
5. **Persistence is untrusted input:** restore accepts only known routes and well-formed conversation IDs, removes duplicates, replaces bad mount IDs, bounds the count, and falls back to a safe new-chat tab. Selected-tab state may only reference a retained tab.
6. **Navigation is local/offline:** all rail glyphs are local inline SVG, layouts are plain CSS/React, and state is stored locally. The existing backend/API retains its own online/offline requirements.
7. **Accessibility:** explicit buttons, names, selection state, tab relationships, arrow/Home/End tab keyboard operation, mobile Escape, inert underlying workspace when mobile navigation is open, reduced-motion behavior, visible focus and coarse-pointer minimum target sizing.
8. **Responsive split:** desktop displays rail + optional sidebar + workspace; phone displays the navigation as an overlay. Landscape phone handling must be tested independently of portrait. Desktop Chromium simulation is not native iOS/WebKit evidence.
9. **Service failures preserve UI state:** previously mounted tabs survive transient local service failure; error state remains visible. Service recovery must not silently duplicate sessions.
10. **Certification is exact-head only:** passing source inspection or unit tests is not rendered QA, mobile WebKit validation, portable-release qualification, or whole-repository certification.

## Targeted tests

- Dependency-free tab reducer tests: allowlisted route identity, invalid persisted tab rejection, duplicated tab elimination, unique mount IDs, selected-tab conservation, tab-cap fail-closed behavior, new-conversation adoption without remount, safe last-tab behavior.
- Existing rendered QA extended: real primary and contextual regions, persistent tab restoration while switching workspaces, selected source and saved-memory UI retention, context collapse retaining primary rail, close-unselected-tab conservation.
- Manual/real-device gates remain OPEN: iPhone portrait and landscape on Safari/WebKit, keyboard avoidance, screen readers, resizing while drawer is open, service outage during active stream, tab recovery after hard restart, large local conversation sets, visual-overflow controls at landscape phone heights.

## Evidence separation and stop rule

Successful GitHub file writes show proposed source only. The known BLK-ACTIONS-001 GitHub runner-admission failure, if it persists on this branch, is an infrastructure BLOCKED state rather than a frontend test PASS or FAIL. Do not change unrelated release baselines or claim certification based on another commit. Reuse frozen source artifacts if runner availability is restored; run the exact-head build, reducer suite, rendered Chromium, rendered WebKit, manual Safari, and final release replay separately.

### Reusable UI regression skill upgrade candidate

The route-registry + bounded tab-state reducer + positive/negative persistent-state fixtures + rendered cross-region assertions constitute a reusable navigation-regression recipe. Promote it into the project's actual Skill Bank only when the target skill's schema and existing scope have been inspected, and after the run has produced its own verified regression evidence. Do not describe this candidate as an already-installed or certified skill.
