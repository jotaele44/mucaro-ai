CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_gates (
    task_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interactions (
    interaction_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_runs (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    queue_seq INTEGER NOT NULL,
    state TEXT NOT NULL,
    idempotency_key TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS work_runs_idempotency
    ON work_runs(conversation_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS work_runs_conversation ON work_runs(conversation_id, queue_seq);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS execution_entry_artifacts (
    artifact_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    mode TEXT NOT NULL,
    state TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (artifact_id, revision)
);
CREATE INDEX IF NOT EXISTS execution_entry_artifacts_conversation
    ON execution_entry_artifacts(conversation_id, revision);
CREATE TABLE IF NOT EXISTS execution_entry_decisions (
    decision_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(artifact_id, revision) REFERENCES execution_entry_artifacts(artifact_id, revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS execution_entry_active_claim
    ON execution_entry_decisions(artifact_id, revision)
    WHERE state IN ('CLAIMED','DISPATCHED');
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);