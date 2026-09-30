# Release scope and certification decisions (2026-09-29)

Decision authority: project owner affirmation in the active session.

| Surface | Binding scope | Certification consequence |
| --- | --- | --- |
| Packaged desktop | Apple Silicon macOS | Run install, launch, workflow, recovery, signing and notarization gates on the declared architecture. Intel-only evidence does not certify this target. |
| Portable desktop lane | Native arm64 embedded runtime | Retain the two-lane requirement. Freeze an independently verified arm64 runtime source and test its packaged app on Apple Silicon. x86_64 under Rosetta is a separate compatibility observation. |
| Mobile | Responsive web UI at iPhone width | Native iOS distribution is outside this release. Chromium device emulation is provisional for iOS Safari behavior. Run WebKit/Safari on an actual iPhone for keyboard, actions, copy/read-aloud and linked PR navigation. |
| Windows and Linux | Source setup documentation only | No packaged or signed desktop release claim. |
| Phone access to local Mac | Not part of the responsive UI evidence | Do not imply a LAN, TLS, PWA or native shipping channel from viewport tests. |

## Release gate

Freeze one final commit after merging the relevant PRs. Re-run all applicable
evidence on that commit. Every receipt must bind the full commit SHA,
architecture, OS/browser, app version and artifact hash. A changed SHA makes
prior execution historical evidence. Keep feature closure, hardware execution,
credentialed signing, and physical double-click as distinct rows.

The Workbench Specification remains an OPEN input. It has not been supplied or
formally removed. Anthropic and Brave keys, Apple signing identity and any
physical-device observation remain external inputs; no empty value passes as
evidence.

Do not mark a prepared development runner as a clean host. Build artifacts on
the build runner and execute a separately provisioned host/runner lifecycle
without Python, Node or repository development dependencies installed by the
certification job. A runner launch is recorded as a runner launch. A physical
double-click claim requires physical input evidence.
