# Múcaro offline plugin intake v1 — source candidate

**Scope:** metadata-validated and hash-frozen ZIP packages, entirely local. This is **not** executable extension support and has no network side effects. Plugins are always `STAGED_DISABLED`; catalog status never implies a working sandbox or execution approval. No existing tool/skill registry, service or GUI is changed by this tranche.

`mucaro-plugin.json` at the archive root defines exact required fields: `host_api` (`mucaro-plugin/1`), reverse-DNS `plugin_id`, `major.minor.patch` `version`, bounded `name` and `description`, **empty** `capabilities` and explicit `contributions`. v1 accepts only declared Markdown/text references, Markdown prompt templates and JSON schemas, but does not automatically inject them into a model context. Every non-directory member must be declared; ZIP scripts/binaries/executables are refused.

**Guards:** bounded byte intake, existing ZIP-safety inspector, casefold and NFC path collision protection, exact member path/size/SHA-256 digest, exact ZIP SHA-256, no overwrite of an existing `(plugin_id, version)` with new bytes, idempotent identical restaging, locked atomic catalog mutation, on-read frozen-archive integrity status, corrupted registry fail-closed. No metadata matching establishes canonical external plugin identity.

**Later dependency gates:** a dedicated runtime isolation adapter (platform-specific guarantees documented, not claimed), operator opt-in/approve/disable/revoke, per-call policy/directory/network enforcement, prompt-injection trust boundary, auditable API/UI lifecycle, native desktop/mobile packaging and exact-release-head replay. Do not merge with unfinished Work state or draft GUI without conflict inspection.

**Test boundary:** `backend/tests/test_plugin_intake.py` covers initial positive/negative behaviors. Passing an isolated harness does not certify full-repo, rendered UI, Apple/WebKit or packaged runtimes.

**Local operator entrypoint (same inert boundary):** `python -m backend.app.plugin_cli list` enumerates staged packages and rechecks archive integrity. `python -m backend.app.plugin_cli stage ./package.zip --expected-sha256 <digest>` checks existing directory permission **before opening the source ZIP**, reads at most the package limit, and stages with an explicit local CLI receipt. There is no automatic execution or network request. This entrypoint is for local operators; graphical/plugin-API integration is still OPEN.
