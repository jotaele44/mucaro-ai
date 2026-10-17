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


## GOV-07 + executed coding reconciliation

Audit base head: `22b7e557e7583f14cd79caaf3d7634526199dfd9`.

- Existing `GOV-07 BLOCKED → PASS` event is included in current arithmetic from exact-main hosted push CI evidence.
- `CODE-06 PROVISIONAL → PASS`: PR #74 merged live sandboxed mutation evidence. CI run `35431347821` passed; mutation artifact `10580902996` digest `sha256:9f712677a566f040744403cc2f42a287b0f233d22bd067c6791cf34aa19ba3dc` preserves approved apply, preimage-drift rejection, reverse-apply rollback, network-disabled execution and host-byte conservation.
- `CODE-07 PROVISIONAL → PASS`: PR #71 merged the isolated coding quality profile. CI run `35430951380` passed; full-profile artifact `10580757491` digest `sha256:782a46980529814233ba082942904e5e5be7564c05c7c5892b5efeaea59e6a1f` and live-container artifact `10580747437` digest `sha256:9402eeb6995090c9e27258308a4fa94990188aa60705e715a9a2329d29c039df` bind the executed coding-plane pytest/Ruff/Mypy/build profile.
- CODE-08 review, CODE-09 remote lifecycle and CODE-10 full regression/certification closure remain PROVISIONAL.

Resulting overlay arithmetic:

- current raw completion: `73.0%`
- current weighted completion: `74.6%`
- applied events: `20`
- CODING: `65.0%` — `PASS=3 / PROVISIONAL=7`

This remains an evidence-state overlay, not coding-domain or release certification.


## OCR-07 live PDF rendering/page-lineage overlay

Audit head: `1a7f950606acc7ef441cd9424c39fc05afe1247e`.

- `OCR-07 PROVISIONAL → PASS`: PR #80 merged the current-main live OCR runtime lane.
- Dedicated OCR run `35460846947` passed and preserved artifact `10589823508` with digest `sha256:123a58aaf7f3481c3973ea8d26bcac8dedc8dcbb0cc9dadc869af853e0a6857f`.
- Normal CI run `35460847002` also passed.
- Evidence includes real pdftoppm rasterization, per-page Tesseract OCR, source/render/report SHA-256 lineage and page-count closure.
- The calibration fixtures are synthetic; therefore OCR-08 multi-column/table/form reconstruction, OCR-09 handwriting/difficult-scan coverage and OCR-10 representative corpus remain unresolved.

Resulting overlay arithmetic:

- current raw completion: `73.5%`
- current weighted completion: `75.0%`
- applied events: `21`
- OCR: `50.0%` — `PASS=3 / PROVISIONAL=4 / OPEN=3`

This remains an evidence-state overlay, not OCR-domain or release certification.


## CODE-08 live review-evidence PASS

Audit head: `a08b0a0cf575c8b6889a43c8c91ec086155984a5`.

- `CODE-08 PROVISIONAL → PASS`: PR #83 merged the dedicated code-review certification lane.
- Normal CI run `35461226140` and Coding Review Certification run `35461226187` both completed successfully.
- Artifact `10589729427` digest `sha256:fa7853ceae339846ceccbfa43fe0d5ea6c50454dbf5ea49364d4bf8fc432c1b2` preserves exact file/line review evidence.
- Positive evidence binds changed path, source SHA-256, exact line range, excerpt and excerpt SHA-256; negative gates reject unchanged paths and invalid ranges while preserving HEAD.
- CODE-09 remote lifecycle and CODE-10 zero-residue coding certification remain PROVISIONAL.

Resulting overlay arithmetic:

- current raw completion: `74.0%`
- current weighted completion: `75.5%`
- applied events: `22`
- CODING: `70.0%` — `PASS=4 / PROVISIONAL=6`

This remains an evidence-state overlay, not coding-domain or release certification.


## RT-09 real-world corpus PROVISIONAL

Audit head: `f70ebd8e815bd4f4044b67455c8a33c7ef05789b`.

- `RT-09 OPEN → PROVISIONAL`: PR #127 merged the current-release-main real-world retrieval corpus lane.
- Dedicated run `36382763249` passed; artifact `10952734875` digest `sha256:1eb4198e783fbaa84ae60610c7048d6e889c077aaf59aa70c7db29cebd4badac`.
- Frozen manifestation: 24 authoritative IETF/W3C/NIST sources, 24 bound queries, TXT/HTML/PDF, 8,277,197 bytes, 4,933 chunks.
- Hybrid top-5 evaluation hit every expected source: hit rate `1.0`, zero missed queries.
- Corpus manifest SHA-256: `8b450c8f3a587d5c39ecc6a9e94480592d5ddb3565021c4373cc65587ae48fb5`.
- Certification report SHA-256: `0249b023d286c9e6332fbd98e633b49797cd98e631c0efcad34c123f0fb3fe6e`.
- The corpus gate itself authorizes at most PROVISIONAL; RT-10 production-scale stress remains unresolved.

Resulting legacy overlay arithmetic:

- current raw completion: `75.5%`
- current weighted completion: `76.8%`
- applied events: `24`
- RETRIEVAL: `90.0%` — `PASS=8 / PROVISIONAL=2`

This is not retrieval-domain or release certification.


## OCR-10 bounded representative corpus overlay

Audit publication head: `f0991f960eb4195d4c4ceaa4a1df68b6b0c0e8a8`.

OCR execution head: `d1018d571da1224f1bdd1e34734acf250892a27e`.

- `OCR-10 OPEN -> PROVISIONAL`: append-only event `evt-ocr10-bounded-corpus-provisional` records the committed corpus execution.
- The six-case manifest is source-byte bound with SHA-256 `2def4509bfa8b85ca2f2fd684551230dd65211e398f8aa88c7a5b6969cc1cb97`.
- Exact-head receipt `ocr_representative_corpus-d1018d571da1.json` reports `OCR_REPRESENTATIVE_CORPUS_READY`, six executed cases, token-recall mean `1.0`, bounded-observation pass rate `1.0`, source identity `1.0`, span accounting `1.0`, and accent preservation for all six reported cases.
- The evaluation report SHA-256 is `22c5f73332074175c13d2e56baf3150b1e7bdcdd00482dc222942e1af3636fb3`; the release receipt SHA-256 is `7d012ab71b79b33dd6860a3563b7d458706e89b658fa064b5860ad15a52a0ee5`.
- Hosted OCR Representative Corpus Certification run `38845291683` completed successfully for the same head.
- `quality_state=PROVISIONAL` and `accuracy_certified=false` remain correct because the corpus is bounded calibration/regression evidence, not a statistically representative real-world accuracy study. OCR-08 semantic table/form reconstruction and OCR-09 handwriting/difficult-scan coverage remain open.

Resulting denominator arithmetic:

- current raw completion: `76.0%`
- current weighted completion: `77.2%`
- applied events: `25`
- OCR: `55.0%` — `PASS=3 / PROVISIONAL=5 / OPEN=2`
- total states: `PASS=60 / PROVISIONAL=32 / OPEN=8`

This remains an evidence-state overlay, not OCR-domain or release certification.
