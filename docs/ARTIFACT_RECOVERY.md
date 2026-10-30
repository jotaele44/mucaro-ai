# Saved outputs and artifact recovery

Open **Artifacts → Saved outputs** to find OCR, GIS, and answer artifacts after
leaving a workspace or restarting the service. Filter by filename, producer, or
artifact ID and choose **Apply filter**. Filtering discovers labels; a matching
name does not establish that files or sources are identical.

**Inspect lineage** shows the stable artifact ID, recorded SHA-256, provenance
state, and direct parents. Each parent can be inspected in turn. Legacy records
without provenance metadata are explicitly labeled; their graph is validated,
but missing historical metadata is not invented. Internal host paths and
transformation parameters are not exposed by this catalogue.

**Download saved file** checks the complete saved byte count and SHA-256 before
returning the checked buffer. Listing metadata does not check file integrity.
Only recorded DERIVED/EXPORT files within the app's export directory are
eligible; missing, changed, oversized, and escaped files fail closed. Some
derived artifacts hold metadata without an export file and are labeled as not
downloadable. Conversation exports remain separately available below.

Pending feedback prevents repeated activation while a request is in flight.
A prepared download still requires the browser or native save dialog to retain
the file. The shared save helper keeps blob URLs alive for Safari acquisition.

## CLI and API

```bash
mucaro artifacts list --query terrain --limit 50
mucaro artifacts inspect artifact_ID
mucaro artifacts save artifact_ID --output ./recovered-terrain.tif
```

Saving creates a private file (0600) exclusively. It does not overwrite existing
files, follow an existing destination symlink, create missing parent folders,
or create an output when source bytes fail verification. Select a new destination
to retain earlier edits. Relative `--output` paths use the current directory.

Authenticated API equivalents:

- `GET /artifacts?limit=50&offset=0&query=terrain`
- `GET /artifacts/{artifact_id}`
- `GET /artifacts/{artifact_id}/download`

Continue a page with its returned `next_offset` and `revision`. The revision is a
SHA-256 of the canonical JSON serialization of the **ordered record list**
(UTF-8, sorted object keys, compact separators). It is not the raw store-file
hash or proof of graph equivalence. If the record snapshot changes, pagination
returns `ERROR_ARTIFACT_STORE_CHANGED` (409). Refresh from the first page. GUI
errors retain the previous list and label it as the last loaded snapshot.

## Damaged state

The shared loader refuses ambiguous JSON, invalid UTF-8, non-finite numbers,
symlinked stores, duplicate IDs, missing parents, invalid parent types/counts,
cycles, and contradictory provenance. Graph validation applies to legacy rows
too. Iterative walks support deep valid lineages and shared ancestors without
recursive overflow or repeated ancestor rows.

Library/download API errors return `ERROR_ARTIFACT_STORE_INVALID` with recovery
guidance. The CLI uses the same loader. Reading or creating an artifact never
silently resets, repairs, or overwrites a damaged store.

Stop the app before recovery. Preserve a byte-for-byte copy of the damaged
`processed/artifact_lineage.json` and its data directory before restoring a known
backup of that directory. Retain the damaged copy as evidence. Restore the
complete data-directory backup while the service is stopped, including its
database, source snapshots and outputs. There is no dedicated CLI command for
restoring that whole directory. Do not delete parent rows, alter IDs/hashes, or
invent provenance to make validation pass. If no trustworthy backup
exists, preserve the data for inspection. Missing original output bytes cannot
be reconstructed by the library.

## Conversation export identity and browser behavior

A work-run ID and an answer-trace ID identify different records. Run summaries
carry the explicit `answer_run_id` and available `export_formats`; the GUI never
guesses that relationship from a prefix, filename, count, or display label.
Cards use the persisted `state` and `prompt_preview`, and hide formats whose
export file is absent. A separate cursor loads older runs. Conversation exports
still use the existing answer-export service; the catalogue's checked-buffer
download guarantee applies to `/artifacts/{id}/download`.

Full rendered checks at `622e9c48fb94` pass 321/321 independently in Chromium
and WebKit across desktop, tablet and narrow mobile. Each engine includes 30
axe scans without detected violations. Screenshot review found no clipping or
hidden library action at the reviewed tablet/narrow widths. This is a bounded
review, not complete product-wide accessibility or native-device certification.

The WebKit run exposed pointer-focus and native-select sizing differences.
Capture the actual clicked button's focus before opening a modal; do not consume
or replay its click. Keep the native select's keyboard behavior while removing
the popup appearance that ignored the 44px minimum, and preserve a visible
themed dropdown affordance. Empty-state fixtures must use genuinely empty
services; an early empty render before a request finishes is not proof of empty
persistent state. Give independent viewport fixtures distinct stable test data.

## Reusable verification pattern

Run `node qa/rendered/artifact_touch_qa.mjs` after installing the pinned frontend
and rendered-QA dependencies. `MUCARO_PYTHON` can select the Python runtime.
The check rebuilds the frontend, records source/input/test-script identities,
starts an isolated real service and sends actual emulated touch events at tablet
and narrow-mobile widths in Chromium and WebKit. One tap opens the library and
inspector; one download tap must return the fixture's recorded bytes with exactly
one request. The authored fixture tests artifact access, not GIS correctness.
Physical devices and native WKWebView remain separate evidence requirements.

Test record contracts and whole-graph invariants at the shared loader. Include
malformed bytes, legacy rows, cycles, deep DAGs, shared ancestors, changing page
revisions, and unchanged-store assertions after refusal. Bind downloads to
actual returned bytes, never just successful status or matching filenames.
Exercise navigation away/back, pending double-clicks, failed refresh/retry, and
empty filtering through rendered controls at supported viewports. See
`backend/tests/test_artifact_recovery.py` and the saved-output scenario in
`qa/rendered/rendered_qa.mjs`.
