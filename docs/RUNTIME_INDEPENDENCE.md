# Mucaro Runtime Independence

Baseline source: `f258b8747f68117394aebd357bc6e6fbf47613df`

## Goal

The installed application must require **zero mandatory external runtime work**
except operations whose purpose is inherently online and explicitly authorized by
the operator, such as remote model APIs and governed web fetch/search/render.

This is stricter than "works offline after setup." A prepared release package
must be installable and launchable on its supported platform with the network
disabled, without requiring Python, Node.js, Git, a package manager, a hosted
backend, Floot, GitHub, or any other developer tool on the host.

## Dependency classes

| Dependency | Current state | Independence target |
| --- | --- | --- |
| Python runtime + core Python libraries | Embedded in some packaged lanes; downloaded by source one-click setup | Bundle per release platform |
| Node/npm/Vite | Used to build the frontend and by source setup | Build-time only; never required on an installed release |
| Runtime source identity | Previously invoked host `git rev-parse HEAD` | Read bound source identity or local VCS metadata without launching Git |
| Git-backed coding tools | Require host Git today | Bundle Git or replace the Git-backed adapter on supported platforms |
| Tesseract + Poppler | Optional host binaries | Bundle optional OCR components where platform/licensing permit |
| Playwright + Chromium | Optional rendered-browser runtime | Bundle an optional governed rendering engine on supported platforms |
| Local LLM | External loopback server today | Optional bundled model engine/model pack, while retaining user-selected loopback engines |
| Remote LLM APIs | Explicit remote provider | Keep as an explicit network exception |
| Web fetch/search/render | Explicit public-network operations | Keep behind the governed network capability gate |
| GitHub/hosted CI | Development and release governance | Never required by the installed application |
| Signing/notarization/update delivery | Distribution infrastructure | Never required for ordinary offline execution after install |

## Runtime rules

1. Local conversation, memory, sources, retrieval, provenance, planning,
   exports, and supported GIS operations must not require the public network.
2. Loopback traffic used by Mucaro's own local service is local IPC, not an
   external-network dependency.
3. Remote model providers and web tools must fail closed when offline. They
   must never silently substitute a mock/local result while claiming the
   requested remote operation succeeded.
4. Optional local engines must be detectable before invocation. A missing
   optional component is explicit capability residue, not an application
   startup failure.
5. Release artifacts must carry all mandatory runtime libraries and data.
   A user must not need pip, npm, Homebrew, Git, Docker, system Python, system
   Node, or a compiler to run the declared offline core.
6. Development dependencies may remain external because they are not part of
   the installed-runtime denominator.

## Platform rule

"Any device" means one common product contract with platform-specific
distributions and execution adapters. It does **not** mean one identical binary
or identical low-level execution mechanism across macOS, Windows, Linux, iOS,
and Android.

Capabilities that cannot satisfy a platform's sandbox or executable-code rules
must be marked unsupported or replaced by a platform-native adapter rather than
quietly requiring an external helper.

## Certification gate

A platform earns `OFFLINE_INDEPENDENT` only when all of the following pass on
a clean device or equivalently controlled clean-machine fixture:

- install from a complete prepared artifact with public networking disabled;
- first launch succeeds without dependency acquisition;
- no host Python, Node, Git, package manager, compiler, container runtime, or
  hosted service is required by the declared offline core;
- local source ingestion, retrieval, conversation, memory, planning,
  provenance, exports, and all other declared offline capabilities pass;
- local data survives restart and reinstall according to the release contract;
- all attempted external connections are either absent or map to an explicit
  operator-authorized network capability;
- missing optional components are reported accurately;
- negative controls prove remote APIs/fetches fail closed while offline;
- package hashes, dependency manifests, and receipts bind the exact tested
  artifact.

Until those gates pass, the correct state is `PROVISIONAL`, not independent.

## Current first tranche

The first implementation tranche:

- removes the host Git executable from runtime-identity resolution;
- adds direct `.git` metadata and bound-build-identity resolution;
- exposes hidden host requirements for Git-backed coding tools in the offline
  capability manifest;
- classifies bundle candidates, optional local engines, and explicit network
  exceptions separately;
- freezes the machine-readable contract in
  `_architecture/runtime_independence_v1.json`.

It does not yet claim that Git-backed coding, OCR, rendered browsing, local LLM
inference, or every platform package is self-contained.
