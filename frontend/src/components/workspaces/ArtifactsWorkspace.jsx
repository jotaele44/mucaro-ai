import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { answerRecords, fileBaseName, saveFile } from "../../files.js";

const REPORT_TOOLS = ["gis.inspect", "ocr.analyze_image"];
const RECENT_RUNS = 15;

// Artifacts (TGT-003): the files Mucaro produced, in one place. Answer records come from each run's artifact events
// (the same identities the chat shows); inspection reports come from executed GIS and OCR calls, with their hashes.
export function ArtifactsWorkspace({ announce }) {
  const [answers, setAnswers] = useState(null);
  const [reports, setReports] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [runs, calls] = await Promise.all([api.workRuns(), api.toolCalls()]);
        const finished = (runs.runs || []).filter((run) => run.state === "COMPLETED").slice(0, RECENT_RUNS);
        const withRecords = await Promise.all(
          finished.map(async (run) => {
            const events = (await api.runEvents(run.run_id)).events || [];
            const artifacts = events.filter((event) => event.type === "artifact").map((event) => event.data);
            return { run, records: answerRecords(artifacts) };
          }),
        );
        if (!alive) return;
        setAnswers(withRecords.filter((row) => row.records.length));
        setReports(
          (calls.tool_calls || [])
            .filter((call) => REPORT_TOOLS.includes(call.tool_name) && call.status === "EXECUTED" && call.result?.report_sha256)
            .reverse(),
        );
      } catch (err) {
        if (alive) setError(err);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  async function download(run, record) {
    try {
      const blob = await api.exportFile(record.runId, record.kind);
      saveFile(blob, `${fileBaseName(run.prompt_preview)}-${record.suffix}`);
      announce?.(`Saved ${record.label}.`);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section className="workspace" aria-labelledby="artifacts-title" data-testid="artifacts-workspace">
      <header className="workspace-header">
        <h1 id="artifacts-title">Artifacts</h1>
        <p className="muted small">Files saved with answers, and the reports written by inspections, with the hash that identifies each.</p>
      </header>
      {error && <p className="error-card" role="alert">{error.status}: {error.message}</p>}

      <h2 className="section-title">Answers</h2>
      {answers === null && !error && <p className="muted small">Loading…</p>}
      {answers?.length === 0 && <p className="empty-state muted">No saved answers yet.</p>}
      <ul className="tool-list" aria-label="Saved answers">
        {(answers || []).map(({ run, records }) => (
          <li key={run.run_id} className="tool-row" data-testid="artifact-answer">
            <div className="tool-row-head">
              <strong>{run.prompt_preview || run.run_id}</strong>
              <span className="muted small">{run.finished_at}</span>
            </div>
            <div className="call-actions">
              {records.map((record) => (
                <button key={record.kind} type="button" className="secondary-button" onClick={() => download(run, record)}>
                  {record.label}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>

      <h2 className="section-title">Inspection reports</h2>
      {!reports.length && <p className="empty-state muted">No GIS or OCR reports yet.</p>}
      <ul className="tool-list" aria-label="Inspection reports">
        {reports.map((call) => (
          <li key={call.tool_call_id} className="tool-row" data-testid="artifact-report">
            <div className="tool-row-head">
              <span className="status-chip status-executed">{call.tool_name}</span>
              <span className="muted small">{call.result.source?.path || ""}</span>
            </div>
            <dl className="facts">
              <dt>Report</dt><dd className="mono">{call.result.report_id}</dd>
              <dt>SHA-256</dt><dd className="mono">{call.result.report_sha256}</dd>
              <dt>Input SHA-256</dt><dd className="mono">{call.result.source?.sha256 || "—"}</dd>
            </dl>
          </li>
        ))}
      </ul>
    </section>
  );
}
