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
