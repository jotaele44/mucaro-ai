# Mucaro V1 denominator audit events

The frozen `_architecture/mucaro_v1_denominator.json` remains historical baseline evidence for `MUCARO_V1_DENOMINATOR/1.0` and is not rewritten as implementation progresses. Current-source promotions/regressions live only in `_architecture/mucaro_v1_denominator_events.json` and are applied by `backend.app.denominator_audit`.

## Rules

1. Every event identifies one frozen requirement ID and preserves denominator membership.
2. `from_state` must equal the state produced by preceding history.
3. Every event requires non-empty evidence and a reason.
4. Event IDs are unique and append-only.
5. Historical exact-head evidence remains historical; later heads require their own evidence.
6. `PASS` is not awarded when material execution/certification residue remains.
7. Hosted Actions `startup_failure` before jobs is BLOCKED infrastructure evidence, never test PASS and never code FAIL.

## Overlay history

- Historical frozen baseline: `61.5% raw / 62.6% weighted`.
- First common-substrate overlay: `64.5% raw / 66.2% weighted` from BC-07, BC-08, BC-09 and PR-10 events.
- Coding read-only overlay: `66.0% raw / 67.7% weighted` after CODE-03 through CODE-05 became PROVISIONAL.
- Coding feature-plane overlay: `68.5% raw / 70.2% weighted` after CODE-06 through CODE-10 became PROVISIONAL. CODING is `55.0%` with `PASS=1 / PROVISIONAL=9 / OPEN=0`.

## OCR + GIS source overlay

Audit head: `c57615fec09c6353059868325c7c43c20a98b2c6`.

Additional append-only events:

- `OCR-07 OPEN → PROVISIONAL`: PR #23 binds original PDF bytes and page count to pdftoppm renderer identity/DPI, page PNG hashes and per-page Tesseract OCR receipts. Live renderer/Tesseract execution, difficult-layout coverage and representative-corpus certification remain unresolved.
- `GIS-09 OPEN → PROVISIONAL`: PR #15 topology repair, PR #25 vector-format manifests, PR #26 GeoTIFF receipts and PR #37 vector↔raster analysis now cover a bounded topology/vector/raster source plane. Broad live regression certification remains unresolved.
- `GIS-10 OPEN → PROVISIONAL`: PR #16 pyproj/PROJ CRS transforms, PR #17 WGS84 ellipsoidal measurement and PR #27 map/export provenance materially establish the certification substrate, but datum-grid breadth, vertical/time-dependent CRS cases, independent-engine parity and rendered visual certification remain unresolved.

Resulting overlay arithmetic:

- current raw completion: `70.0%`
- current weighted completion: `71.8%`
- applied events: `15`
- CODING: `55.0%` — `PASS=1 / PROVISIONAL=9`
- GIS: `75.0%` — `PASS=5 / PROVISIONAL=5`
- OCR: `45.0%` — `PASS=2 / PROVISIONAL=5 / OPEN=3`

WEB-08 remains OPEN: frozen-static-HTML anchor discovery from PR #24 is not equivalent to automated search/discovery authority. No event is recorded for that row yet.

This overlay is an evidence-state audit, not a new denominator version, domain certification, or release certification claim.
