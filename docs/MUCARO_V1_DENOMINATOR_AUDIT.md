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


## WEB-10 bounded traversal overlay

Audit head: `4cc0433cb1f06648ddd8be62e1c5326228a0f68e`.

- `WEB-10 OPEN → PROVISIONAL`: PR #49 merged deterministic bounded static-HTML traversal and GitHub Actions run `35415105947` completed successfully.
- The scope preserves page/depth/link ceilings plus duplicate, deferred, cross-host and fetch-failure residue.
- `BOUNDED_EXHAUSTED` applies only to the declared static-anchor queue. It does not claim rendered-JavaScript, search-engine, sitemap, API, site-wide or internet-wide exhaustion.
- WEB-08 automated search/discovery and WEB-09 rendered/JavaScript interaction remain OPEN.

Resulting overlay arithmetic:

- current raw completion: `70.5%`
- current weighted completion: `72.3%`
- applied events: `16`
- BROWSING: `60.0%` — `PASS=4 / PROVISIONAL=4 / OPEN=2`

This remains an evidence-state overlay, not browsing-domain or release certification.


## WEB-09 live rendered-browser overlay

Audit head: `477bf5d0dcf5a705f70646e9e50fb72e3ff2a88c`.

- `WEB-09 OPEN → PROVISIONAL`: PR #69 merged a governed rendered-browser adapter plus dedicated live Playwright/Chromium execution.
- Web Render Certification run `35430709543` completed successfully and preserved artifact `10580238368` with digest `sha256:c65c2b47d76ac69f2c62a7377d552062581c0011ef67af94ae575d358542d238`.
- The executed render used Playwright Chromium `140.0.7339.16`, reached `https://example.com/` with HTTP 200, and preserved exact DOM and PNG screenshot hashes.
- Rendered observations remain explicitly non-citation-ready by default.
- The frozen WEB-09 requirement remains broader than this execution: authenticated browsing, click/form automation, downloads/uploads and arbitrary JavaScript interaction are not certified. Therefore PASS is not authorized.

Resulting overlay arithmetic:

- current raw completion: `71.0%`
- current weighted completion: `72.8%`
- applied events: `17`
- BROWSING: `65.0%` — `PASS=4 / PROVISIONAL=5 / OPEN=1`

This remains a provisional evidence-state overlay, not browsing-domain or release certification.


## CODE-07 executed coding quality overlay

Audit head: `bd17fa6e7bea44bbf2dddb895ecc1074043a7967`.

- `CODE-07 PROVISIONAL → PASS`: PR #71 merged the current-main replay of the isolated coding quality profile.
- GitHub Actions CI run `35430951380` completed successfully.
- Full-profile artifact `10580757491` has digest `sha256:782a46980529814233ba082942904e5e5be7564c05c7c5892b5efeaea59e6a1f`.
- Live-container artifact `10580747437` has digest `sha256:9402eeb6995090c9e27258308a4fa94990188aa60705e715a9a2329d29c039df`.
- Coding-plane pytest, Ruff, Mypy and package build all executed in the non-root, network-disabled, read-only-host isolated runtime.
- This PASS is scoped to frozen CODE-07. It does not promote CODE-06 mutation, CODE-08 review, CODE-09 lifecycle, CODE-10 regression corpus, or whole-repository lint/type debt.

Resulting overlay arithmetic:

- current raw completion: `71.5%`
- current weighted completion: `73.3%`
- applied events: `18`
- CODING: `60.0%` — `PASS=2 / PROVISIONAL=8`

This remains an evidence-state overlay, not coding-domain or release certification.
