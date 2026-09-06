# Source Lineage

Every cited answer must preserve this chain:

```text
file → source_id → chunk_id → retrieval_result → answer_trace → export
```

If a generated answer does not cite retrieved chunk IDs, it receives `ERROR_UNCITED_ANSWER`.
If it cites a chunk that was not retrieved, it receives `ERROR_CITED_CHUNK_NOT_RETRIEVED`.
If cited chunk IDs cannot be expanded into trace records, it receives
`ERROR_CITATION_TRACE_MISSING` or `ERROR_CITED_CHUNK_NOT_TRACED`.

Answer traces include a `cited_chunks` array that binds each citation to the
retrieved evidence used at answer time:

```json
{
  "citation_order": 1,
  "retrieval_rank": 1,
  "chunk_id": "chunk_a",
  "source_id": "src_a",
  "chunk_index": 0,
  "score": 0.547248,
  "retrieval_strategy": "hybrid_tfidf_v1",
  "matched_terms": ["lineage", "source"],
  "source_format": "md",
  "text_snippet": "Source lineage requires cited chunk IDs."
}
```

JSON exports preserve the full answer trace, Markdown exports include
source/rank details under each cited chunk, and citation CSV exports include
one row per cited chunk with run ID, citation order, retrieval rank, source ID,
chunk index, score, retrieval strategy, source format, and text snippet.
`mucaro eval answer-trace` and `mucaro certify answer-trace-quality` validate
this trace contract with positive, negative, and generated local-corpus cases
before release.

HTML and HTM inputs follow the same chain. The parser extracts visible document
text for chunking while preserving file SHA-256, byte count, title metadata,
link count, parser ID, and parser warnings in the source manifest. Script,
style, noscript, and template content is excluded from retrieval text so local
HTML imports do not pollute cited context with page implementation details.

DOCX inputs also follow the same chain. The parser reads the local OpenXML
package, extracts visible paragraph and table text from `word/document.xml`, and
preserves paragraph count, table count, text length, parser ID, parser warnings,
and available core document properties in the source manifest.

EML inputs also follow the same chain. The parser reads local RFC 822/MIME
messages, extracts selected headers and body text for chunking, preserves body
part counts, attachment counts, extracted/skipped attachment counts, attachment
names/content types, parser ID, and parser warnings in the source manifest.
Text-like TXT, Markdown, HTML, CSV, and JSON attachments are extracted behind a
bounded allowlist and appended to retrieval text under explicit attachment
markers. Other attachment types remain listed with skip reasons instead of
being silently folded into cited evidence.

MSG inputs follow a narrower provisional chain. The parser treats local Outlook
`.msg` files as binary sources, verifies the compound-file signature when
present, and extracts bounded printable text segments into cited chunks when
recoverable. The source manifest records `outlook_msg_text_probe_v1`,
`structured_properties_extracted=false`, parser warnings, extracted segment
counts, byte count, file hash, and source path so this heuristic evidence cannot
be confused with full Outlook property extraction.
