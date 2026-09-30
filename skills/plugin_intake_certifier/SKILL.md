---
name: plugin-intake-certifier
version: 0.1.0
---
# Offline plugin intake certification (repo-local candidate)

Goal: certify an inert plugin-package intake boundary before admitting executable extensions. This is a source candidate, not a globally installed skill.

1. Freeze the exact Git commit and plugin ZIP bytes/SHA-256; preserve all member path, decompressed size and SHA-256 rows separately from ZIP hash.
2. Verify authoritative manifest ID+version as declared claims, not external registry identity. Reject collision for the same ID+version with different content; maintain exact package manifests for each version.
3. Exercise all positive/negative plugin fixtures: malformed ZIP/JSON, duplicate key, traversal, executable, casefold collision, undeclared member, capability escalation, tampered archive/catalog, size/ratio overflow and repeat staging.
4. Confirm zero extension execution and zero external network I/O during catalog staging. Runtime or permission assertions need dedicated instrumentation before promotion.
5. Record Python test count and failures on the **exact final branch head**, then on the **exact eventual merge/release head**. Never reuse stale CI or call zero-step runners a code failure.
6. Certification states: source candidate, isolated-test PASS, full-repo OPEN/BLOCKED, GUI OPEN, native-release OPEN; no transitive PASS between states.
