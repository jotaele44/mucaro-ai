# Packaging Policy

Mucaro AI uses a staged packaging policy. The current release artifact is a
tracked-source ZIP with a JSON manifest. Desktop installers and signed bundles
remain future distribution layers built from the same certified source state.

## Current Artifact

The supported artifact format is `source_zip_with_json_manifest_v1`.

Build it from a clean Git worktree:

```bash
PYTHONPATH=.:backend python3 scripts/mucaro.py package build --format text
PYTHONPATH=.:backend python3 scripts/mucaro.py package verify data/exports/packages/MANIFEST.json --require-current-head --format text
python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json
python3 scripts/certify_python_distribution.py
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
Use `mucaro release receipts --current-head --format text` to audit matching
receipts and `mucaro release receipt RECEIPT_ID --format json` to inspect a
specific receipt with its computed file SHA-256. Local release receipts include
the package artifact payload from `mucaro package build --format json`, linking
the certification receipt to the source ZIP SHA-256, manifest SHA-256, and
member count produced for the same source state.

`mucaro package verify MANIFEST` validates an existing artifact after it has
been written. Verification checks that the manifest is valid JSON, the artifact
format and package identity match the supported source-ZIP policy, the archive
path exists, the archive size and SHA-256 match the manifest, all ZIP member
paths are safe, duplicate archive members are absent, the archive member set
exactly matches the manifest, and every member's size and SHA-256 match. Use
`--require-current-head` before release or distribution decisions so the
manifest's `head_sha` must match the current Git checkout.

`scripts/certify_source_artifact.py MANIFEST` validates installability from the
source ZIP itself. It verifies the manifest against the current Git `HEAD`,
extracts the archive to a temporary directory, creates a fresh virtual
environment, installs the extracted package with development dependencies,
installs frontend dependencies, runs `scripts/release_check.py`, checks the
installed `mucaro` entry point, and runs the desktop package self-test. A
passing run writes a `SOURCE_ARTIFACT_CERT_OK` receipt whose `head_sha` is taken
from the verified manifest.

`scripts/certify_python_distribution.py` validates the standard Python
distribution layer. It builds both wheel and sdist artifacts under
`data/exports/python_dist`, records SHA-256 and member counts for each
distribution artifact, installs the built wheel into a separate clean virtual
environment, verifies the installed `mucaro` entry point, and imports installed
package modules. A passing run writes a `PYTHON_DIST_CERT_OK` receipt for the
current Git `HEAD`.

## Release Gates

Before a governed release tag, the operator must have:

- clean local worktree
- `CERTIFY_RELEASE_OK`
- `FRESH_CLONE_CERT_OK` for the exact pushed SHA
- `PACKAGE_ARTIFACT_READY` for the same source state
- `PACKAGE_ARTIFACT_VERIFIED` for the generated artifact manifest
- `SOURCE_ARTIFACT_CERT_OK` for the generated artifact manifest
- hosted GitHub Actions success for the exact pushed SHA
- branch protection and tag binding reviewed for the release scope

## Deferred Distribution Layers

`PYTHON_DIST_CERT_OK` is a production-packaging readiness signal for the Python
wheel/sdist layer. It is not a hosted governance substitute and does not by
itself authorize a governed release tag while hosted GitHub Actions remains
open.

The current policy does not claim a signed installer, notarized macOS app,
Windows installer, Linux package, PyPI publication, or auto-update channel.
Those are future distribution layers and must be certified separately.
