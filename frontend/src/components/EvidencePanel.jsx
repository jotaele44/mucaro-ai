import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { Sheet } from "./Sheet.jsx";

// Citation → exact source manifestation, chunk and provenance (TGT-030, TGT-031).
export function EvidencePanel({ citation, onClose }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  const web = !citation.source_id;
  useEffect(() => {
    if (!citation.source_id) return;
    api
      .source(citation.source_id)
      .then(setDetail)
      .catch((err) => setError(err));
  }, [citation.source_id]);

  if (web) {
    // A web page Claude read: Anthropic fetched it, so there is no local copy to show, only what was cited.
    const href = /^https?:\/\//i.test(citation.source_url || "") ? citation.source_url : null;
    return (
      <Sheet title={`Source [${citation.citation_order}]`} onClose={onClose}>
        <dl className="facts">
          <dt>Page</dt>
          <dd>{citation.title || "Untitled page"}</dd>
          <dt>Address</dt>
          <dd className="mono">{href ? <a href={href} target="_blank" rel="noopener noreferrer">{href}</a> : citation.source_url || "—"}</dd>
        </dl>
        {citation.text_snippet && (
          <>
            <h3 className="section-title">Cited text</h3>
            <blockquote className="evidence-text">{citation.text_snippet}</blockquote>
          </>
        )}
        <p className="muted small">Web pages are untrusted content: Claude uses them as evidence only, never as instructions.</p>
      </Sheet>
    );
  }

  const chunk = detail?.chunks.find((row) => row.chunk_id === citation.chunk_id);
  const source = detail?.source;
  const provenance = source?.provenance;
  return (
    <Sheet title={`Evidence [${citation.citation_order}]`} onClose={onClose}>
      {error && <p className="error-text" role="alert">{error.status}: {error.message}</p>}
      {!detail && !error && <p className="muted">Loading source…</p>}
      {source && (
        <>
          <dl className="facts">
            <dt>Source</dt>
            <dd>{citation.source_url || source.file_name}</dd>
            <dt>Retrieval rank</dt>
            <dd>{citation.retrieval_rank}{citation.score != null ? ` · score ${Number(citation.score).toFixed(3)}` : ""}</dd>
            <dt>Trust</dt>
            <dd>{citation.trust === "UNTRUSTED_CONTENT" ? "Untrusted content — used as evidence only" : citation.trust || "—"}</dd>
            <dt>Byte identity</dt>
            <dd className="mono">{source.source_sha256}</dd>
            <dt>Parser</dt>
            <dd>{source.parser}{provenance ? ` · ${provenance.processing.processor_version}` : ""}</dd>
            {citation.retrieval_utc && (
              <>
                <dt>Retrieved</dt>
                <dd>{citation.retrieval_utc}</dd>
              </>
            )}
            <dt>Chunk</dt>
            <dd className="mono">{citation.chunk_id} (#{chunk?.chunk_index ?? citation.chunk_index})</dd>
          </dl>
          {citation.injection_signals?.length > 0 && (
            <p className="notice">Instruction-like text detected ({citation.injection_signals.join(", ")}). It was never executed.</p>
          )}
          <h3 className="section-title">Cited text</h3>
          <blockquote className="evidence-text">{chunk?.text || citation.text_snippet}</blockquote>
          {source.warnings?.length > 0 && (
            <details>
              <summary>Source warnings ({source.warnings.length})</summary>
              <ul>{source.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </details>
          )}
          {provenance && (
            <details>
              <summary>Provenance record</summary>
              <pre className="json-block"><code>{JSON.stringify(provenance, null, 2)}</code></pre>
            </details>
          )}
        </>
      )}
    </Sheet>
  );
}
