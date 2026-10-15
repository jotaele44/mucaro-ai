---
name: ui_video_reconstruction_certifier
version: 1.0.0
description: Freeze UI recordings into bounded observation denominators, map every portable observation to implementation and regression evidence, and fail closed on unmapped residue or provider-specific leakage.
scope: ui_reconstruction_and_certification
---
# UI Video Reconstruction Certifier

Use this skill after a UI recording is supplied and the goal is feature/motion reconstruction rather than brand imitation.

## Binding workflow

1. Freeze each source video by raw-byte SHA-256 plus duration, frame denominator, dimensions and retrieval/source identity.
2. Enumerate observable interactions with stable `OBS-*` IDs. Distinguish FACT from INFERENCE and UNKNOWN. Hidden behavior is never inferred into a 100% claim.
3. Explicitly exclude provider-specific branding, commercial/account semantics, product/model names and native OS controls that the target app should adapt to rather than recreate.
4. Map every portable observation to one or more implementation paths and one or more regression surfaces.
5. Run `python scripts/certify_video_ui_coverage.py`. It fails on duplicate IDs, missing/extraneous observations, missing code/tests, nonzero unmapped residue, or hard-coded ChatGPT/Claude product UI in covered frontend files.
6. If original videos are available locally, rerun with `--video-a` and `--video-b`; otherwise source-byte verification is `OPEN_NOT_SUPPLIED`, never silently PASS.
7. Separately certify build, rendered behavior, accessibility, performance, native desktop and physical mobile. Source coverage PASS does not imply any of those.
8. Merge only when the bounded claim has zero unresolved residue. Physical-device OPEN blocks a claim that explicitly includes physical-device parity.

## Commands

```bash
python scripts/certify_video_ui_coverage.py
python scripts/certify_video_ui_coverage.py --video-a /path/to/video-a.mp4 --video-b /path/to/video-b.mp4 --output /fresh/path/ui-video-coverage-receipt.json
pytest -q backend/tests/test_video_ui_coverage.py
```

## Certification semantics

- `PASS`: only the named gate passed.
- `PROVISIONAL`: implementation is present but downstream rendered/device gates remain.
- `OPEN`: not yet executed.
- `BLOCKED`: required evidence cannot currently execute.
- `UNRESOLVED`: contradictory/tied evidence prevents adjudication.
- `SUPERSEDED`: retained historical evidence displaced by a newer exact-head result.

Never convert deterministic implementation, same labels, visual similarity, or source absence into functional identity.
