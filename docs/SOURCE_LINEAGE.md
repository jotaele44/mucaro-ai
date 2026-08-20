# Source Lineage

Every cited answer must preserve this chain:

```text
file → source_id → chunk_id → retrieval_result → answer_trace → export
```

If a generated answer does not cite retrieved chunk IDs, it receives `ERROR_UNCITED_ANSWER`.
