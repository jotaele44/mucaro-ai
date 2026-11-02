# Frozen web captures and recovery

HTTP capture writes two separate records: an immutable snapshot file and an
observation index. A capture ID binds the exact requested URL, final URL, response
content SHA-256 and retrieval UTC. It identifies an observation, not a canonical
publisher, entity, or current version of a page. The response-byte hash and the
snapshot-JSON hash are distinct identities. Original response bytes remain
base64-encoded in the snapshot; extracted text remains untrusted evidence.

## Persistence and boundaries

Writers use one shared thread/process lock for the entire index read–modify–write
operation. Complete snapshot bytes are published exclusively, without replacing
an existing capture. An identical retry retains the index and capture bytes; the
same ID with different bytes or metadata fails closed. Capture JSON uses the
existing sorted-key UTF-8 serialization. Differently formatted JSON does not
establish prior byte identity and is not silently rewritten during recovery.

Before HTTP acquisition, the index must pass its schema, field, unique-ID,
UTC, digest, ID-binding and count checks. Damaged, ambiguous, oversized or
symlinked indexes are preserved and refused. Capture-directory symlinks are
refused. Shared locks reject symlinks and nonregular files and create private
0600 lock files. Index validation never resolves source hosts or fetches pages.

Budgets are 16 MiB for a complete snapshot JSON file, including base64 and
metadata, and 32 MiB for its index. Oversized encoded bodies are refused before
decoding. These limits are storage safeguards, not a claim of arbitrary web or
corpus coverage. Preserve/archive the entire collection before selecting a new
data directory when capacity is exhausted; do not delete its index to make room.

## Recover an interrupted index write

If saving the capture succeeds but the index commit fails, the error receipt
includes its saved capture ID and SHA-256. The frozen file is retained. Check
storage permissions and capacity, then choose one explicit recovery surface:

- In **Sources → Recover a saved web capture**, enter that ID and SHA-256 and
  select **Index saved capture**. Draft references survive workspace navigation.
- Run `mucaro captures recover websnap_ID --sha256 SHA256` from the configured
  local data environment.
- Authenticated API clients may post `{"sha256":"SHA256"}` to
  `/web-captures/{snapshot_id}/recover`.

The shared service checks the exact regular file, its full byte hash, snapshot
ID, original response bytes and metadata before indexing it. It does not accept
an arbitrary path or URL, reacquire a mutable page, replace a frozen file, ingest
it into the source library automatically, or turn page instructions into
authority. Repeated recovery is idempotent. Missing files, stale hashes,
noncanonical/invalid JSON and symlink targets are refused explicitly.

If the index itself is damaged, preserve the full data directory and restore a
known complete backup while the service is stopped. Recovery does not invent
missing history or reset damaged state. A file retained after a failed commit is
an unindexed candidate until its explicit byte binding is checked; its presence
alone does not prove acquisition history or the truth of its contents.

## Reusable verification

Use `backend/tests/test_web_capture_store.py` for actual concurrent HTTP captures,
separate-process writers, replay, identity conflicts, malformed state, capacity,
failed writes, no-network recovery and authenticated API/CLI checks.
`backend/tests/test_file_lock.py` exercises refused symlink/FIFO locks and lock
release after interruption. The rendered QA uses authored unindexed fixtures to
exercise stale-hash refusal, recovery, rapid activation, draft retention and
byte-preserving replay in desktop/tablet/mobile layouts; those fixtures do not
establish live search, research synthesis quality, physical devices or native
desktop behavior.

## Verified checkpoint, 2026-11-02

At application source `3ba0d8229819801756d9d4a6a3bfefe2ac877745`, the full local
release gate passes all 28 gates, including 1,735 backend tests with eight skips,
the capture/lock regressions, OCR/GIS/retrieval/recovery checks, package
verification, frontend build, and backend plus both macOS runtime-lock audits.
The gate uses the isolated Python environment with the patched pypdf 6.21.4.
Its receipt is `_architecture/release/evidence/local_release-3ba0d8229819.json`.
The earlier seven-advisory failure at `b86335d77d07` remains historical evidence,
superseded by the targeted package patch and rerun.

Full WebKit checks pass 340/340 at that source; full Chromium checks pass 340/340
at `d53ae4815069`. Across those two commits all 353 backend/frontend Git entries
match, with zero one-sided or symmetric differences, and frontend input/assets
and served-index manifests match. Each engine covers desktop/tablet/mobile and
33 axe scans with zero detected violations. The Chromium runner changed only an
unrelated queued fixture query to known source content. Representative narrow
and tablet recovery screens were visually inspected and retained with the
receipts. Earlier failures remain available as superseded observations.

This is bounded local/browser evidence. Live search, representative comparison
and synthesis, full manual accessibility, packaged/native/physical-device use,
signing and hosted release obligations remain independently open.

The same `3ba0d8229819` source also passed `FRESH_CLONE_CERT_OK` (installation,
release checks, CLI help, launcher self-test/smoke test, clean clone) and
`SOURCE_ARTIFACT_CERT_OK` (verified ZIP, extraction, installation, release checks,
CLI and desktop-package self-test). Both installed environments were inspected
and reported pypdf 6.21.4. Their terminal receipts and the verified source ZIP
manifest are preserved under `_architecture/release/evidence/` as
`fresh_clone-3ba0d8229819.json`, `source_artifact-3ba0d8229819.json`, and
`source_manifest-3ba0d8229819.json`. These establish source installation on this
host, not a clean physical host, native package signing, or deployment.
