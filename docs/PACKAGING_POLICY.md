# Packaging Policy

Mucaro AI uses a staged packaging policy. The current release artifact is a
tracked-source ZIP with a JSON manifest. Desktop installers and signed bundles
remain future distribution layers built from the same certified source state.

## Current Artifact

The supported artifact format is `source_zip_with_json_manifest_v1`.

Build it from a clean Git worktree:

```bash
PYTHONPATH=.:backend python3 scripts/mucaro.py package build --format text
```

The command writes generated files under `data/exports/packages`, which is
ignored by Git.

The artifact contains only paths returned by `git ls-files`. It excludes local
runtime state, secrets, virtual environments, node modules, processed data,
indexes, caches, build output, and generated exports unless those files are
explicitly tracked in Git.

## Required Receipts

Each artifact manifest records:

- package name and version
- Git `HEAD` SHA
- source ZIP path, size, and SHA-256
- manifest path and SHA-256
- tracked member count
- per-member path, size, and SHA-256

Release and fresh-clone certification scripts also write exact-HEAD JSON
receipts under `data/exports/release_receipts`. The `mucaro release state`
command uses only receipts whose `head_sha` matches the current Git `HEAD`;
older receipts remain history, not current certification proof.

## Release Gates

Before a governed release tag, the operator must have:

- clean local worktree
- `CERTIFY_RELEASE_OK`
- `FRESH_CLONE_CERT_OK` for the exact pushed SHA
- `PACKAGE_ARTIFACT_READY` for the same source state
- hosted GitHub Actions success for the exact pushed SHA
- branch protection and tag binding reviewed for the release scope

## Deferred Distribution Layers

The current policy does not claim a signed installer, notarized macOS app,
Windows installer, Linux package, PyPI package, or auto-update channel.
Those are future packaging layers and must be certified separately.
