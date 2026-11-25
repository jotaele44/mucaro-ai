// View models for governed tool results (TGT-002, TGT-003). Pure functions so they can be tested without a browser;
// the components only lay out what these return. Every view is derived from the tool call record, never from prose.

export const TOOL_FAMILIES = {
  gis: ["gis.inspect"],
  ocr: ["ocr.analyze_image"],
  code: ["coding.repo_status", "coding.read_file", "coding.list_files", "coding.search", "coding.diff", "coding.exec"],
  pr: ["github.prs_list", "github.pr_view"],
};

export function familyOf(toolName) {
  return Object.keys(TOOL_FAMILIES).find((family) => TOOL_FAMILIES[family].includes(toolName)) || null;
}

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** GIS inspection report → CRS line, geometry counts and a features table. */
export function gisView(result) {
  if (!isObject(result) || !Array.isArray(result.features)) return null;
  const source = isObject(result.source) ? result.source : {};
  const counts = Object.entries(isObject(result.geometry_counts) ? result.geometry_counts : {}).sort((a, b) => b[1] - a[1]);
  const features = result.features.map((feature, index) => ({
    index: num(feature?.feature_index) ?? index,
    id: feature?.feature_id ?? null,
    geometry: feature?.geometry_type ?? "unknown",
    status: feature?.geometry_status ?? "unknown",
    bbox: Array.isArray(feature?.bbox) && feature.bbox.length === 4 && feature.bbox.every((v) => num(v) !== null) ? feature.bbox : null,
    warnings: Array.isArray(feature?.warnings) ? feature.warnings : [],
  }));
  return {
    status: result.status ?? null,
    crs: source.crs ?? null,
    crsStatus: source.crs_status ?? null,
    featureCount: num(source.feature_count) ?? features.length,
    sha256: source.sha256 ?? null,
    counts,
    features,
    warnings: Array.isArray(result.warnings) ? result.warnings : [],
  };
}

/**
 * Bounding boxes projected into a width×height box (y flipped so north is up). This is a preview of where features
 * are relative to each other, not a map: there is no basemap, projection or scale.
 */
export function bboxPreview(features, width = 320, height = 200, pad = 8) {
  const boxes = (features || []).map((feature) => feature.bbox).filter(Boolean);
  if (!boxes.length) return null;
  const minX = Math.min(...boxes.map((b) => b[0]));
  const minY = Math.min(...boxes.map((b) => b[1]));
  const maxX = Math.max(...boxes.map((b) => b[2]));
  const maxY = Math.max(...boxes.map((b) => b[3]));
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const shapes = boxes.map(([x0, y0, x1, y1]) => {
    const x = pad + (x0 - minX) * scale;
    const y = height - pad - (y1 - minY) * scale;
    const w = (x1 - x0) * scale;
    const h = (y1 - y0) * scale;
    return w < 2 && h < 2 ? { kind: "point", cx: x + w / 2, cy: y + h / 2 } : { kind: "box", x, y, w: Math.max(w, 1), h: Math.max(h, 1) };
  });
  return { width, height, shapes, bounds: [minX, minY, maxX, maxY] };
}

/** OCR report → canonical text and a spans table. */
export function ocrView(result) {
  if (!isObject(result)) return null;
  const text = isObject(result.text) ? result.text.canonical ?? result.text.normalized ?? result.text.raw ?? "" : String(result.text ?? "");
  const spans = (Array.isArray(result.spans) ? result.spans : []).map((span) => ({
    text: span?.canonical_text ?? span?.text ?? "",
    confidence: num(span?.confidence),
    state: span?.confidence_state ?? null,
    line: num(span?.line_num),
  }));
  const engine = isObject(result.engine) ? result.engine : {};
  const source = isObject(result.source) ? result.source : {};
  return {
    status: result.status ?? null,
    text,
    spans,
    lowConfidence: spans.filter((span) => span.state === "LOW").length,
    engine: engine.name ? `${engine.name}${engine.version ? ` ${engine.version}` : ""}` : null,
    size: num(source.width) && num(source.height) ? `${source.width}×${source.height}` : null,
  };
}

/** Repository status → the facts a reviewer checks first. */
export function repoStatusView(result) {
  if (!isObject(result) || !("head_sha" in result || "branch" in result)) return null;
  const list = (key) => (Array.isArray(result[key]) ? result[key] : []);
  return {
    branch: result.branch ?? null,
    head: typeof result.head_sha === "string" ? result.head_sha.slice(0, 12) : null,
    dirty: result.dirty_status ?? null,
    staged: list("staged_files"),
    unstaged: list("unstaged_files"),
    untracked: list("untracked_files"),
  };
}

/** Container execution receipt → exit code and the two output streams. */
export function execView(result) {
  if (!isObject(result) || !("exit_code" in result)) return null;
  const stream = (value) => (isObject(value) ? value.text ?? value.content ?? value.preview ?? "" : typeof value === "string" ? value : "");
  const truncated = (value) => (isObject(value) ? Boolean(value.truncated) : false);
  return {
    exitCode: num(result.exit_code),
    passed: result.exit_code === 0,
    durationMs: num(result.duration_ms),
    command: Array.isArray(result.command_argv) ? result.command_argv.join(" ") : null,
    stdout: stream(result.stdout),
    stderr: stream(result.stderr),
    truncated: truncated(result.stdout) || truncated(result.stderr),
  };
}

/** Pull request list or single pull request → rows for a list. */
export function prRows(result) {
  if (!isObject(result)) return [];
  const items = Array.isArray(result.pull_requests) ? result.pull_requests : isObject(result.pull_request) ? [result.pull_request] : [];
  return items.map((pr) => ({
    number: num(pr?.number),
    title: pr?.title ?? "",
    state: pr?.draft ? "draft" : String(pr?.state ?? "unknown").toLowerCase(),
    head: typeof pr?.head_sha === "string" ? pr.head_sha.slice(0, 7) : null,
    headRef: pr?.head_ref ?? null,
    baseRef: pr?.base_ref ?? null,
    url: typeof pr?.url === "string" && /^https:\/\//.test(pr.url) ? pr.url : null,
    author: pr?.author ?? null,
  }));
}
