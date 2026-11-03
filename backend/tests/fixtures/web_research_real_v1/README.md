# Frozen comparative-research sources

This fixture includes three complete documents: RFC 8259, RFC 7519, and RFC 6531,
totaling 132,376 original source bytes. Their original preambles, formatting and
copyright notices are retained without rewriting. The full upstream corpus has
24 sources; this focused subset includes three and excludes 21. The parent
acquisition and corpus manifests retain the complete upstream denominator.

The bytes came from existing GitHub Actions artifact `10952734875`, rather than
a new acquisition from the document servers. The downloaded ZIP SHA-256 is
`1eb4198e783fbaa84ae60610c7048d6e889c077aaf59aa70c7db29cebd4badac` and its
corpus-manifest SHA-256 is
`8b450c8f3a587d5c39ecc6a9e94480592d5ddb3565021c4373cc65587ae48fb5`.
All 24 archived source hashes were checked before selecting the subset. The
frozen archive and all 30 member hashes remain in ignored local data under
`data/exports/research_real_corpus/rt09-10952734875/`.

`manifest.json` binds the selected raw files to exact parent acquisition rows,
URLs, retrieval times, byte counts and hashes. It also preserves the different
commit-shaped labels in the artifact name and reported workflow metadata. No
equivalence of those code identifiers is assumed, and this fixture does not
re-certify the historical hosted workflow.

`cases.json` freezes five questions and reference expectations before release
qualification. Three comparative cases require six specific evidence anchors;
two negative cases require withholding synthesis. Expected anchors were selected
through engineering inspection of the frozen source text. They are authored
reference judgments, not an independent model-intelligence benchmark. In
particular, requirements about general JSON objects, JWT claim sets and SMTP
strings have different scopes; a shared keyword does not make them equivalent.

The evaluator reconstructs capture JSON representations from the original
content bytes and acquisition times. These are explicitly marked
`RESTORED_CAPTURE_REPRESENTATION`: the new JSON files are not claimed byte-identical
to any historical Mucaro snapshot. Raw document hashes remain unchanged.

The checks require 1.0 case pass rate, required-anchor coverage, exact excerpt
fidelity and no-context accuracy. They exercise the shared governed skill in
paragraph mode, keeping hard-wrapped prose together while retaining exact
Unicode offsets and the existing 2,000-character passage ceiling. No source URL
is reacquired and no model is invoked.

Reproduce with `python3 scripts/evaluate_web_research_real.py`. Broader document
formats, real-world breadth, independent semantic adjudication and live-provider
quality remain separate acceptance requirements.
