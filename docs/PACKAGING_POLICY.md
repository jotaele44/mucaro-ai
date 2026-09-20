# Packaging Policy

Mucaro AI uses a staged packaging policy. The current release artifact is a
tracked-source ZIP with a JSON manifest. Desktop installers and signed bundles
remain future distribution layers built from the same certified source state.
The distribution handoff manifest is the current operator-facing bridge between
local release artifacts, exact-head receipts, and the still-open hosted
governance lane.

## Current Artifact

The supported artifact format is `source_zip_with_json_manifest_v1`.

Build it from a clean Git worktree:

```bash
PYTHONPATH=.:backend python3 scripts/mucaro.py package build --format text
PYTHONPATH=.:backend python3 scripts/mucaro.py package verify data/exports/packages/MANIFEST.json --require-current-head --format text
python3 scripts/certify_source_artifact.py data/exports/packages/MANIFEST.json
python3 scripts/certify_python_distribution.py
PYTHONPATH=.:backend python3 scripts/mucaro.py package distribution-manifest --save --format text
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
distribution artifact, verifies wheel `METADATA` name/version and console-script
entry point, verifies required sdist source members, writes a Python
distribution manifest, installs the built wheel into a separate clean virtual
environment from a temporary working directory outside the source checkout,
forces an uncached wheel installation, verifies the installed executable
`mucaro` entry point, and proves imported package modules resolve under the
isolated environment's `sys.prefix` rather than the repository. A passing run
writes a `PYTHON_DIST_CERT_OK` receipt for the current Git `HEAD`.

`mucaro package distribution-manifest --save` validates that the exact current
Git `HEAD` has all required local release receipts: `local_release`,
`fresh_clone`, `source_artifact`, `python_dist`, `answer_trace_quality`,
`retrieval_quality`, and `operator_workflow`. It then writes a JSON handoff
manifest under `data/exports/distribution_manifests` with the receipt paths and
file SHA-256 values plus the source archive, source manifest, Python
distribution manifest, wheel, and sdist paths and SHA-256 values.

`mucaro package distribution-manifest --verify MANIFEST` reloads that handoff
manifest, verifies its schema, optionally verifies its own expected SHA-256,
optionally requires its `head_sha` to match the current checkout, and recomputes
all referenced receipt and artifact hashes. This command does not publish,
upload, sign, notarize, or mark hosted governance as passing; it preserves the
local distribution handoff boundary.

## Release Gates

Before a governed release tag, the operator must have:

- clean local worktree
- `CERTIFY_RELEASE_OK`
- `FRESH_CLONE_CERT_OK` for the exact pushed SHA
- `PACKAGE_ARTIFACT_READY` for the same source state
- `PACKAGE_ARTIFACT_VERIFIED` for the generated artifact manifest
- `SOURCE_ARTIFACT_CERT_OK` for the generated artifact manifest
- `DISTRIBUTION_MANIFEST_VERIFIED` for the local handoff manifest
- hosted GitHub Actions success for the exact pushed SHA
- branch protection and tag binding reviewed for the release scope

## Mac App Store Lane

The Mac App Store is a separate distribution lane from both the source release
and Developer ID notarization. Run the source-policy audit with:

```bash
PYTHONPATH=.:backend python3 scripts/mucaro.py package app-store-audit --strict --format text
```

The tracked policy, entitlement template, and privacy template live in
`_architecture/macos_app_store_security_profile.json` and `packaging/macos`.
The audit fails closed on malformed policy, path traversal, missing bundle
metadata, arbitrary transport loads, escaping symlinks, source-bound wrappers,
missing self-containment receipts, dangerous or undeclared entitlements,
invalid signatures, expired or mismatched provisioning profiles, debug
profiles, and development or Developer ID provisioning profiles.

When supplied a signed `.app`, the local audit can report only
`MAC_APP_STORE_CANDIDATE_PROVISIONAL`. Mac App Distribution credentials,
App Store Connect processing, privacy/export declarations, and Apple App Review
for the exact uploaded build remain external evidence gates. Developer ID
notarization belongs to the direct-distribution lane and is not accepted as Mac
App Store proof. The full boundary is defined in
`docs/APPLE_APP_STORE_SECURITY.md`.

## Deferred Distribution Layers

`PYTHON_DIST_CERT_OK` is a production-packaging readiness signal for the Python
wheel/sdist layer. It is not a hosted governance substitute and does not by
itself authorize a governed release tag while hosted GitHub Actions remains
open.

The current policy does not claim a signed installer, notarized macOS app, Mac
App Store acceptance, Windows installer, Linux package, PyPI publication, or
auto-update channel. Those are future distribution layers and must be
certified separately.
