# Native arm64 portable-runtime source candidate — 2026-09-29

**State:** SOURCE_AND_LOCK_IDENTITY_VERIFIED  
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


## Same-day upstream freshness adjudication

A 2026-09-29 source-head check found a newer immutable upstream release,
`20260929`, published after this vector originally bound `20260901`. The
same CPython 3.13.15 install-only architecture pair exists there:

| Release | Architecture | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `20260901` | arm64 / aarch64 | `25293188` | `b9054a9d3d54f4cb5573d44907fddb29874b08909bde73f29f2868cf872223ee` |
| `20260901` | x86_64 | `25037769` | `49f0d97f506b855eed60b74a8ac138595c5b39799a6aa5e0d7ca8abe1019a4d4` |
| `20260929` | arm64 / aarch64 | `25334687` | `003d459a75ff6949a6590812b1e02a65e849b5b2a9f47c421010138ec643a11c` |
| `20260929` | x86_64 | `25052332` | `54e9da9571fd13655096dbfbd2f18a04ff90fe0051c135cb6547d6e76c3db507` |

**Decision for this bounded certification vector: retain `20260901`.** The
existing governed x86_64 manifest and packaged-lifecycle evidence are already
bound to `20260901`; using the matching arm64 asset isolates architecture
rather than changing both architecture and upstream release at once. Adopting
`20260929` would be a separate two-architecture runtime-refresh vector and
would require renewed x86_64 source/runtime/lifecycle evidence as well. This is
a reproducibility/parity decision, not a claim that `20260901` is newer,
safer, or otherwise superior.

The source choice therefore does not float with upstream `latest`. A future
runtime-refresh vector may deliberately move both architectures together after
this arm64 parity lane is closed.

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

## Executed source/lock closure — 2026-10-16

Exact-head hosted certification independently retrieved the frozen `20260901`
arm64 asset and reproduced the source/lock evidence twice.

- archive bytes: `25293188`
- archive SHA-256: `b9054a9d3d54f4cb5573d44907fddb29874b08909bde73f29f2868cf872223ee`
- archive root: `python`
- members: `1654`
- regular files: `1646`
- symlinks: `8`
- total regular bytes: `65443266`
- logical-members SHA-256: `74f3a0af3f4b7d02ed1dec4713ffa2a65c9fac11c7bab3bf95a23235af4ec859`
- payload-multiset SHA-256: `fa7a654dc80b75c13ed842026b97035e7e52eadc4aeea7a262bfdcc91e6978c0`
- dependency lock: `packaging/runtime/requirements-macos-arm64.lock`
- lock bytes: `44071`
- lock SHA-256: `cf7cea35480de5f416b4cf960ec0892954b97c7111e1665a40daa49b4f4fa457`
- resolver: `uv==0.10.0`, Python 3.13, `aarch64-apple-darwin`,
  wheel-only, hashes, `MACOSX_DEPLOYMENT_TARGET=14.0`

The initial resolver attempt at uv's default Darwin 13.0 floor correctly
rejected `pyproj==3.7.2`. The rerun used the already-declared Mucaro minimum
macOS 14.0 and changed no project dependency version.

`packaging/runtime/macos-arm64.json` now binds the independently observed
geometry/hashes and dedicated arm64 lock.

## What remains OPEN

1. **Apple-Silicon materialization.** Execute the normal portable-runtime
   materializer on an Apple-Silicon macOS 14+ runner and require exact source
   bytes/hash, archive identity, locked-distribution installation, required
   imports, `platform=darwin`, `machine=arm64`, Python 3.13.15, no host
   user-site leakage, and a frozen runtime-tree/provenance receipt.
2. **Packaged lifecycle.** Build the portable app from that verified runtime and
   execute install, launch, representative workflow, post-launch bundle
   immutability, distinct-version rollback and reinstall on Apple Silicon.
3. **Separate gates.** Clean-host execution, physical-input evidence,
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

This record now closes source discovery, independent BYTE identity, archive
logical identity, and the dedicated arm64 dependency-lock/manifest boundary.
It does not certify runtime materialization, Apple-Silicon runtime execution,
the packaged lifecycle, clean-host execution, physical input, signing,
notarization, or the release lane.
