# Frozen web research

The Sources workspace can compare two indexed web observations, inspect exact
citations, and save a report with both sources as lineage parents. This is the
offline comparison/citation/synthesis portion of TGT-006 and MATRIX-008. It does
not replace the separate search, acquisition, live-provider, or semantic-quality
requirements in the acceptance ledger.

## Operator workflow

1. Capture pages with the conversation’s `/fetch URL` tool and approve the calls.
   Search results, opened pages, and frozen observations remain separate states.
   Interrupted index commits use [capture recovery](WEB_CAPTURE_STORE.md), without
   downloading a mutable page again.
2. Open **Sources → Compare frozen web sources**. Allow the declared capabilities
   and activate research. Activation, deactivation, stale policy checks, and
   execution receipts use the shared skill runtime.
3. Select two distinct saved observations. The selection binds each ID to its
   full snapshot SHA-256. Refreshing the list does not silently accept a changed
   digest. Select a question and synthesis mode, then **Compare and synthesize**.
4. Inspect passage accounting, the draft, exact excerpts, offsets, retrieval
   dates, hashes, warnings, and limits. Download the verified comparison report
   for the complete passage inventory and all ranking candidates/ties. Saved
   outputs also appear in the artifact library.
5. Navigation retains the draft and loads the last successful saved research
   result. A later failed request does not erase that result. Deactivation blocks
   new invocations and keeps the saved results.

Default **Offline excerpts** performs no model invocation. **Mock model** is an
explicit offline test mode. Local and Anthropic model modes use the existing
provider configuration and permission checks. Anthropic sends the selected
passages and question to its provider. Requested model errors never trigger a
mock fallback. Missing matching context prevents model invocation.

## CLI and API

```sh
mucaro captures list --limit 50 --format json
mucaro skills mount mucaro/web-research
mucaro skills activate mucaro/web-research \
  --allow-capability filesystem.read \
  --allow-capability filesystem.write \
  --allow-capability model.invoke
```

Using the IDs and full digests from that listing:

```sh
mucaro captures compare "$CAPTURE_A_ID" "$CAPTURE_B_ID" \
  --a-sha256 "$CAPTURE_A_SHA256" --b-sha256 "$CAPTURE_B_SHA256" \
  --question 'What changed in the budget?' --synthesis extractive
```

`captures list` supports `--offset` and `--revision`. A changed collection refuses
continuation with an earlier revision. CLI errors use structured JSON on stderr
and a nonzero exit code. Comparison is an alias for `skills execute
mucaro/web-research`; it adds no separate business logic or permission bypass.

Authenticated `GET /web-captures?limit=50&offset=0` returns indexed metadata with
no server file paths. This is index metadata, explicitly unverified until use.
Subsequent pages bind `revision` to the first page. The authenticated existing
`POST /skills/mucaro%2Fweb-research/execute` accepts an `arguments` object:

```json
{
  "a_id": "websnap_0123456789abcdef",
  "a_sha256": "<complete 64-character lowercase SHA-256>",
  "b_id": "websnap_fedcba9876543210",
  "b_sha256": "<complete 64-character lowercase SHA-256>",
  "question": "What changed in the budget?",
  "synthesis": "extractive"
}
```

Those example identifiers are placeholders, not existing observations. Supported
synthesis values are `extractive`, `mock`, `openai_compatible_local`, and
`anthropic`. Unknown fields, URL/path overrides, malformed digests, whitespace
questions, and unknown modes are refused by the shared arguments schema.

## Evidence semantics and safeguards

### Choosing passage boundaries

Line mode is the default and preserves the existing comparison behavior for
lists and records. Choose **Paragraphs (wrapped prose)** for documents whose
sentences wrap across adjacent lines. Paragraph mode groups text until a blank
line; it retains the intervening line breaks in exact excerpts. The original
document bytes and Unicode extraction offsets remain unchanged. Each resulting
passage still has the 2,000-character ceiling and the existing source budgets.

The GUI saves the choice with the research draft. The CLI accepts
`--passage-mode paragraph`; API skill arguments accept `"passage_mode":
"paragraph"`. Supported values are `line` and `paragraph`. Saved results identify
the mode that produced their counts, even if the current selection later changes.

