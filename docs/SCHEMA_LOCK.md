# Schema Lock

The schema lock prevents silent format drift.

## Schemas

- `source_manifest.schema.json`
- `document_chunk.schema.json`
- `retrieval_result.schema.json`
- `answer_trace.schema.json`
- `prompt_template.schema.json`
- `run_manifest.schema.json`
- `execution_gate.schema.json`

## Example source manifest

```json
{
  "source_id": "src_deadbeef",
  "source_path": "data/input/src_deadbeef_note.md",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "file_name": "note.md",
  "mime_type": "text/markdown",
  "parser": "text_utf8",
  "ingested_at": "2026-07-06T00:00:00Z",
  "lineage_status": "VALID",
  "chunk_count": 1,
  "errors": [],
  "warnings": []
}
```

## Validation

```bash
python scripts/validate_json.py
```
