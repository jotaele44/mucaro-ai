# Security

Mucaro handles local files and runs approved tools. Treat its operator token as a
credential with access to the current user's data. Keep local deployments on
loopback. Phone or production access requires a strong operator token; use HTTPS
when requests cross an untrusted network. Configure explicit trusted CORS origins.

When an operator token is configured, protected routes require it, including
same-origin phone requests (`docs/PHONE_ACCESS.md`). CORS only allows the
configured origins.

Archive safety rejects path traversal, absolute and backslash paths, symlinks,
encrypted or duplicated members, executable payloads, and configured size, ratio,
and member-count limit violations.

Run `make security-gates` for secret scanning, static security triage, lock
integrity, SBOMs, Python and frontend dependency audits, network exposure,
permission boundaries, and regression tests (`docs/release/SECURITY_GATES.md`).
Run `make test` for the complete backend suite. Dependabot proposes weekly updates
to the GitHub Actions versions and the npm dependencies.

Passing checks measures these controls at the tested revision. It does not prove
the absence of unknown vulnerabilities, validate every deployment configuration,
or replace platform-specific release verification. macOS packaging, signing,
notarization, and real browser/container certification retain their release gates.

Report suspected vulnerabilities privately to the repository owner with the
affected revision, reproduction steps, and impact. Do not include live tokens,
private source documents, or other credentials in reports or test fixtures.
