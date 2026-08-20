import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api.js";
import { PlanningGate } from "./components/PlanningGate.jsx";
import "./styles.css";

function App() {
  const [health, setHealth] = useState(null);
  const [sources, setSources] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [query, setQuery] = useState("How does source lineage work?");
  const [status, setStatus] = useState("Idle");

  async function refresh() {
    setHealth(await api.health());
    const src = await api.sources();
    setSources(src.sources || []);
  }

  useEffect(() => { refresh().catch((err) => setStatus(err.message)); }, []);

  async function upload(e) {
    const file = e.target.files[0];
    if (!file) return;
    setStatus("Uploading...");
    await api.ingest(file);
    setStatus("INGEST_OK");
    await refresh();
  }

  async function ask() {
    setStatus("Asking...");
    const result = await api.ask({ query, top_k: 5, model_provider: "mock" });
    setAnswer(result);
    setStatus(result.citation_status || result.status || "DONE");
  }

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
        <PlanningGate />
        <section className="panel">
          <h2>File Intake</h2>
          <input type="file" onChange={upload} />
        </section>
        <section className="panel">
          <h2>Source Manifest</h2>
          <table>
            <thead><tr><th>Source</th><th>File</th><th>Status</th><th>Chunks</th></tr></thead>
            <tbody>{sources.map((s) => <tr key={s.source_id}><td>{s.source_id}</td><td>{s.file_name}</td><td>{s.lineage_status}</td><td>{s.chunk_count}</td></tr>)}</tbody>
          </table>
        </section>
        <section className="panel">
          <h2>RAG Chat</h2>
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
          <button onClick={ask}>Ask with citations</button>
          {answer && <pre>{JSON.stringify(answer, null, 2)}</pre>}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
