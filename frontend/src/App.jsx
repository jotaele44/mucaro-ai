import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api.js";
import { DirectoryIntake } from "./components/DirectoryIntake.jsx";
import { PlanningGate } from "./components/PlanningGate.jsx";
import "./styles.css";

function App() {
  const [health, setHealth] = useState(null);
  const [sources, setSources] = useState([]);
  const [indexStatus, setIndexStatus] = useState(null);
  const [chunks, setChunks] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [runSummary, setRunSummary] = useState(null);
  const [config, setConfig] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [answer, setAnswer] = useState(null);
  const [selectedSourceId, setSelectedSourceId] = useState(null);
  const [retrievalStrategy, setRetrievalStrategy] = useState("hybrid");
  const [operatorToken, setOperatorToken] = useState(api.getOperatorToken());
  const [chatFilter, setChatFilter] = useState("");
  const [renameTitle, setRenameTitle] = useState("");
  const [query, setQuery] = useState("How does source lineage work?");
  const [status, setStatus] = useState("Idle");
  const [error, setError] = useState(null);

  async function refresh() {
    setHealth(await api.health());
    setConfig(await api.config());
    const src = await api.sources();
    setSources(src.sources || []);
    setIndexStatus(src.index || null);
    const chunkPayload = await api.chunks();
    setChunks(chunkPayload.chunks || []);
    const conversationPayload = await api.conversations();
    setConversations(conversationPayload.conversations || []);
    setRunSummary(await api.runSummary());
    const workspacePayload = await api.workspaces();
    setWorkspaces(workspacePayload.workspaces || []);
  }

  useEffect(() => { refresh().catch((err) => setStatus(err.message)); }, []);

  async function upload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setStatus("Uploading...");
    setError(null);
    try {
      const result = await api.ingest(file);
      setStatus(result.status || "INGEST_OK");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("UPLOAD_ERROR");
    } finally {
      e.target.value = "";
    }
  }

  async function ask() {
    setStatus("Asking...");
    setError(null);
    try {
      let conversation = activeConversation;
      if (!conversation) {
        conversation = await api.createConversation({ title: "New chat" });
        setActiveConversation(conversation);
      }
      const payload = await api.askConversation(conversation.conversation_id, { query, top_k: 5, retrieval_strategy: retrievalStrategy, model_provider: "mock" });
      const result = payload.answer;
      setAnswer(result);
      setActiveConversation(payload.conversation);
      setStatus(result.citation_status || result.status || "DONE");
      const conversationPayload = await api.conversations();
      setConversations(conversationPayload.conversations || []);
    } catch (err) {
      setError(err.message);
      setStatus("ASK_ERROR");
    }
  }

  async function reindex() {
    setStatus("Reindexing...");
    setError(null);
    try {
      const result = await api.reindexSources();
      setStatus(result.status || "REINDEX_OK");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("REINDEX_ERROR");
    }
  }

  async function removeSelectedSource() {
    if (!selectedSource) return;
    setStatus("Deleting source...");
    setError(null);
    try {
      const result = await api.deleteSource(selectedSource.source_id);
      setStatus(result.status || "SOURCE_DELETE_OK");
      setSelectedSourceId(null);
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("SOURCE_DELETE_ERROR");
    }
  }

  async function saveCurrentWorkspace() {
    setStatus("Saving workspace...");
    setError(null);
    try {
      const result = await api.saveWorkspace({ name: config?.workspace_profile || "default", notes: "Saved from operator console." });
      setStatus(result.status || "WORKSPACE_PROFILE_SAVED");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("WORKSPACE_ERROR");
    }
  }

  async function saveOperatorToken() {
    api.setOperatorToken(operatorToken);
    setStatus(operatorToken.trim() ? "OPERATOR_TOKEN_SAVED" : "OPERATOR_TOKEN_CLEARED");
    await refresh();
  }

  async function newChat() {
    setStatus("Creating chat...");
    setError(null);
    try {
      const conversation = await api.createConversation({ title: "New chat" });
      setActiveConversation(conversation);
      setRenameTitle(conversation.title || "New chat");
      setAnswer(null);
      setStatus("CHAT_READY");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("CHAT_ERROR");
    }
  }

  async function selectConversation(conversationId) {
    setStatus("Loading chat...");
    setError(null);
    try {
      const conversation = await api.conversation(conversationId);
      setActiveConversation(conversation);
      setRenameTitle(conversation.title || "");
      const lastAssistant = [...(conversation.turns || [])].reverse().find((turn) => turn.role === "assistant");
      setAnswer(lastAssistant?.answer_trace || null);
      setStatus("CHAT_READY");
    } catch (err) {
      setError(err.message);
      setStatus("CHAT_ERROR");
    }
  }

  async function renameActiveConversation() {
    if (!activeConversation || !renameTitle.trim()) return;
    setStatus("Renaming chat...");
    setError(null);
    try {
      const conversation = await api.renameConversation(activeConversation.conversation_id, { title: renameTitle });
      setActiveConversation(conversation);
      setStatus("CHAT_RENAMED");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("CHAT_RENAME_ERROR");
    }
  }

  async function deleteActiveConversation() {
    if (!activeConversation) return;
    setStatus("Deleting chat...");
    setError(null);
    try {
      await api.deleteConversation(activeConversation.conversation_id);
      setActiveConversation(null);
      setRenameTitle("");
      setAnswer(null);
      setStatus("CHAT_DELETED");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("CHAT_DELETE_ERROR");
    }
  }

  const selectedSource = sources.find((source) => source.source_id === selectedSourceId) || sources[0];
  const filteredConversations = conversations.filter((conversation) => {
    const needle = chatFilter.trim().toLowerCase();
    if (!needle) return true;
    return `${conversation.title || ""} ${conversation.last_status || ""}`.toLowerCase().includes(needle);
  });
  const citedChunks = answer
    ? chunks.filter((chunk) => (answer.cited_chunk_ids || []).includes(chunk.chunk_id))
    : [];
  const validationFlags = answer?.validation_flags || [];
  const hasNoSourceAnswer = answer?.status === "ERROR_NO_SOURCES";

  return (
    <main>
      <aside>
        <h1>Mucaro AI</h1>
        <nav>
          <a>Mission Control</a>
          <a>Planning Gate</a>
          <a>File Intake</a>
          <a>Directory Access</a>
          <a>Chat Sessions</a>
          <a>Source Manifest</a>
          <a>RAG Chat</a>
          <a>Citation Inspector</a>
          <a>Export Center</a>
        </nav>
      </aside>
      <section className="workspace">
        <header>
          <div><strong>Status:</strong> {status}</div>
          <div><strong>Backend:</strong> {health ? health.status : "offline"}</div>
          <div><strong>Provider:</strong> {health ? health.model_provider : "unknown"}</div>
          <div><strong>Auth:</strong> {config?.auth?.status || "unknown"}</div>
        </header>
        {error && <div className="notice error">{error}</div>}
        <PlanningGate />
        <section className="panel">
          <h2>File Intake</h2>
          <input type="file" onChange={upload} />
          {sources.length === 0 && <p className="hint">No sources ingested yet. Add a TXT, MD, CSV, JSON, or PDF file before asking source-grounded questions.</p>}
        </section>
        <DirectoryIntake onIngested={refresh} />
        <section className="panel">
          <div className="panel-header">
            <h2>Workspace Profile</h2>
            <button onClick={saveCurrentWorkspace}>Save Profile</button>
          </div>
          <dl>
            <div><dt>Active</dt><dd>{config?.workspace_profile || "default"}</dd></div>
            <div><dt>Project Root</dt><dd>{config?.project_root || "unknown"}</dd></div>
            <div><dt>Data Dir</dt><dd>{config?.data_dir || "unknown"}</dd></div>
            <div><dt>Profiles</dt><dd>{workspaces.map((workspace) => workspace.name).join(", ") || "none"}</dd></div>
          </dl>
          <div className="token-row">
            <label>
              Operator Token
              <input
                value={operatorToken}
                onChange={(event) => setOperatorToken(event.target.value)}
                type="password"
                placeholder={config?.auth?.auth_required ? "Required for mutating actions" : "Optional"}
              />
            </label>
            <button onClick={saveOperatorToken}>Use Token</button>
          </div>
        </section>
        <section className="panel">
          <div className="panel-header">
            <h2>Source Manifest</h2>
            <div className="button-row">
              <span className="badge">{indexStatus?.status || "INDEX_UNKNOWN"}</span>
              <button onClick={reindex}>Reindex</button>
              <button className="secondary" onClick={removeSelectedSource} disabled={!selectedSource}>Delete Source</button>
            </div>
          </div>
          <table>
            <thead><tr><th>Source</th><th>File</th><th>Status</th><th>Chunks</th><th>Parser</th></tr></thead>
            <tbody>{sources.map((s) => (
              <tr key={s.source_id} onClick={() => setSelectedSourceId(s.source_id)} className={selectedSource?.source_id === s.source_id ? "selected-row" : ""}>
                <td>{s.source_id}</td>
                <td>{s.file_name}</td>
                <td>{s.lineage_status}</td>
                <td>{s.chunk_count}</td>
                <td>{s.parser}</td>
              </tr>
            ))}</tbody>
          </table>
          {selectedSource && (
            <div className="source-detail">
              <h3>{selectedSource.file_name}</h3>
              <dl>
                <div><dt>SHA256</dt><dd>{selectedSource.source_sha256}</dd></div>
                <div><dt>Bytes</dt><dd>{selectedSource.bytes ?? "unknown"}</dd></div>
                <div><dt>Parse</dt><dd>{selectedSource.parse_status || "unknown"}</dd></div>
                <div><dt>Warnings</dt><dd>{(selectedSource.warnings || []).join(", ") || "none"}</dd></div>
                <div><dt>Path</dt><dd>{selectedSource.source_path || "unknown"}</dd></div>
              </dl>
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-header">
            <h2>RAG Chat</h2>
            <button onClick={newChat}>New Chat</button>
          </div>
          {runSummary && (
            <div className="summary-strip">
              <span>Runs: {runSummary.run_count}</span>
              <span>Cited OK: {runSummary.citation_ok}</span>
              <span>Latest: {runSummary.latest_run_id || "none"}</span>
            </div>
          )}
          <div className="chat-layout">
            <section className="chat-list">
              <h3>Chat Sessions</h3>
              <input
                className="compact-input"
                value={chatFilter}
                onChange={(event) => setChatFilter(event.target.value)}
                placeholder="Search chats"
              />
              {conversations.length === 0 && <p className="hint">No saved chats yet.</p>}
              {conversations.length > 0 && filteredConversations.length === 0 && <p className="hint">No chats match the filter.</p>}
              {filteredConversations.map((conversation) => (
                <button
                  className={activeConversation?.conversation_id === conversation.conversation_id ? "chat-tab active" : "chat-tab"}
                  key={conversation.conversation_id}
                  onClick={() => selectConversation(conversation.conversation_id)}
                >
                  <span>{conversation.title}</span>
                  <small>{conversation.turn_count} turns</small>
                </button>
              ))}
            </section>
            <section className="chat-surface">
              {activeConversation && (
                <div className="chat-management">
                  <input
                    value={renameTitle}
                    onChange={(event) => setRenameTitle(event.target.value)}
                    placeholder="Chat title"
                  />
                  <button onClick={renameActiveConversation}>Rename</button>
                  <button className="secondary" onClick={deleteActiveConversation}>Delete</button>
                </div>
              )}
              {activeConversation && (
                <div className="turn-list">
                  {(activeConversation.turns || []).map((turn) => (
                    <article className={`turn ${turn.role}`} key={turn.turn_id}>
                      <strong>{turn.role}</strong>
                      <p>{turn.content}</p>
                    </article>
                  ))}
                </div>
              )}
              <label className="inline-control">
                Retrieval
                <select value={retrievalStrategy} onChange={(e) => setRetrievalStrategy(e.target.value)}>
                  <option value="hybrid">hybrid</option>
                  <option value="persistent">persistent</option>
                  <option value="vector">vector</option>
                  <option value="lexical">lexical</option>
                </select>
              </label>
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
              <button onClick={ask}>Ask with citations</button>
              {hasNoSourceAnswer && <div className="notice">No sources are available yet. Ingest a file first, then ask again.</div>}
              {answer?.status === "ERROR_NO_RETRIEVAL_CONTEXT" && <div className="notice">No matching context was found. Try a broader query, switch retrieval strategy, or reindex sources.</div>}
            </section>
          </div>
          {answer && !answer.status && (
            <div className="answer-grid">
              <section>
                <h3>Answer</h3>
                <p>{answer.answer}</p>
                <dl>
                  <div><dt>Run</dt><dd>{answer.run_id}</dd></div>
                  <div><dt>Model</dt><dd>{answer.model_provider} / {answer.model_name}</dd></div>
                  <div><dt>Retrieval</dt><dd>{answer.retrieval_strategy || "unknown"}</dd></div>
                  <div><dt>History Turns</dt><dd>{answer.conversation_context_turn_count ?? 0}</dd></div>
                  <div><dt>Confidence</dt><dd>{answer.confidence}</dd></div>
                </dl>
                {answer.run_id && <a className="export-link" href={`${api.baseUrl}/exports/${answer.run_id}`}>Open JSON export</a>}
              </section>
              <section>
                <h3>Cited Chunks</h3>
                {citedChunks.length === 0 && <p className="hint">No cited chunks were returned.</p>}
                {citedChunks.map((chunk) => (
                  <article className="chunk" key={chunk.chunk_id}>
                    <strong>{chunk.chunk_id}</strong>
                    <small>{chunk.source_id}</small>
                    <p>{chunk.text}</p>
                  </article>
                ))}
              </section>
              <section>
                <h3>Validation Flags</h3>
                <ul>{validationFlags.length ? validationFlags.map((flag) => <li key={flag}>{flag}</li>) : <li>OK</li>}</ul>
              </section>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
