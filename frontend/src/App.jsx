import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api.js";
import { PlanningGate } from "./components/PlanningGate.jsx";
import "./styles.css";

function App() {
  const [health, setHealth] = useState(null);
  const [sources, setSources] = useState([]);
  const [chunks, setChunks] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [selectedSourceId, setSelectedSourceId] = useState(null);
  const [query, setQuery] = useState("How does source lineage work?");
  const [status, setStatus] = useState("Idle");
  const [error, setError] = useState(null);

  async function refresh() {
    setHealth(await api.health());
    const src = await api.sources();
    setSources(src.sources || []);
    const chunkPayload = await api.chunks();
    setChunks(chunkPayload.chunks || []);
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
      const result = await api.ask({ query, top_k: 5, model_provider: "mock" });
      setAnswer(result);
      setStatus(result.citation_status || result.status || "DONE");
    } catch (err) {
      setError(err.message);
      setStatus("ASK_ERROR");
    }
  }

  const selectedSource = sources.find((source) => source.source_id === selectedSourceId) || sources[0];
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
        </header>
        {error && <div className="notice error">{error}</div>}
        <PlanningGate />
        <section className="panel">
          <h2>File Intake</h2>
          <input type="file" onChange={upload} />
          {sources.length === 0 && <p className="hint">No sources ingested yet. Add a TXT, MD, CSV, JSON, or PDF file before asking source-grounded questions.</p>}
        </section>
        <section className="panel">
          <h2>Source Manifest</h2>
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
              </dl>
            </div>
          )}
        </section>
        <section className="panel">
          <h2>RAG Chat</h2>
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
          <button onClick={ask}>Ask with citations</button>
          {hasNoSourceAnswer && <div className="notice">No sources are available yet. Ingest a file first, then ask again.</div>}
          {answer && !answer.status && (
            <div className="answer-grid">
              <section>
                <h3>Answer</h3>
                <p>{answer.answer}</p>
                <dl>
                  <div><dt>Run</dt><dd>{answer.run_id}</dd></div>
                  <div><dt>Model</dt><dd>{answer.model_provider} / {answer.model_name}</dd></div>
                  <div><dt>Retrieval</dt><dd>{answer.retrieval_strategy || "unknown"}</dd></div>
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
