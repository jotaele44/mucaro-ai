# Native arm64 portable-runtime source candidate — 2026-09-29

**State:** CANDIDATE_NOT_IDENTITY  
**Retrieval UTC:** 2026-09-29T22:04:11Z  
**Release target:** native Apple Silicon / arm64 portable embedded-runtime lane  
**Binding decision:** `docs/RELEASE_SCOPE_2026-09-29.md` requires a native arm64 embedded runtime. Intel/x86_64 evidence is compatibility evidence only.

## Authoritative upstream asset binding

The candidate source is an asset published by the upstream
`astral-sh/python-build-standalone` release tagged `20260901`.

| Field | Frozen value |
| --- | --- |
| Project | `astral-sh/python-build-standalone` |
| Release tag | `20260901` |
| Asset | `cpython-3.13.15+20260901-aarch64-apple-darwin-install_only.tar.gz` |
| Asset bytes | `25293188` |
| Asset SHA-256 | `b9054a9d3d54f4cb5573d44907fddb29874b08909bde73f29f2868cf872223ee` |
| Python | `3.13.15` |
| Platform | `darwin` |
| Machine | `arm64` / upstream asset token `aarch64` |
| Upstream release metadata | GitHub Releases API for tag `20260901` |
| Asset URL | `https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.13.15%2B20260901-aarch64-apple-darwin-install_only.tar.gz` |

The byte count and digest above are upstream GitHub asset metadata. They bind the
candidate asset identity, but they do **not** by themselves prove that this
repository independently downloaded those bytes.

## What is closed

- A matching CPython 3.13.15 native Apple-Silicon upstream asset exists.
- Its upstream asset name, release tag, byte count and SHA-256 are bound.
- Its Python/platform/machine target matches the declared release architecture.
- The existing portable CLI can accept an explicit runtime manifest.
- The accompanying feature branch removes the remaining output-name collision:
  an explicit `macos-arm64.json` manifest defaults to an architecture-specific
  `macos-arm64` output directory instead of `macos-x86_64`.

## Restartable archive-freeze path

The feature branch adds a fail-closed archive inspector to the supported CLI.
It reads a supplied `.tar.gz` without extracting it and emits:

- exact archive bytes and SHA-256;
- single archive root;
- member / regular-file / symlink counts;
- total uncompressed regular bytes;
- a canonical path-sensitive logical-member SHA-256 built from member path,
  type, regular-file mode, uncompressed size/content hash, and symlink target;
- a canonical path-insensitive payload-multiset SHA-256 built from
  `{uncompressed bytes, content SHA-256}` records with duplicates preserved.

Inspection rejects multiple roots, path traversal, duplicate members,
unsupported member types, escaping/broken/cyclic symlinks, read-size
mismatches, and archive geometry above explicit inspection bounds. Before any
regular-file payload is hashed, the inspector rejects member count or declared
regular-byte totals above its ceiling. Its standalone CLI defaults are 2,000
members and 70,000,000 regular bytes, inherited from the existing governed
x86_64 manifest as a safety baseline only; they are **not** observations of the
arm64 asset. The CLI exposes explicit inspection-only overrides, while
manifest-driven extraction always uses that manifest's own maxima. Future
arm64 manifests may bind the two logical hashes in
`archive_policy`; extraction verifies them when present while retaining
backward compatibility with the current x86_64 manifest.

The inspector is an evidence generator, not identity by filename: an explicit
manifest filename only selects a convenient default output directory. An
explicit `--output-dir` remains authoritative and runtime identity continues
to be established by frozen source bytes, manifest target fields, dependency
lock, runtime probe, and tree receipt.

## What remains OPEN

1. **Independent archive-byte retrieval.** Re-download the upstream asset on a
   permitted execution surface and require observed byte count and SHA-256 to
   equal the frozen metadata above.
2. **Archive logical geometry.** Compute and freeze:
   - member count;
   - regular-file count;
   - symlink count;
   - total uncompressed regular bytes;
   - archive root;
   - regular-file modes inside the logical-member identity;
   - path-sensitive logical-member SHA-256;
   - path-insensitive payload-multiset SHA-256;
   - safe symlink-target closure.
   Do not copy the x86_64 archive-policy counts or treat the standalone
   inspection safety ceilings as observed arm64 geometry.
3. **Dedicated portable dependency lock.** An off-branch generation attempt on
   2026-09-29 with uv 0.10.0 could not reach PyPI because the execution
   environment had no DNS; no lock file was produced and no dependency
   incompatibility was observed. Generate
   `packaging/runtime/requirements-macos-arm64.lock` from the current
   `pyproject.toml` for **Python 3.13 + aarch64-apple-darwin + wheel-only +
   hashes**. The existing `packaging/macos/requirements-macos-arm64.lock` is
   NONCOMPARABLE for this purpose because it was resolved for Python 3.11 and
   includes native-app build dependencies such as PyInstaller and pywebview.
4. **Manifest promotion.** Only after items 1–3 close, create
   `packaging/runtime/macos-arm64.json` using independently observed archive
   geometry and the dedicated portable lock.
5. **Materialization.** Execute the normal portable-runtime materializer on an
   Apple-Silicon runner and require:
   - exact source bytes/hash;
   - exact archive geometry;
   - locked-distribution installation;
   - required imports;
   - `platform=darwin`;
   - `machine=arm64`;
   - `python_version=3.13.15`;
   - no host user-site leakage;
   - frozen runtime tree hash and provenance receipt.
6. **Packaged lifecycle.** Build the portable app from that verified runtime and
   execute install, launch, representative workflow, post-launch bundle
   immutability, distinct-version rollback and reinstall on Apple Silicon.
7. **Separate gates.** Clean-host execution, physical-input evidence,
   Developer ID signing and notarization remain independent.

## Falsification rules

The candidate must fail closed if any of the following occurs:

- observed source bytes or SHA-256 differ;
- upstream asset redirects to a different payload without the frozen digest;
- archive counts differ from the future frozen arm64 manifest;
- unsupported archive member types or escaping symlinks appear;
- dependency lock is generated for another Python version/platform;
- a required direct dependency differs from `pyproject.toml`;
- runtime probe reports `x86_64` or any machine other than native arm64;
- installed distributions differ from the dedicated arm64 lock;
- runtime or app member manifests drift after launch;
- an Intel/Rosetta run is presented as native arm64 certification.

## Certification boundary

This record closes **source discovery and authoritative asset metadata binding
only**. It does not certify BYTE identity, archive logical identity, dependency
closure, runtime materialization, Apple-Silicon execution, clean-host
execution, signing, notarization, or the release lane.
