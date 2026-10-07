# Security

Mucaro handles local files and runs approved tools. Treat its operator token as a
credential with access to the current user's data. Keep local deployments on
loopback. Phone or production access requires a strong operator token; use HTTPS
when requests cross an untrusted network. Configure explicit trusted CORS origins.

The server rejects untrusted browser origins before routing and authenticates
write requests before parsing their bodies. Tokenless loopback requests require a
loopback Host header to resist DNS rebinding. Same-origin phone requests still
require the operator token for protected routes. CLI clients may omit Origin.

Archive safety rejects traversal, Windows device/stream names, portable filename
aliases, special files, executable payloads, and configured resource-limit
violations. Extraction requires a new destination and publishes its complete tree
only after successful validation. Existing files and links are never overwritten.

Run `make security-gates` for secret scanning, static security triage, lock
integrity, SBOMs, dependency audits across the packaged Python platforms, frontend
auditing, network exposure, permission boundaries, and regression tests. Run
`make test` for the complete backend suite. Failed or incomplete vulnerability
scanner reports cannot certify a clean result. CI actions are pinned to commits,
and Dependabot proposes updates to the action pins and npm dependencies.

Passing checks measures these controls at the tested revision. It does not prove
the absence of unknown vulnerabilities, validate every deployment configuration,
or replace platform-specific release verification. macOS packaging, signing,
notarization, and real browser/container certification retain their release gates.

Report suspected vulnerabilities privately to the repository owner with the
affected revision, reproduction steps, and impact. Do not include live tokens,
private source documents, or other credentials in reports or test fixtures.
