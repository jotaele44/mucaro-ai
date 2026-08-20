# Mucaro AI Baseline Certification

Date: 2026-08-20

## Scope

This certification covers the committed `mucaro-ai` source tree intended for the
`v0.1.0-local-prototype` baseline tag.

Included:

- Backend source, schemas, tests, scripts, docs, sample inputs, frontend source,
  package lockfile, and CLI packaging metadata.
- CLI surface: `mucaro ask`, `mucaro ingest`, `mucaro plan`, `mucaro validate`,
  and `mucaro serve`.

Excluded by design:

- `.env`, virtual environments, node modules, frontend build output, processed
  manifests, SQLite databases, answer exports, local indexes, pycache, Finder
  metadata, agent metadata, and cloned reference repositories.

## Certification Gates

| Gate | Result |
|---|---|
| GitHub repo visibility | PASS: private |
| Local/remote branch alignment | PASS: `main` tracks `origin/main` |
| Prior prototype-name residue | PASS: no committed hits for prior prototype identifiers |
| Secret-pattern scan | PASS: no committed secret material found; `.env.example` model name is an allowed non-secret example |
| Large committed files | PASS: no committed file over 1 MB |
| Fresh virtualenv install | PASS: editable install succeeded in `/tmp/mucaro-ai-cert-venv` |
| Python preflight | PASS: `PREFLIGHT_OK` |
| Backend tests | PASS: 13 tests passed |
| JSON schema validation | PASS: `JSON_OK` |
| CLI smoke | PASS: `INGEST_FOLDER_OK` and `CITATION_OK` |
| Frontend install | PASS: `npm ci` completed |
| Frontend security audit | PASS: `npm audit --audit-level=moderate` found 0 vulnerabilities |
| Frontend build | PASS: `npm run build` succeeded |
| Release check | PASS: `RELEASE_CHECK_OK` |

## Notes

- A prior global-environment editable install reported a dependency conflict with
  another local package. The fresh virtualenv certification run isolated Mucaro AI
  and passed.
- Dependency versions are pinned in `pyproject.toml`, `backend/requirements.txt`,
  and `frontend/package-lock.json`. The npm lockfile was updated to clear
  transitive audit findings before certification.
- This is a bounded certification of the current local prototype baseline, not a
  production-readiness certification.

## Status

CERTIFIED for `v0.1.0-local-prototype` baseline tagging.
