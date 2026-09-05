# Phase 2 Queue

| Item | Purpose | Status |
|---|---|---|
| Local hybrid retrieval | Deterministic lexical plus TF-IDF vector scoring | Complete in V1 |
| Retrieval eval harness | recall@k, top-1, and no-context gate for retrieval changes | Complete in V1 |
| Persistent local TF-IDF index | Local stored vector index with explicit `persistent` strategy | Complete in V1.4 |
| Local BM25 retrieval | Opt-in BM25 ranking for stronger local term matching | Complete candidate in V1.14 |
| Explicit local memory | Opt-in preference/profile memory with inspect, disable, delete, export, and answer-context wiring | Complete candidate in V1.20 |
| Production security profile | Single-operator production auth, explicit CORS, public-bind guard, startup validation | Complete candidate in V1.21 |
| Intelligence Plane descriptor | Machine-readable provisional repo role/capability declaration | Candidate in Intelligence Plane foundation |
| Federation Skill contract | Immutable skill identity/version/digest/runtime/capability schema | Candidate in Intelligence Plane foundation |
| Skill mount runtime | Mount → authorize → activate → execute lifecycle with local execution ledger | Candidate in Intelligence Plane foundation |
| Execution contract | Runtime/execution identity and status schema | Candidate in Intelligence Plane foundation |
| Model runtime contract | Provider/model/mode/capability schema independent of provider implementation | Candidate in Intelligence Plane foundation |
| Provider-neutral model runtime | Replace narrow provider branching with adapter registry while preserving fail-closed live-mode behavior | Next |
| Local worker runtime adapter | Isolate general skills from the app process | Next |
| Container runtime adapter | Reproducible heavy GIS/OCR/media execution with pinned runtime identity | Queued |
| Execution router | Choose IN_PROCESS / LOCAL_WORKER / CONTAINER / SERVICE / FEDERATION_WORKER by policy, never by skill fiat | Queued |
| Capability policy engine | Bind filesystem/network/database/subprocess/model permissions to explicit policy | Next |
| Artifact identity/provenance | Input/output handles, SHA-256 identity, staged commit, execution receipts | Next |
| Agent/workflow DAG runtime | Durable multi-step plans with explicit state, restart/resume, and receipts | Queued |
| Federation membership discovery | Consume canonical membership and repo capability descriptors once federation environment is available | Blocked on federation execution environment |
| FEDERATION LOCKSTEP client | Compatibility/impact gate for skill activation and federation-visible contracts | Blocked on federation execution environment |
| Cross-repo orchestration | Invoke contract-bound domain capabilities while preserving repo autonomy | Blocked on federation execution environment |
| Chroma integration | Persistent embedding/vector store for larger corpora | Planned in `docs/RETRIEVAL_UPGRADE_PLAN.md`; queued after larger eval corpus |
| LlamaIndex ingestion | Richer document ingestion and node parsing | Queued |
| Unstructured parser expansion | Better PDFs, richer DOCX/HTML extraction, emails, and model-backed media understanding beyond metadata | Queued |
| GraphRAG entity layer | People/org/place/event graph extraction | Queued |
| MCP adapter | External protocol adapter beneath the Skill Fabric; not a separate authority model | Queued |
| PostGIS/GIS service adapter | Spatial source lineage and map exports behind explicit service capability | Queued |
| FOIA folder automation | Request packets, exhibits, response ledgers | Queued |
| PR-IIS/Spiderweb connector | Puerto Rico investigation federation layer | Blocked on federation execution environment |

## Architecture rule

Tool protocols, model providers, containers, local workers, and future federation workers are execution adapters. They do not bypass Skill Fabric identity, capability authorization, activation, execution-ledger, or certification boundaries.
