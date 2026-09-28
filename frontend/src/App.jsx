import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api.js";
import { DirectoryIntake } from "./components/DirectoryIntake.jsx";
import { PlanningGate } from "./components/PlanningGate.jsx";
import "./styles.css";

// Mirrors SUPPORTED_UPLOAD_SUFFIXES in backend/app/main.py so the iOS picker offers only ingestible files.
const UPLOAD_ACCEPT = ".txt,.md,.csv,.json,.pdf,.png,.jpg,.jpeg,.gif,.webp,.wav,.mp3,.m4a,.mp4,.mov,.webm";

function App() {
  const [health, setHealth] = useState(null);
  const [sources, setSources] = useState([]);
  const [indexStatus, setIndexStatus] = useState(null);
  const [chunks, setChunks] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [runSummary, setRunSummary] = useState(null);
  const [memory, setMemory] = useState([]);
  const [config, setConfig] = useState(null);
  const [workspaces, setWorkspaces] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [answer, setAnswer] = useState(null);
  const [selectedSourceId, setSelectedSourceId] = useState(null);
  const [retrievalStrategy, setRetrievalStrategy] = useState("hybrid");
  const [operatorToken, setOperatorToken] = useState(api.getOperatorToken());
  const [chatFilter, setChatFilter] = useState("");
  const [renameTitle, setRenameTitle] = useState("");
  const [memoryContent, setMemoryContent] = useState("");
  const [memoryKind, setMemoryKind] = useState("preference");
  const [query, setQuery] = useState("How does source lineage work?");
  const [status, setStatus] = useState("Idle");
  const [error, setError] = useState(null);
  const [view, setView] = useState("chat");
  const [connection, setConnection] = useState("checking");
  const [asking, setAsking] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const [stopped, setStopped] = useState(false);

  async function refresh() {
    let healthPayload;
    try {
      healthPayload = await api.health();
    } catch (err) {
      setHealth(null);
      setConnection("offline");
      err.connectionFailure = true;
      throw err;
    }
    setHealth(healthPayload);
    setConnection("online");
    setConfig(await api.config());
    const src = await api.sources();
    setSources(src.sources || []);
    setIndexStatus(src.index || null);
    const chunkPayload = await api.chunks();
    setChunks(chunkPayload.chunks || []);
    const conversationPayload = await api.conversations();
    setConversations(conversationPayload.conversations || []);
    setRunSummary(await api.runSummary());
    const memoryPayload = await api.memory();
    setMemory(memoryPayload.memories || []);
    const workspacePayload = await api.workspaces();
    setWorkspaces(workspacePayload.workspaces || []);
  }

  async function loadWorkspace() {
    try {
      await refresh();
      setStatus("Idle");
    } catch (err) {
      setStatus(err.connectionFailure ? "BACKEND_OFFLINE" : err.message);
      if (!err.connectionFailure) setError(err.message);
    }
  }

  useEffect(() => { loadWorkspace(); }, []);

  async function quitApp() {
    if (!window.confirm("Quit Mucaro AI? It stops on this computer until you double-click Mucaro AI again.")) return;
    setQuitting(true);
    try {
      await api.quit();
      setStopped(true);
    } catch (err) {
      setError(`Quit failed: ${err.message}`);
    } finally {
      setQuitting(false);
    }
  }

  async function retryConnection() {
    setConnection("checking");
    setStatus("Reconnecting...");
    setError(null);
    await loadWorkspace();
  }

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
    if (asking || !query.trim()) return;
    setAsking(true);
    setStatus("Asking...");
    setError(null);
    try {
      let conversation = activeConversation;
      if (!conversation) {
        conversation = await api.createConversation({ title: "New chat" });
        setActiveConversation(conversation);
      }
      // No model_provider: the backend applies its configured default (MUCARO_AI_MODEL_PROVIDER).
      const payload = await api.askConversation(conversation.conversation_id, { query, top_k: 5, retrieval_strategy: retrievalStrategy });
      const result = payload.answer;
      setAnswer(result);
      setActiveConversation(payload.conversation);
      setQuery("");
      setStatus(result.citation_status || result.status || "DONE");
      const conversationPayload = await api.conversations();
      setConversations(conversationPayload.conversations || []);
    } catch (err) {
      setError(err.message);
      setStatus("ASK_ERROR");
    } finally {
      setAsking(false);
    }
  }

  function handleComposerKeyDown(event) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      ask();
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
    if (!window.confirm(`Delete source "${selectedSource.file_name}" from the library? This cannot be undone.`)) return;
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
    setError(null);
    try {
      await refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  async function rememberPreference() {
    if (!memoryContent.trim()) return;
    setStatus("Saving memory...");
    setError(null);
    try {
      const result = await api.remember({ kind: memoryKind, content: memoryContent });
      setMemoryContent("");
      setStatus(result.status || "MEMORY_SAVED");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("MEMORY_ERROR");
    }
  }

  async function toggleMemory(item) {
    setStatus("Updating memory...");
    setError(null);
    try {
      const result = await api.updateMemory(item.memory_id, { enabled: !item.enabled });
      setStatus(result.status || "MEMORY_UPDATED");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("MEMORY_ERROR");
    }
  }

  async function deleteMemory(item) {
    if (!window.confirm("Delete this memory item? This cannot be undone.")) return;
    setStatus("Deleting memory...");
    setError(null);
    try {
      const result = await api.deleteMemory(item.memory_id);
      setStatus(result.status || "MEMORY_DELETE_OK");
      await refresh();
    } catch (err) {
      setError(err.message);
      setStatus("MEMORY_ERROR");
    }
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
    if (!window.confirm(`Delete chat "${activeConversation.title || "Untitled"}"? This cannot be undone.`)) return;
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

  const navItems = [
    ["chat", "Chat"],
    ["sources", "Sources"],
    ["access", "Directory Access"],
    ["memory", "Memory"],
    ["planning", "Planning"],
    ["settings", "Settings"],
  ];

  if (stopped) {
    return (
      <main className="stopped-screen">
        <section className="panel" role="status">
          <h1>Mucaro AI has stopped</h1>
          <p>You can close this tab. Double-click Mucaro AI to start it again.</p>
        </section>
      </main>
    );
  }

  const quitSupported = Boolean(config?.desktop?.quit_supported);

  return (
    <main className="app-shell">
      <aside className="app-nav">
        <div className="brand-block">
          <h1>Mucaro AI</h1>
          <p>Local-first evidence workbench</p>
        </div>
        <nav aria-label="Primary workspace">
          {navItems.map(([id, label]) => (
            <button
              key={id}
              className={view === id ? "nav-button active" : "nav-button"}
              onClick={() => setView(id)}
              aria-current={view === id ? "page" : undefined}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="nav-health">
          <span className={`status-dot ${connection}`} aria-hidden="true" />
          <div><strong>{connection === "checking" ? "connecting" : health ? health.status : "offline"}</strong><small>{status}</small></div>
        </div>
        {quitSupported && (
          <button className="quit-button" onClick={quitApp} disabled={quitting}>{quitting ? "Quitting..." : "Quit Mucaro AI"}</button>
        )}
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div>
            <span className="eyebrow">Workspace</span>
            <h2>{navItems.find(([id]) => id === view)?.[1]}</h2>
          </div>
          <div className="header-facts">
            <span>Provider <strong>{health ? health.model_provider : "unknown"}</strong></span>
            <span>Auth <strong>{config?.auth?.status || "unknown"}</strong></span>
            <span>Index <strong>{indexStatus?.status || "unknown"}</strong></span>
          </div>
        </header>
        {connection === "offline" && (
          <div className="notice error connection-banner" role="alert">
            <div>
              <strong>Backend not reachable</strong>
              <p>
                Mucaro could not reach its local service at {api.displayUrl}. Start it on the computer that hosts your
                sources by double-clicking <code>Mucaro AI</code> (or with <code>python scripts/launch_app.py</code>), then retry.
              </p>
            </div>
            <button onClick={retryConnection}>Retry</button>
          </div>
        )}
        {error && <div className="notice error" role="alert">{error}</div>}

        {view === "chat" && (
          <section className="panel conversation-workspace">
            <div className="panel-header">
              <div>
                <span className="eyebrow">Primary surface</span>
                <h2>Conversation</h2>
              </div>
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
                <h3>Sessions</h3>
                <input className="compact-input" type="search" value={chatFilter} onChange={(event) => setChatFilter(event.target.value)} placeholder="Search chats" aria-label="Search chats" />
                {conversations.length === 0 && <p className="hint">No saved chats yet.</p>}
                {conversations.length > 0 && filteredConversations.length === 0 && <p className="hint">No chats match the filter.</p>}
                {filteredConversations.map((conversation) => (
                  <button className={activeConversation?.conversation_id === conversation.conversation_id ? "chat-tab active" : "chat-tab"} key={conversation.conversation_id} onClick={() => selectConversation(conversation.conversation_id)}>
                    <span>{conversation.title}</span>
                    <small>{conversation.turn_count} turns</small>
                  </button>
                ))}
              </section>
              <section className="chat-surface">
                {activeConversation && (
                  <div className="chat-management">
                    <input value={renameTitle} onChange={(event) => setRenameTitle(event.target.value)} placeholder="Chat title" aria-label="Chat title" />
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
                <div className="composer">
                  <label className="inline-control">
                    Retrieval
                    <select value={retrievalStrategy} onChange={(event) => setRetrievalStrategy(event.target.value)}>
                      <option value="hybrid">hybrid</option><option value="semantic">semantic</option><option value="persistent">persistent</option>
                      <option value="bm25">bm25</option><option value="vector">vector</option><option value="lexical">lexical</option>
                    </select>
                  </label>
                  <textarea value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={handleComposerKeyDown} aria-label="Question" placeholder="Ask a question about your sources" />
                  <button onClick={ask} disabled={asking || !query.trim()}>{asking ? "Asking…" : "Ask with citations"}</button>
                </div>
                <p className="hint composer-hint">Answers cite the retrieved source chunks. Press ⌘/Ctrl + Enter to send.</p>
                {hasNoSourceAnswer && <div className="notice">No sources are available. Add them in the Sources workspace.</div>}
                {answer?.status === "ERROR_NO_RETRIEVAL_CONTEXT" && <div className="notice">No matching context was found. Broaden the query, switch retrieval strategy, or reindex in Sources.</div>}
              </section>
            </div>
            {answer && !answer.status && (
              <div className="evidence-drawer">
                <section>
                  <h3>Answer evidence</h3>
                  <p className="answer-copy">{answer.answer}</p>
                  <dl>
                    <div><dt>Run</dt><dd>{answer.run_id}</dd></div>
                    <div><dt>Model</dt><dd>{answer.model_provider} / {answer.model_name}</dd></div>
                    <div><dt>Retrieval</dt><dd>{answer.retrieval_strategy || "unknown"}</dd></div>
                    <div><dt>Confidence</dt><dd>{answer.confidence}</dd></div>
                  </dl>
                  {answer.run_id && <a className="export-link" href={`${api.baseUrl}/exports/${answer.run_id}`} target="_blank" rel="noopener noreferrer">Open JSON trace</a>}
                </section>
                <section>
                  <h3>Cited chunks</h3>
                  {citedChunks.length === 0 && <p className="hint">No cited chunks were returned.</p>}
                  {citedChunks.map((chunk) => <article className="chunk" key={chunk.chunk_id}><strong>{chunk.chunk_id}</strong><small>{chunk.source_id}</small><p>{chunk.text}</p></article>)}
                </section>
                <section>
                  <h3>Validation</h3>
                  <ul>{validationFlags.length ? validationFlags.map((flag) => <li key={flag}>{flag}</li>) : <li>OK</li>}</ul>
                </section>
              </div>
            )}
          </section>
        )}

        {view === "sources" && (
          <>
            <section className="panel">
              <div className="panel-header"><h2>File Intake</h2><span className="badge">{sources.length} sources</span></div>
              <label className="file-picker">
                Add a file
                <input type="file" accept={UPLOAD_ACCEPT} onChange={upload} />
              </label>
              <p className="hint">
                Supported: text, Markdown, CSV, JSON, PDF, images (PNG, JPEG, GIF, WebP), audio (WAV, MP3, M4A), and video
                (MP4, MOV, WebM). Media files are indexed by file metadata only.
              </p>
              {sources.length === 0 && <p className="hint">No sources ingested yet.</p>}
            </section>
            <section className="panel">
              <div className="panel-header">
                <h2>Source Manifest</h2>
                <div className="button-row"><button onClick={reindex}>Reindex</button><button className="secondary" onClick={removeSelectedSource} disabled={!selectedSource}>Delete Source</button></div>
              </div>
              <div className="table-scroll">
                <table><thead><tr><th>Source</th><th>File</th><th>Status</th><th>Chunks</th><th>Parser</th></tr></thead>
                <tbody>{sources.map((source) => <tr key={source.source_id} tabIndex={0} onClick={() => setSelectedSourceId(source.source_id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedSourceId(source.source_id); } }} className={selectedSource?.source_id === source.source_id ? "selected-row" : ""}><td>{source.source_id}</td><td>{source.file_name}</td><td>{source.lineage_status}</td><td>{source.chunk_count}</td><td>{source.parser}</td></tr>)}</tbody></table>
              </div>
              {selectedSource && <div className="source-detail"><h3>{selectedSource.file_name}</h3><dl><div><dt>SHA256</dt><dd>{selectedSource.source_sha256}</dd></div><div><dt>Bytes</dt><dd>{selectedSource.bytes ?? "unknown"}</dd></div><div><dt>Parse</dt><dd>{selectedSource.parse_status || "unknown"}</dd></div><div><dt>Warnings</dt><dd>{(selectedSource.warnings || []).join(", ") || "none"}</dd></div><div><dt>Path</dt><dd>{selectedSource.source_path || "unknown"}</dd></div></dl></div>}
            </section>
          </>
        )}

        {view === "access" && <DirectoryIntake onIngested={refresh} />}

        {view === "memory" && (
          <section className="panel">
            <div className="panel-header"><h2>Memory</h2><a className="export-link" href={`${api.baseUrl}/memory/export`} target="_blank" rel="noopener noreferrer">Export JSON</a></div>
            <p className="hint">Memory supplies bounded preference/profile context and is never treated as cited source evidence.</p>
            <div className="memory-form">
              <select value={memoryKind} onChange={(event) => setMemoryKind(event.target.value)} aria-label="Memory kind"><option value="preference">preference</option><option value="profile">profile</option><option value="instruction">instruction</option><option value="fact">fact</option></select>
              <input value={memoryContent} onChange={(event) => setMemoryContent(event.target.value)} placeholder="Save explicit context" aria-label="Memory content" />
              <button onClick={rememberPreference} disabled={!memoryContent.trim()}>Remember</button>
            </div>
            <div className="memory-list">
              {memory.length === 0 && <p className="hint">No saved memory items.</p>}
              {memory.map((item) => <article className={item.enabled ? "memory-item" : "memory-item disabled"} key={item.memory_id}><div><strong>{item.kind}</strong><p>{item.content}</p><small>{item.memory_id}</small></div><div className="button-row"><button className="secondary" onClick={() => toggleMemory(item)}>{item.enabled ? "Disable" : "Enable"}</button><button className="secondary" onClick={() => deleteMemory(item)}>Delete</button></div></article>)}
            </div>
          </section>
        )}

        {view === "planning" && <PlanningGate />}

        {view === "settings" && (
          <section className="panel">
            <div className="panel-header"><h2>Workspace & Security</h2><button onClick={saveCurrentWorkspace}>Save Profile</button></div>
            <dl><div><dt>Active</dt><dd>{config?.workspace_profile || "default"}</dd></div><div><dt>Project Root</dt><dd>{config?.project_root || "unknown"}</dd></div><div><dt>Data Dir</dt><dd>{config?.data_dir || "unknown"}</dd></div><div><dt>Profiles</dt><dd>{workspaces.map((workspace) => workspace.name).join(", ") || "none"}</dd></div></dl>
            <div className="token-row"><label>Operator Token<input value={operatorToken} onChange={(event) => setOperatorToken(event.target.value)} type="password" placeholder={config?.auth?.auth_required ? "Required for mutating actions" : "Optional"} /></label><button onClick={saveOperatorToken}>Use Token</button></div>
            {quitSupported && (
              <div className="quit-row"><p>Mucaro AI keeps running in the background until you quit it.</p><button onClick={quitApp} disabled={quitting}>{quitting ? "Quitting..." : "Quit Mucaro AI"}</button></div>
            )}
          </section>
        )}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
