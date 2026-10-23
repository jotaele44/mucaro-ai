---
name: gui_intake_certifier
version: 1.0.0
description: Audit uploaded GUI draft ZIPs and separate brand assets before integrating reusable components into Múcaro's canonical local-first UI.
scope: archive_and_ui_seam
---
# GUI Intake Certifier — v1.0

**Purpose:** Freeze the original GUI draft and brand asset, inspect every archive member, conservatively compare archive manifestations, and identify only reusable UI patterns. The tool does **not** certify frontend functionality, feature identity, a standalone app or a release.

## Inputs and source identities

- Original user-provided `Mucaro AI.zip`: SHA-256 `20047e9421b6eeb9329a2e6f39a9da7d39f5bf2ab39303815cceb41692851bd6`.
- Separately uploaded owl JPEG: SHA-256 `783e24be90f11ed800199cc0adb1d45f4c37ba2dc4cc8175864ebd521fdbec16`.
- Expected path + uncompressed size + per-member SHA-256 canonical digest: `bd10e542bdfba30fd0099201d929d7e895e1d81b2937dde625d50d6382337231`.
- These frozen hashes identify **this snapshot**, not future versions or equivalent UI functionality. The independently generated 64px WebP GitHub brand derivative has SHA-256 `3be31e5aa096458a7534f855393a4e9637889db5999782397ceaeaa30c485d02` and is **not** byte-identical to the original JPEG.

## Execution

Use only local Python 3.11+ and repository source. Do not clone external templates or install the prototype's 46 direct dependencies.

```bash
python3 scripts/audit_gui_draft.py "/path/to/Mucaro AI.zip" \
  --owl "/path/to/B4229344-06E4-4F76-86EA-FE7730C7ABE0.jpeg" \
  --verify-known --output "/fresh/path/gui_intake_receipt.json"
python3 -m unittest discover -s scripts -p test_audit_gui_draft.py -v
```

When the original attachments are absent in a CI checkout, the source-bound positive fixture is explicitly **SKIPPED**, never counted as a verified source PASS; synthetic security and equivalence fixtures must still execute.

## Binding gates

1. Inspect ZIP headers, every member's exact RAW path, byte length and SHA-256, duplicate/portable collision, symlink/encryption/traversal risk, archive CRC, global size bounds, and canonical member serialization. An archive outer hash difference only proves BYTE difference.
2. Preserve separate `BYTE`, `LOGICAL`, `SCHEMA`, `SOURCE_MANIFESTATION` identity. Only classify `BYTE_IDENTICAL | PURE_RECOMPRESSION | SAME_PAYLOADS_DIFFERENT_PATHS | DISTINCT_PAYLOADS | UNRESOLVED` within the audit's defined checks. If identical payloads have unexplained container differences, return `UNRESOLVED`.
3. Explicitly calculate INTERSECTION, A_ONLY, B_ONLY, UNION and SYMMETRIC_DIFFERENCE from whole-member triples (path, size, hash) when comparing two ZIPs. Preserve duplicate payload multiplicity separately.
4. Do not interpret Floot demo page content as live application data or claim two destinations are identical merely because their labels match. Compare real routes against the repository's route registry, keeping draft-only features as design candidates.
5. Never copy unreviewed runtime dependencies, remote font imports, broken CDN image paths, mock records, embedded secrets or generated asset claims into canonical Múcaro.
6. Provide positive/negative regression receipts and frozen input hashes. A script exit code proves only its own defined checks, not integrated desktop/mobile certification.
7. Reuse already-passed receipts when downstream build/device certification fails; do not redownload mutable sources as if they were the same byte manifestation.
8. Fail closed if the frozen original ZIP, logo, or canonical member digest changes under `--verify-known`. For a genuinely new draft, preserve this snapshot and create a separately named manifest, never overwrite.

## Integration handoff

GUI draft styling and components are **adaptations**, not a replacement for existing authenticated API, provenance/evidence widgets, safe mutation gates, or the current three-region route registry. Preserve native shell packaging on its own release lane. An absent 40-frame sprite atlas blocks **sprite animation equivalence** but not a clearly labeled static local-image fallback. Device QA and native app packaging remain OPEN until the exact release head has matching receipts.

**Verified initial bounded evidence (2026-09-30):** original ZIP 143 entries / 137 files / 6 directories; source verification PASS; 8 local Python fixtures PASS. Script and tests stored in this repository match the exact locally executed Git blob SHA-1 manifests. No whole-repository or iPhone certification claimed.
