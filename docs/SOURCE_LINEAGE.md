# Source Lineage

Every cited answer must preserve this chain:

```text
file → source_id → chunk_id → retrieval_result → answer_trace → export
```

If a generated answer does not cite retrieved chunk IDs, it receives `ERROR_UNCITED_ANSWER`.

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
