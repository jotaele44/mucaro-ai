# Release scope and certification decisions (2026-09-29)

Decision authority: project owner affirmation in the active session.

| Surface | Binding scope | Certification consequence |
| --- | --- | --- |
| Packaged desktop | Apple Silicon macOS | Run install, launch, workflow, recovery, signing and notarization gates on the declared architecture. Intel-only evidence does not certify this target. |
| Portable desktop lane | Native arm64 embedded runtime | Retain the two-lane requirement. Freeze an independently verified arm64 runtime source and test its packaged app on Apple Silicon. x86_64 under Rosetta is a separate compatibility observation. |
| Mobile | Responsive web UI at iPhone width | Native iOS distribution is outside this release. Chromium device emulation is provisional for iOS Safari behavior. Run WebKit/Safari on an actual iPhone for keyboard, actions, copy/read-aloud and linked PR navigation. |
| Windows and Linux | Source setup documentation only | No packaged or signed desktop release claim. |
| Phone access to local Mac | Adopted as scope delta `SD-012` (amended 2026-10-01, project owner affirmation in the active session): a phone on the same private network or VPN reaches the source launcher's service with an access token, optionally over TLS. Status PROVISIONAL; see `docs/PHONE_ACCESS.md`. Not part of the responsive UI evidence. | Viewport tests do not prove it. `SD-012` carries its own gates and needs a physical iPhone session before it can pass. Do not imply an installable web app, a native client (`SD-010`) or serving from the packaged app, which stays loopback-only. |

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
