import React, { useState } from "react";
import { api } from "../../api.js";

function statusClass(value) {
  if (!value) return "pending";
  if (value.includes("OK") || value.includes("HIGH")) return "completed";
  if (value.includes("WARN") || value.includes("MEDIUM")) return "queued";
  if (value.includes("NO_TEXT") || value.includes("LOW") || value.includes("UNKNOWN")) return "failed";
  return "pending";
}

function TextTiers({ text }) {
  if (!text) return null;
  return (
    <details className="panel" open>
      <summary>Text tiers</summary>
      <div className="ocr-text-grid">
        <section>
          <h3>Raw OCR</h3>
          <pre className="text-preview">{text.raw || "(no text detected)"}</pre>
        </section>
        <section>
          <h3>Normalized</h3>
          <pre className="text-preview">{text.normalized || "(empty)"}</pre>
        </section>
        <section>
          <h3>Canonical comparison</h3>
          <pre className="text-preview">{text.canonical || "(empty)"}</pre>
        </section>
      </div>
    </details>
  );
}

function SpanTable({ spans }) {
  if (!spans?.length) return <p className="empty-state muted">No OCR spans were saved.</p>;
  return (
    <div className="table-scroll">
      <table className="ocr-table">
        <caption className="sr-only">OCR span evidence</caption>
        <thead>
          <tr><th scope="col">Text</th><th scope="col">Confidence</th><th scope="col">State</th><th scope="col">Page</th><th scope="col">Bounds</th></tr>
        </thead>
        <tbody>
          {spans.map((span) => (
            <tr key={`${span.page_index}-${span.span_index}`}>
              <td>{span.raw_text}</td>
              <td>{span.confidence ?? "—"}</td>
              <td><span className={`status-chip status-${statusClass(span.confidence_state)}`}>{span.confidence_state}</span></td>
              <td>{(span.page_index ?? 0) + 1}</td>
              <td className="mono">{span.bbox ? `${span.bbox.x},${span.bbox.y} ${span.bbox.width}×${span.bbox.height}` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OCRWorkspace({ announce }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function analyze(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.ocrAnalyze(file);
      setReport(result.report);
      announce(`OCR complete for ${file.name}.`);
    } catch (err) {
      setError(err);
      setReport(null);
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  const pages = report?.operation === "pdf_page_render_ocr" ? report.pages || [] : [];
  const counts = report?.invariants || {};
  const engine = report?.engine || report?.ocr || {};

  return (
    <section className="workspace" aria-labelledby="ocr-title">
      <header className="workspace-header">
        <h1 id="ocr-title">OCR / page review</h1>
        <p className="muted small">Inspect local OCR evidence before promoting a document into your source library. OCR output is evidence, not identity proof.</p>
      </header>
      <div className="workspace-actions">
        <label className="primary-button file-button">
          {busy ? "Analyzing…" : "Analyze image or PDF"}
          <input type="file" accept="image/*,.pdf" onChange={analyze} disabled={busy} className="sr-only" />
        </label>
        {report && <button type="button" className="secondary-button" onClick={() => setReport(null)}>Clear review</button>}
      </div>
      {error && <p className="error-card" role="alert">{error.category || "OCR ERROR"} — {error.status || error.message}: {error.message || "The OCR request failed."}</p>}
      {!report && !error && <p className="empty-state muted">Choose a local image or PDF to begin a bounded OCR review.</p>}
      {report && (
        <>
          <section className="panel" aria-labelledby="ocr-summary-title">
            <div className="tool-row-head">
              <span className={`status-chip status-${statusClass(report.status)}`}>{report.status}</span>
              <h2 id="ocr-summary-title">Evidence summary</h2>
            </div>
            <dl className="facts">
              <dt>Operation</dt><dd>{report.operation}</dd>
              <dt>Source SHA-256</dt><dd className="mono">{report.source?.sha256}</dd>
              <dt>Engine</dt><dd>{engine.name || engine.engine} {engine.version || ""}</dd>
              <dt>Language / layout</dt><dd>{engine.language || "—"} · {engine.layout_mode || "—"}</dd>
              <dt>Pages / spans</dt><dd>{report.source?.page_count || 1} · {counts.total_span_count ?? counts.span_count ?? report.spans?.length ?? 0}</dd>
              <dt>Confidence</dt><dd>{counts.high_confidence_count ?? "—"} high · {counts.low_confidence_count ?? 0} low · {counts.unknown_confidence_count ?? 0} unknown</dd>
            </dl>
          </section>
          {report.text && <TextTiers text={report.text} />}
          {pages.length > 0 && (
            <section className="panel" aria-labelledby="ocr-pages-title">
              <h2 id="ocr-pages-title">Page evidence ({pages.length})</h2>
              <ol className="ocr-page-list">
                {pages.map((page) => (
                  <li key={page.page_index} className="panel nested-panel">
                    <div className="tool-row-head">
                      <span className={`status-chip status-${statusClass(page.ocr_status)}`}>{page.ocr_status}</span>
                      <strong>Page {page.page_number}</strong>
                      <span className="muted small">{page.span_count} spans</span>
                    </div>
                    <TextTiers text={page.text} />
                    {page.warnings?.length > 0 && <ul className="warning-list">{page.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
                    <p className="mono small">Rendered SHA-256: {page.rendered_sha256}</p>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {report.spans && (
            <details className="panel">
              <summary>Span coordinates and confidence ({report.spans.length})</summary>
              <SpanTable spans={report.spans} />
            </details>
          )}
          {report.warnings?.length > 0 && <section className="panel"><h2>Warnings</h2><ul className="warning-list">{report.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
          {report.limitations?.length > 0 && <details className="panel"><summary>Limitations and falsifiers</summary><ul className="warning-list">{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul>{report.falsification_criteria?.length > 0 && <><h3>Reject or review when</h3><ul className="warning-list">{report.falsification_criteria.map((item) => <li key={item}>{item}</li>)}</ul></>}</details>}
        </>
      )}
    </section>
  );
}
