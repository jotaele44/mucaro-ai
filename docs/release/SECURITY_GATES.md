# Security gates

`python scripts/certify_security_gates.py` (`make security-gates`) runs the
measurable security checks that INV-016 and MATRIX-014 require. It writes a
`security_gates` release receipt bound to the current head, plus SBOMs and raw
scanner output in `data/exports/security/<head12>/`.

Passing means that each named measurement held. It is **not** a claim that
Mucaro is free of unknown vulnerabilities.

| Gate | What it measures |
| --- | --- |
| `secret_scan` | Secret-shaped strings (API keys, tokens, private keys) in tracked files and in generated evidence (`_architecture/release/evidence`, `data/exports`). |
| `evidence_secret_fields` | Generated JSON evidence stores no value under a secret-like key (`token`, `password`, `api_key`, …) (TGT-024). |
| `static_security_triage` | Ruff's flake8-bandit rules (`--select S`) over `backend/app`, `packaging` and `scripts`. Every finding needs a recorded disposition. New, stale or HIGH/CRITICAL findings fail the gate, and so does any `shell=True`. |
| `dependency_integrity` | Every Python component in the macOS lock is SHA-256-pinned, and every npm package has SHA-512 integrity. |
| `sbom` | CycloneDX 1.5 SBOMs of the shipped Python runtime (from the hashed lock) and of the GUI bundle. React, React DOM and Scheduler are `required`; build tooling is `excluded`. |
| `python_vulnerabilities` | `pip-audit` of the exact locked versions shipped in the app (`--disable-pip`, hash-pinned). |
| `npm_vulnerabilities` | `npm audit --omit=dev` of the frontend. |
| `network_exposure` | Starts the desktop service and reads its listening sockets (`/proc` on Linux, `lsof` on macOS). It must listen on loopback only, with the data directory at mode 0700 and `instance.json` at 0600. |
| `permission_boundaries` | Every tool that reaches the network or changes state requires approval (TGT-025). |
| `xml_amplification_guard` | expat is 2.4.1 or later, so ElementTree refuses billion-laughs and quadratic-blowup amplification. |
| `security_regressions` | Auth boundary, archive and ingestion attacks, XML entity attacks, prompt injection, permissions, tools, the web SSRF policy, the desktop shell, contracts and schema upgrade. |

## Dispositions

`_architecture/release/security/static_security_dispositions_v1.json` classifies
each static finding as CRITICAL, HIGH, MEDIUM, LOW or NOT_APPLICABLE. Each entry
gives a rationale and evidence. It uses one of two scopes:
- **Rule-wide** — S603 (subprocess with argument lists) and S607 (partial
  executable paths).
- **Per occurrence** — the rule, file and exact source line.

When the code changes, affected dispositions go stale and the gate fails until
someone re-triages them. Known vulnerabilities from `pip-audit` or `npm audit`
fail the gate unless `vulnerability_dispositions` records them, with evidence
that the vulnerable path is not applicable.

S607 residual risk: executable names such as `git`, `codesign`, `lsof`,
`tesseract`, `pdftoppm` and `ditto` resolve through the user's `PATH`. Anyone
able to change that `PATH` can already run code as the user, so the risk is
accepted for local tooling.

## Where this feeds

`scripts/certify_release_grade.py` runs these gates as its `security_gates` row.
It records the SBOM hashes and the security receipt in the final release
manifest (MATRIX-020). The release-grade verdict stays `PROVISIONAL` while any
required gate or ledger condition is unmet.
