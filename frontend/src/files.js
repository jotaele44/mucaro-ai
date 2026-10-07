// Saving and sharing an answer as a file. Saving downloads it: a browser keeps it with its downloads (a phone puts it
// in Files) and the desktop window asks where to save it. Sharing opens the system share sheet, which browsers offer
// only on a secure page (https, or the computer itself); a phone that opens Mucaro over http saves the file instead.

export const SAVED = "saved";
export const SHARED = "shared";
export const CANCELLED = "cancelled";

// The files Mucaro writes for every answer, served by kind (never by path) from /exports/<run_id>/files/<kind>.
export const ANSWER_RECORDS = [
  { kind: "markdown", label: "Report with sources (.md)", suffix: "report.md" },
  { kind: "json", label: "Full answer record (.json)", suffix: "record.json" },
  { kind: "csv", label: "Citations table (.csv)", suffix: "citations.csv" },
];

const oneLine = (value) => String(value || "").replace(/\s+/g, " ").trim();

/** A file name stem from the question: "mucaro-how-does-source-lineage-work", or "mucaro-answer". */
export function fileBaseName(title) {
  const slug = oneLine(title)
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, 48)
    .replace(/-+$/, "");
  return slug ? `mucaro-${slug}` : "mucaro-answer";
}

function sourceLabel(citation) {
  return citation.source_url || citation.source_id || citation.chunk_id || "source";
}

export function answerMarkdown({ title, text, citations = [] }) {
  const lines = [];
  if (oneLine(title)) lines.push(`# ${oneLine(title)}`, "");
  lines.push(String(text || "").trim());
  if (citations.length) {
    lines.push("", "## Sources", "");
    citations.forEach((citation, index) => {
      const order = citation.citation_order ?? index + 1;
      lines.push(`${order}. ${sourceLabel(citation)}${citation.chunk_id ? ` (chunk ${citation.chunk_id})` : ""}`);
      if (citation.text_snippet) lines.push(`   > ${oneLine(citation.text_snippet)}`);
    });
  }
  return `${lines.join("\n")}\n`;
}

export function answerPlainText({ title, text, citations = [] }) {
  const lines = [];
  if (oneLine(title)) lines.push(`Question: ${oneLine(title)}`, "");
  lines.push(String(text || "").trim());
  if (citations.length) {
    lines.push("", "Sources:");
    citations.forEach((citation, index) => lines.push(`[${citation.citation_order ?? index + 1}] ${sourceLabel(citation)}`));
  }
  return `${lines.join("\n")}\n`;
}

/** The answer records this run exported, from its artifact events ("answer_trace:<run_id>:<kind>"). */
export function answerRecords(artifacts = []) {
  const runs = new Map();
  for (const artifact of artifacts) {
    const [scope, runId, kind] = String(artifact.logical_identity || "").split(":");
    if (scope === "answer_trace" && runId && kind) runs.set(kind, runId);
  }
  return ANSWER_RECORDS.filter((record) => runs.has(record.kind)).map((record) => ({ ...record, runId: runs.get(record.kind) }));
}

/** Download a blob under `name`. */
export function saveFile(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Safari starts the download after this returns and fails if the address is already revoked.
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

/**
 * Open the share sheet for one file. Resolves `{ outcome: SHARED }`, `{ outcome: CANCELLED }` when the person closed
 * the sheet, or `{ outcome: SAVED, reason }` when sharing is not available here and the file was saved instead.
 * Never rejects.
 */
export async function shareFile(blob, name, { title } = {}) {
  const file = new File([blob], name, { type: blob.type || "application/octet-stream" });
  if (typeof navigator.share === "function" && navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: title || name });
      return { outcome: SHARED };
    } catch (err) {
      if (err?.name === "AbortError") return { outcome: CANCELLED };
      // NotAllowedError (the tap was too long ago) or a refusal by the system: save the file instead.
    }
  }
  saveFile(blob, name);
  return { outcome: SAVED, reason: window.isSecureContext === false ? "insecure" : "unsupported" };
}

export function shareMessage(result, name) {
  if (result.outcome === SHARED) return "Shared.";
  if (result.outcome === CANCELLED) return "Sharing cancelled.";
  return result.reason === "insecure"
    ? `Sharing needs a secure (https) connection, so ${name} was saved instead.`
    : `This browser cannot share files, so ${name} was saved instead.`;
}