An insufficient-context result explicitly states that no model was invoked,
including when the requested mode was a live provider. Selecting a provider does
not imply that a request was sent.

### Source and execution boundaries

- Inputs must be indexed canonical snapshot JSON, regular files in this capture
  collection, with matching selected SHA-256, observation metadata, decoded
  content length, and HTTP content digest. Client paths are never accepted.
- Supported documents are successful HTTP 2xx HTML, XHTML, plain text, Markdown,
  and JSON. Character decoding is strict, with a bounded explicit charset set.
  Unsupported encodings and invalid bytes fail rather than inserting replacement
  characters. HTML text extraction excludes head/script/style/template/noscript;
  it executes no JavaScript and makes no visual-layout claim.
- Each source is limited to 200,000 decoded/extracted characters and 1,000
  passages. Paragraphs/lines are segmented at 2,000 Unicode characters. Exceeding
  the limits refuses the operation; the complete comparison is never silently
  truncated. Empty text is retained as an explicit no-context case.
- Raw captured bytes and raw extracted passages remain separate from the
  whitespace-collapsed comparison view. Mojibake, accents, case, spelling, and
  trailing spaces are retained in raw excerpts. SHA equality and normalized
  overlap do not prove canonical real-world identity.
- Passage accounting uses multisets. For A/B occurrence counts, `A = shared +
  A_only`, `B = shared + B_only`, `union = shared + symmetric_difference`.
  Duplicate occurrences and every passage ID remain in the report. No
  whole-record synthesis fabricates an observation.
- Lexical discovery selects at most two passages per source for synthesis. Ties
  prefer passages exclusive to one observation, then multiplicity differences,
  then source order. The default overview uses the same difference-first order.
  This prevents a late relevant difference from being hidden by an unchanged
  opening passage with the same lexical score. All
  candidate IDs and boundary ties are recorded. A deterministic selection is not
  a relevance or identity proof. No lexical match is a bounded context result,
  not proof that no evidence exists anywhere.
- The differences inspector displays up to 20 normalized groups with expandable
  raw excerpts; large duplicate groups show five raw occurrences. The report
  retains every group and occurrence. This display limit does not truncate the
  underlying comparison.
- Model prompts are checked against a conservative UTF-8 byte estimate plus the
  provider's output reserve and protocol overhead. A context-budget failure
  occurs before the provider request; selected excerpts are not silently
  shortened to force a fit. The local server's configured context remains an
  operator-supplied limit, not an independently measured model capacity.
- Exact citation offsets refer to the extracted Unicode string, not HTML bytes,
  screen coordinates, or OCR spans. The report binds each citation to its
  snapshot/content hashes and observation time.
- Model citations must use `(chunk_id=...)` and name selected passage IDs.
  Missing/unknown/inconsistent references put the response in
  `MODEL_CITATIONS_REJECTED`; the GUI exposes it as rejected, not as a grounded
  answer. Valid references still do not certify truth, completeness, or
  faithfulness of generated claims. Differing source claims stay unresolved.
- Instruction detection is advisory. Structural protection comes from source
  fencing in the provider prompt, no tool-execution path from research content,
  explicit provider mode, and the shared skill permission boundary. Original
  instruction-bearing evidence remains inspectable.
- Reports are new private files with both source artifact parents. The existing
  authenticated download service checks their bytes before returning them.
  Retries reuse frozen inputs; they never reacquire their URLs.

## Reusable verification workflow

```sh
python3 -m pytest backend/tests/test_web_research.py -q
python3 scripts/evaluate_web_research.py --output data/exports/web-research-eval.json
python3 scripts/evaluate_web_research_real.py --output data/exports/web-research-real-eval.json
node qa/rendered/rendered_qa.mjs --scenarios research --browser chromium
node qa/rendered/rendered_qa.mjs --scenarios research --browser webkit
```

