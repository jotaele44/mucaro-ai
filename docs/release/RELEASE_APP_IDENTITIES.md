# macOS application identities

| Artifact | Purpose | Bundle name | Bundle identifier | State |
| --- | --- | --- | --- | --- |
| Release build | Installed, frozen Apple Silicon app | `Mucaro AI.app` | `ai.mucaro.app` | Implemented source; signing and final certification OPEN |
| Checkout setup launcher | Sets up the source checkout and opens its own window | `Mucaro AI Setup.app` | `ai.mucaro.launcher` | Candidate in PR #133; not a release artifact |
| Portable Intel lane | Embedded x86_64 runtime experiment | `Mucaro AI Portable.app` | `ai.mucaro.portable` | Historical compatibility lane; cannot close native arm64 requirement |

These are three distinct artifact identities. A similar user-facing purpose or icon
does not equate their runtime, signature, installation, upgrade, or release
certification. The Setup.app is bound to its checkout and must not be renamed
to the release bundle. Keep the bundle identifiers disjoint so macOS LaunchServices
and installation receipts cannot silently select the other application.

Before merging PR #133, verify its Info.plist identity and its LaunchServices
receipt against the current release build. If the release bundle identifier,
name, or distribution class changes, amend this mapping and add a regression
gate. PR #126 is closed without merge and does not create a Windows release.
