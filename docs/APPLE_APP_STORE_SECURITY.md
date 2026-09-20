# Mac App Store security and distribution policy

## State

`OPEN`

This policy defines the Mac App Store lane for Mucaro AI. It does not convert
the current local launcher, portable bundle, Developer ID signing verifier, or
notarization evidence into a Store-ready artifact.

## Distribution lanes

| Lane | Required security evidence | Current state |
|---|---|---|
| Local development wrapper | source-bound launcher receipt and local tests | `PASS` for local development only |
| Direct macOS distribution | self-contained bundle, Developer ID signing, hardened runtime, notarization, Gatekeeper, clean-machine install | `OPEN` |
| Mac App Store | self-contained bundle, App Sandbox, least-privilege entitlements, privacy/data disclosures, Mac App Distribution signing and provisioning, App Store Connect processing, App Review | `OPEN` |

Notarization is part of the direct-distribution lane. Apple states that Store
submission already includes equivalent security checks, so a notarization
receipt is not accepted as proof of Mac App Store readiness.

## Binding local gates

The machine-readable profile is
`_architecture/macos_app_store_security_profile.json`. The command
`mucaro package app-store-audit` validates that profile and the source templates.
Pass `--app PATH` to inspect a candidate bundle on macOS.

A local candidate must satisfy all of the following:

- be self-contained and never point back to a repository checkout or external Python installation;
- include `Contents/Resources/mucaro-app-store-receipt.json` with the governed
  self-contained Store-candidate scope, embedded-runtime origin, bundle ID, and
  source-head binding;
- include `com.apple.security.app-sandbox=true`;
- use only the entitlements declared by the machine-readable profile;
- omit debug, JIT, unsigned executable memory, disabled library validation,
  executable user-file access, and temporary-exception entitlements;
- access external files through system open/save panels and user-selected
  read/write scope;
- persist external file grants only through security-scoped bookmarks, including
  stale-bookmark renewal and balanced start/stop access;
- include a parseable `PrivacyInfo.xcprivacy` whose contents match the final data
  and third-party SDK inventory;
- include a nonexpired App Store distribution provisioning profile whose team,
  application identifier, sandbox policy, and nondebug posture match the
  verifiable distribution code signature and exact bundle identifier;
- keep App Transport Security enabled and reject arbitrary transport loads;
- contain no symlink that resolves outside the application bundle;
- keep private-content network transmission behind explicit user permission and
  disclose the applicable data behavior accurately;
- remain bound to locked dependencies, release SBOM, malware/vulnerability
  scans, regression tests, and exact-build hashes.

### Candidate supply-chain evidence

The signed candidate must carry three regular JSON files under
`Contents/Resources/Security`: a CycloneDX SBOM, a vulnerability-scan receipt,
and a malware-scan receipt. Their exact paths and SHA-256 values are recorded in
`mucaro-app-store-receipt.json`, so substitution or reuse of different evidence
fails the local audit and the bundle signature seals the resulting files.

The vulnerability and malware receipts must bind to the same source commit,
bundle identifier, application version, and executable SHA-256 as the audited
candidate. Scanner identity, scanner version, vulnerability-database or malware
signature update time, scan time, and closed result counts are mandatory. Scans
older than the profile's 72-hour limit fail. Vulnerability severity counts must
close arithmetically, and the Store candidate permits no critical, high, or
known patchable vulnerability and no malware detection or scan error.

The versioned receipt contracts live under `_architecture/schemas`. These gates
prove only the supplied evidence and exact candidate binding. They do not prove
that an unknown vulnerability or undetectable malicious payload cannot exist,
and they do not replace Apple's processing or review.
Evidence is size-bounded before parsing: 50 MiB for the SBOM and 10 MiB for
each scanner receipt under the current profile.

## Coding and local-server boundary

The standard Store profile denies downloaded executable code, unreviewed
plug-ins, and arbitrary shell access. Mucaro's broader coding engine may not be
enabled in the Store build until its behavior is shown to comply with Apple's
executable-code rules for the app's reviewed purpose. User-authored or generated
source is not automatically exempt.

The current GUI launcher starts a loopback HTTP server. The minimal entitlement
template intentionally omits `com.apple.security.network.server`. Before GUI
work begins, the Store build must either replace that boundary with an
in-process or sandbox-compatible IPC design, or add the server entitlement only
after a documented threat model and App Review justification. Local binding,
token checks, and CORS controls remain required even if the entitlement is
approved.

## External gates

The following cannot be certified from source code or a local unsigned build:

- Mac App Distribution certificate and matching App Store provisioning profile;
- App Store Connect upload validation and processing;
- export-compliance and privacy answers for the final feature set;
- TestFlight or equivalent distribution evidence where applicable;
- Apple App Review acceptance of the exact submitted binary and metadata.

These gates remain `BLOCKED` until the operator provides the required Apple
Developer credentials and Apple returns evidence for the exact candidate.

## Apple references

- [Configuring the macOS App Sandbox](https://developer.apple.com/documentation/xcode/configuring-the-macos-app-sandbox)
- [Accessing files from the macOS App Sandbox](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox)
- [Privacy manifest files](https://developer.apple.com/documentation/bundleresources/privacy-manifest-files)
- [Preparing your app for distribution](https://developer.apple.com/documentation/xcode/preparing-your-app-for-distribution)
- [App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/)
- [Create an App Store Connect provisioning profile](https://developer.apple.com/help/account/provisioning-profiles/create-an-app-store-provisioning-profile)
- [Upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds)

## Certification rule

`MAC_APP_STORE_CANDIDATE_PROVISIONAL` means only that the supplied local bundle
passed the static bundle, property-list, entitlement, signature, and containment
checks implemented by Mucaro. It is not App Store certification. Only the exact
submitted build can advance after App Store Connect processing and Apple App
Review evidence are attached to the release record.
