import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { CloseIcon, FileIcon } from "./Icons.jsx";
import { Sheet } from "./Sheet.jsx";

// Files Claude can read: images, PDFs and text documents (the server checks the content, not just the name).
export const ATTACH_ACCEPT =
  "image/png,image/jpeg,image/gif,image/webp,application/pdf,.txt,.md,.csv,.json,.html,.htm,.docx,.eml,.msg,.zip," +
  ".py,.js,.ts,.jsx,.tsx,.css,.sql,.yaml,.yml,.toml,.xml,.sh,.log,.ini,.cfg,.rst,.tex,.java,.c,.h,.cpp,.go,.rs,.rb,.php,.swift,.kt";

export function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// The image's bytes come through the authenticated API, so a plain <img src> cannot load them.
function useAttachmentUrl(conversationId, attachmentId, enabled) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!enabled || !conversationId || !attachmentId) return undefined;
    let objectUrl = null;
    let alive = true;
    api
      .attachmentFile(conversationId, attachmentId)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (alive) setUrl(objectUrl);
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => alive && setUrl(null));
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [conversationId, attachmentId, enabled]);
  return url;
}

async function download(conversationId, attachment) {
  const blob = await api.attachmentFile(conversationId, attachment.attachment_id);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = attachment.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// A file on a sent message: images show a thumbnail that opens full size; other files download.
function SentAttachment({ conversationId, attachment }) {
  const image = attachment.kind === "image";
  const url = useAttachmentUrl(conversationId, attachment.attachment_id, image);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className={`attachment-chip sent ${image ? "image" : ""}`}
        onClick={() => (image ? setOpen(true) : download(conversationId, attachment).catch(() => {}))}
        aria-label={image ? `View ${attachment.name}` : `Download ${attachment.name}`}
        title={attachment.name}
      >
        {image && url ? <img src={url} alt="" className="attachment-thumb" /> : <FileIcon />}
        {!image && <span className="attachment-name">{attachment.name}</span>}
      </button>
      {open && (
        <Sheet title={attachment.name} onClose={() => setOpen(false)}>
          {url ? <img src={url} alt={attachment.name} className="attachment-full" /> : <p className="muted">Loading…</p>}
        </Sheet>
      )}
    </>
  );
}

export function SentAttachments({ conversationId, attachments }) {
  if (!attachments?.length) return null;
  return (
    <div className="attachment-row" aria-label="Attached files">
      {attachments.map((attachment) => (
        <SentAttachment key={attachment.attachment_id} conversationId={conversationId} attachment={attachment} />
      ))}
    </div>
  );
}

// Files waiting in the composer: uploading, ready, or refused with the reason.
export function PendingAttachments({ files, onRemove }) {
  if (!files.length) return null;
  return (
    <ul className="attachment-row pending" aria-label="Files to send">
      {files.map((file) => (
        <li key={file.localId} className={`attachment-chip pending status-${file.status}`}>
          <FileIcon />
          <span className="attachment-name" title={file.name}>{file.name}</span>
          <span className="muted small">
            {file.status === "uploading" ? "Uploading…" : file.status === "error" ? file.error : formatBytes(file.bytes)}
          </span>
          <button type="button" className="attachment-remove" onClick={() => onRemove(file.localId)} aria-label={`Remove ${file.name}`}>
            <CloseIcon />
          </button>
        </li>
      ))}
    </ul>
  );
}