The committed nine-case corpus freezes accounting and excerpt/no-context
thresholds at 1.0. It covers duplicate conservation, conflicting observations,
HTML entities and excluded active content, whitespace differences, multilingual
and mojibake preservation, empty/negative context, and instruction-bearing text.
These are authored offline acceptance examples, not an independent live-model
semantic benchmark. The release gate runs this evaluator alongside existing
retrieval, ingestion, and recovery gates.

The additional real-source gate uses complete, hash-bound frozen RFC documents
with five cases and six required evidence anchors. It reuses the archived RT-09
source manifestation and adds explicit paragraph boundaries after the JWT
document demonstrated the weakness of counting every hard-wrapped line as a
separate passage. Source hashes, parent manifests, scope distinctions and
inclusion/exclusion counts are preserved in
`backend/tests/fixtures/web_research_real_v1/README.md`. This extends evidence
breadth without claiming independent model semantics or full live web research.

The rendered scenario uses real local HTTP acquisition of authored documents,
then stops their source servers before comparison. It tests activation,
duplicate-source refusal, rapid repeated pointer/touch input, one persisted
execution, exact citation inspection, byte-verified report download, no-context
feedback, navigation recovery, revocation, overflow, and axe at desktop, tablet,
and narrow mobile sizes. Scenario-only receipts remain marked partial.

Engineering lesson: defer the provider adapter import until invocation, because
provider-native tool planning itself imports the skill registry. Keep domain
comparisons independent of provider loading, and verify stderr for CLI error
contracts. Required arithmetic checks must use explicit branches and structured
errors; Python assertions can disappear under optimization. Certification must
retain failing subprocess output, and browser checks must await the submitted
execution ID instead of accepting a previously saved result as new completion.
This runbook and the regression corpus are reusable guidance; they do
not establish that a model or agent has been trained.

## Qualification at the frozen source checkpoint

The tested source is `48f36a197fcf291959f791d453d4cacd5fcef247`, tree
`12e3c06a74cc04c7e9cc60caac87916aa3754e8e`. It passes 29/29 local release gates,
1,773 backend tests (8 skipped), fresh-clone installation/CLI/launcher checks,
and installation from the separately verified 697-member source ZIP. Its
qualified ZIP SHA-256 is
`736764a7712902dddc8529df36bbb1efd75a480ad2103abaf91c5b37b2bd3070`.
The two installed environments match all 62 frozen package versions: intersection
and union 62, A-only/B-only/symmetric-difference 0. This establishes version-set
equality, not byte identity of complete environments.

The nine authored evaluation cases pass all frozen thresholds; all 15 cited
excerpts match exact extracted text and both no-context cases withhold synthesis.
The 1,000-passage regression preserves 999 shared occurrences, one A-only and
one B-only occurrence, union 1,001, and symmetric difference 2. It verifies that
the changed value at passage 500 appears in synthesis while all 1,000 tied
candidates per source remain recorded.

Full Chromium/WebKit browser runs pass 383/383 each at earlier source
`7c963594c8b450c433c249ab7e5ac3236cb83700`. After accounting/security hardening,
both engines pass research-only 45/45 replays at the tested source above.
Frontend input/build/served-index identities match all four runs; the later
backend is not claimed identical to the earlier one. There are 72 automated axe
scans across the full runs and six additional scans across the scoped replays,
with zero detected violations. The result screens were visually inspected at
desktop, tablet, and narrow mobile sizes. This remains bounded browser review.

The earlier source failed one security test because production assertions could
be disabled by optimized Python. Its failed release receipt is retained as
SUPERSEDED evidence. Explicit nonnegative-integer arithmetic guards and an
optimized-Python negative regression resolve that failure; no security waiver
was added. Earlier harness setup/selector/timing failures remain in the ignored
raw run directories. The byte-preserved receipts, manifest, dependency constraints,
screenshots, and hashes are indexed in
`_architecture/release/evidence/web_research_checkpoint_48f36a197fcf.json`.

The 139-requirement ledger remains authoritative and the full release remains
PROVISIONAL. Live search, live-provider/semantic research quality, broader real
corpora, native/device/manual accessibility, and hosted release obligations keep
their existing states. The primary checkout, prototype snapshots, and v0.1.4
baseline were preserved. No push, hosted merge, new tag, or deployment occurred.
