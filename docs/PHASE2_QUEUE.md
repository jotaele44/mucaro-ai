# Phase 2 Queue

| Item | Purpose | Status |
|---|---|---|
| Local hybrid retrieval | Deterministic lexical plus TF-IDF vector scoring | Complete in V1 |
| Retrieval eval harness | recall@k, top-1, and no-context gate for retrieval changes | Complete in V1 |
| Persistent local TF-IDF index | Local stored vector index with explicit `persistent` strategy | Complete in V1.4 |
| Local BM25 retrieval | Opt-in BM25 ranking for stronger local term matching | Complete candidate in V1.14 |
| Chroma integration | Persistent embedding/vector store for larger corpora | Planned in `docs/RETRIEVAL_UPGRADE_PLAN.md`; queued after larger eval corpus |
| LlamaIndex ingestion | Richer document ingestion and node parsing | Queued |
| Unstructured parser expansion | Better PDFs, DOCX, HTML, emails, and model-backed media understanding beyond metadata | Queued |
| LiteLLM model router | Provider-agnostic model routing | Queued |
| GraphRAG entity layer | People/org/place/event graph extraction | Queued |
| MCP external tools | GitHub, Drive, PostGIS, local tools | Queued |
| PostGIS/GIS layer | Spatial source lineage and map exports | Queued |
| FOIA folder automation | Request packets, exhibits, response ledgers | Queued |
| PR-IIS/Spiderweb connector | Puerto Rico investigation federation layer | Queued |
