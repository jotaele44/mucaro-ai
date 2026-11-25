import React from "react";
import { bboxPreview, execView, familyOf, gisView, ocrView, prRows, repoStatusView } from "../../resultViews.js";

// Purpose-built views of governed tool results (TGT-002, TGT-003). Each one is derived from the tool call record;
// the raw result always stays one click away, rendered inertly.

export function Json({ value }) {
  return (
    <pre className="json-block">
      <code>{JSON.stringify(value ?? {}, null, 2)}</code>
    </pre>
  );
}

/** A unified diff with added and removed lines marked, so a proposed change can be read before it is allowed. */
export function DiffView({ patch, label = "Proposed change" }) {
  const lines = String(patch).replace(/\n$/, "").split("\n");
  const kind = (line) =>
    line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")
      ? "meta"
      : line.startsWith("@@")
        ? "hunk"
        : line.startsWith("+")
          ? "add"
          : line.startsWith("-")
            ? "del"
            : "ctx";
  const added = lines.filter((line) => kind(line) === "add").length;
  const removed = lines.filter((line) => kind(line) === "del").length;
  return (
    <figure className="diff-view">
      <figcaption className="muted small">{label}: {added} line{added === 1 ? "" : "s"} added, {removed} removed</figcaption>
      <pre className="diff-block" tabIndex={0} aria-label={`${label} as a diff`}>
        {lines.map((line, index) => (
          <span key={index} className={`diff-line diff-${kind(line)}`}>
            {line || " "}
            {"\n"}
          </span>
        ))}
      </pre>
    </figure>
  );
}
function Table({ caption, columns, rows, testId }) {
  return (
    <div className="result-table-wrap" tabIndex={0}>
      <table className="result-table" data-testid={testId}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>{columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const MAX_ROWS = 50;

function GisResult({ view }) {
  const preview = bboxPreview(view.features);
  return (
    <div className="result-view" data-testid="gis-result">
      <dl className="facts">
        <dt>Coordinate system</dt><dd>{view.crs || "unknown"}{view.crsStatus ? ` (${view.crsStatus.toLowerCase().replaceAll("_", " ")})` : ""}</dd>
        <dt>Features</dt><dd>{view.featureCount}</dd>
        <dt>Geometry</dt><dd>{view.counts.map(([type, count]) => `${count} ${type}`).join(", ") || "none"}</dd>
      </dl>
      {preview && (
        <figure className="bbox-preview">
          <svg viewBox={`0 0 ${preview.width} ${preview.height}`} role="img" aria-label={`Bounding-box preview of ${view.features.length} features`} data-testid="gis-bbox-preview">
            <rect x="0" y="0" width={preview.width} height={preview.height} className="bbox-frame" />
            {preview.shapes.map((shape, index) =>
              shape.kind === "point" ? (
                <circle key={index} cx={shape.cx} cy={shape.cy} r="2.5" className="bbox-point" />
              ) : (
                <rect key={index} x={shape.x} y={shape.y} width={shape.w} height={shape.h} className="bbox-box" />
              ),
            )}
          </svg>
          <figcaption className="muted small">Where features sit relative to each other (bounding boxes, north up). Not a map: no basemap or scale.</figcaption>
        </figure>
      )}
      <Table
        caption="Features"
        testId="gis-features"
        columns={["#", "ID", "Geometry", "Status"]}
        rows={view.features.slice(0, MAX_ROWS).map((feature) => [feature.index, feature.id ?? "—", feature.geometry, feature.status])}
      />
      {view.features.length > MAX_ROWS && <p className="muted small">Showing {MAX_ROWS} of {view.features.length} features.</p>}
    </div>
  );
}

function OcrResult({ view }) {
  return (
    <div className="result-view" data-testid="ocr-result">
      <dl className="facts">
        <dt>Engine</dt><dd>{view.engine || "unknown"}</dd>
        {view.size && (<><dt>Image</dt><dd>{view.size}</dd></>)}
        <dt>Words</dt><dd>{view.spans.length}{view.lowConfidence ? ` (${view.lowConfidence} low confidence)` : ""}</dd>
      </dl>
      <pre className="ocr-text" tabIndex={0} aria-label="Recognized text">{view.text || "No text recognized."}</pre>
      {view.spans.length > 0 && (
        <Table
          caption="Recognized words"
          testId="ocr-spans"
          columns={["Line", "Text", "Confidence"]}
          rows={view.spans.slice(0, MAX_ROWS).map((span) => [span.line ?? "—", span.text, span.confidence === null ? "—" : `${Math.round(span.confidence)}${span.state === "LOW" ? " (low)" : ""}`])}
        />
      )}
    </div>
  );
}

function CodeResult({ toolName, result }) {
  const run = execView(result);
  if (run) {
    return (
      <div className="result-view" data-testid="code-exec-result">
        <p>
          <span className={`status-chip status-${run.passed ? "completed" : "failed"}`}>exit {run.exitCode ?? "?"}</span>{" "}
          {run.command && <code>{run.command}</code>}
          {run.durationMs !== null && <span className="muted small"> · {run.durationMs} ms</span>}
        </p>
        <pre className="terminal-block" tabIndex={0} aria-label="Standard output">{run.stdout || "(no output)"}</pre>
        {run.stderr && <pre className="terminal-block terminal-err" tabIndex={0} aria-label="Standard error">{run.stderr}</pre>}
        {run.truncated && <p className="muted small">Output was cut at the size limit.</p>}
      </div>
    );
  }
  if (toolName === "coding.diff" && typeof result?.diff === "string") return <DiffView patch={result.diff || "(no changes)"} label="Uncommitted changes" />;
  const repo = repoStatusView(result);
  if (repo) {
    const files = (label, list) => (<><dt>{label}</dt><dd>{list.length ? list.slice(0, 20).join(", ") : "none"}</dd></>);
    return (
      <dl className="facts result-view" data-testid="code-repo-result">
        <dt>Branch</dt><dd>{repo.branch || "detached"}</dd>
        <dt>HEAD</dt><dd className="mono">{repo.head || "—"}</dd>
        <dt>Working tree</dt><dd>{repo.dirty ? repo.dirty.toLowerCase() : "—"}</dd>
        {files("Staged", repo.staged)}
        {files("Changed", repo.unstaged)}
        {files("Untracked", repo.untracked)}
      </dl>
    );
  }
  return null;
}

function PullRequestResult({ result }) {
  const rows = prRows(result);
  if (!rows.length) return <p className="muted small">No pull requests.</p>;
  return (
    <ul className="pr-list result-view" data-testid="pr-result">
      {rows.map((pr) => (
        <li key={pr.number ?? pr.title} className="pr-row">
          <span className={`status-chip status-${pr.state === "open" ? "running" : pr.state === "merged" ? "completed" : "queued"}`}>{pr.state}</span>{" "}
          {pr.url ? <a href={pr.url} target="_blank" rel="noreferrer noopener">#{pr.number} {pr.title}</a> : <span>#{pr.number} {pr.title}</span>}
          <span className="muted small"> {pr.headRef ? `${pr.headRef} → ${pr.baseRef || "?"}` : ""}{pr.head ? ` · ${pr.head}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

/** The best view of a tool result, with the raw record behind a disclosure. */
export function ToolResult({ toolName, result, status }) {
  const family = familyOf(toolName);
  let view = null;
  if (family === "gis") {
    const model = gisView(result);
    if (model) view = <GisResult view={model} />;
  } else if (family === "ocr") {
    const model = ocrView(result);
    if (model && (model.text || model.spans.length)) view = <OcrResult view={model} />;
  } else if (family === "code") {
    view = <CodeResult toolName={toolName} result={result} />;
  } else if (family === "pr") {
    view = <PullRequestResult result={result} />;
  }
  return (
    <>
      {view && <div className="tool-result-view">{view}</div>}
      <details>
        <summary>{view ? "Raw result" : "Output"}{status ? ` (${status})` : ""}</summary>
        <Json value={result} />
      </details>
    </>
  );
}
