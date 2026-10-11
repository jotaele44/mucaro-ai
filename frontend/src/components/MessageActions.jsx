import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { copyFailureMessage, copyText } from "../clipboard.js";
import { answerMarkdown, answerPlainText, fileBaseName, saveFile, shareFile, shareMessage } from "../files.js";
import { RichText } from "./RichText.jsx";
import { Sheet } from "./Sheet.jsx";

const TEXT_TYPE = "text/plain;charset=utf-8";
const MARKDOWN_TYPE = "text/markdown;charset=utf-8";

// Save formats for one answer. The answer itself (as shown, edits included) is built here; the records are the files
// Mucaro saved with the answer, fetched from the computer that runs it.
function SaveSheet({ text, file, announce, onClose }) {
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const base = fileBaseName(file.title);

  function saveAnswer(markdown) {
    const name = `${base}.${markdown ? "md" : "txt"}`;
    const content = (markdown ? answerMarkdown : answerPlainText)({ title: file.title, text, citations: file.citations });
    saveFile(new Blob([content], { type: markdown ? MARKDOWN_TYPE : TEXT_TYPE }), name);
    announce?.(`Saved ${name}.`);
    onClose();
  }

  async function saveRecord(record) {
    setLoading(record.kind);
    setError("");
    try {
      const name = `${base}-${record.suffix}`;
      saveFile(await api.exportFile(record.runId, record.kind), name);
      announce?.(`Saved ${name}.`);
      onClose();
    } catch (err) {
      setError(`${err?.status || "ERROR"}: ${err?.message || "The file could not be loaded."}`);
      setLoading("");
    }
  }

  return (
    <Sheet title="Save answer" onClose={onClose} side="center">
      <div className="save-options">
        <button type="button" className="secondary-button" onClick={() => saveAnswer(true)}>
          Markdown (.md)
        </button>
        <button type="button" className="secondary-button" onClick={() => saveAnswer(false)}>
          Plain text (.txt)
        </button>
      </div>
      {file.records.length > 0 && (
        <>
          <h3 className="save-heading">Saved with this answer</h3>
          <div className="save-options">
            {file.records.map((record) => (
              <button key={record.kind} type="button" className="secondary-button" disabled={Boolean(loading)} onClick={() => saveRecord(record)}>
                {loading === record.kind ? "Loading…" : record.label}
              </button>
            ))}
          </div>
        </>
      )}
      {error && <p className="error-text" role="alert">{error}</p>}
      <p className="muted small">A browser keeps the file with its downloads; the desktop app asks where to save it.</p>
    </Sheet>
  );
}

// One canonical per-message action surface. Copy/edit never mutate the canonical transcript.
// `file` ({ title, citations, records }) adds Save and Share for an answer.
export function MessageActions({ text, announce, onEdit, allowReadAloud = false, file = null }) {
  const [speaking, setSpeaking] = useState(false);
  const [saving, setSaving] = useState(false);
  const canSpeak = allowReadAloud && typeof window !== "undefined" && "speechSynthesis" in window;

  useEffect(() => () => canSpeak && window.speechSynthesis.cancel(), [canSpeak]);

  async function copy() {
    const result = await copyText(text);
    announce?.(result.ok ? "Copied to clipboard." : copyFailureMessage(result));
  }

  function toggleSpeech() {
    if (!canSpeak) return;
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
    setSpeaking(true);
  }

  async function share() {
    // Plain text: share sheets accept .txt everywhere, while some refuse .md (Chrome's list of shareable types).
    const name = `${fileBaseName(file.title)}.txt`;
    const content = answerPlainText({ title: file.title, text, citations: file.citations });
    const result = await shareFile(new Blob([content], { type: TEXT_TYPE }), name, { title: file.title });
    announce?.(shareMessage(result, name));
  }

  return (
    <>
      <div className="message-actions" aria-label="Message actions">
        <button type="button" className="ghost-button" onClick={copy} aria-label="Copy message text">
          Copy
        </button>
        <button type="button" className="ghost-button" onClick={onEdit} aria-label="Edit message">
          Edit
        </button>
        {canSpeak && (
          <button type="button" className="ghost-button" onClick={toggleSpeech} aria-pressed={speaking} aria-label={speaking ? "Stop reading aloud" : "Read message aloud"}>
            {speaking ? "Stop reading" : "Read aloud"}
          </button>
        )}
        {file && (
          <>
            <button type="button" className="ghost-button" onClick={() => setSaving(true)} aria-haspopup="dialog" aria-label="Save answer as a file">
              Save
            </button>
            <button type="button" className="ghost-button" onClick={share} aria-label="Share answer">
              Share
            </button>
          </>
        )}
      </div>
      {saving && <SaveSheet text={text} file={file} announce={announce} onClose={() => setSaving(false)} />}
    </>
  );
}

export function EditableMessage({
  text,
  revision = 0,
  edited = false,
  onSave,
  announce,
  allowReadAloud = false,
  file = null,
  children,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const editorRef = useRef(null);

  useEffect(() => {
    if (!editing) setDraft(text);
  }, [text, editing]);

  useEffect(() => {
    if (editing) {
      editorRef.current?.focus();
      editorRef.current?.setSelectionRange(editorRef.current.value.length, editorRef.current.value.length);
    }
  }, [editing]);

  function cancel() {
    if (busy) return;
    setDraft(text);
    setError("");
    setEditing(false);
  }

  async function save(event) {
    event.preventDefault();
    if (busy || !draft.trim() || draft === text) return;
    setBusy(true);
    setError("");
    try {
      await onSave(draft, revision);
      setEditing(false);
    } catch (err) {
      setError(err?.message || "Message could not be updated.");
      announce?.("Message edit failed.");
    } finally {
      setBusy(false);
    }
  }

  function onEditorKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="editable-message">
      {editing ? (
        <form className="message-editor" onSubmit={save}>
          <label>
            <span className="sr-only">Edit message text</span>
            <textarea
              ref={editorRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onEditorKeyDown}
              rows={Math.min(12, Math.max(3, draft.split("\n").length + 1))}
              disabled={busy}
            />
          </label>
          {error && <p className="error-text" role="alert">{error}</p>}
          <div className="message-editor-actions">
            <button type="button" className="ghost-button" onClick={cancel} disabled={busy}>Cancel</button>
            <button type="submit" className="primary-button" disabled={busy || !draft.trim() || draft === text}>
              {busy ? "Saving…" : "Save edit"}
            </button>
          </div>
          <p className="muted small message-edit-disclosure">Display-only revision. The original message remains preserved in provenance.</p>
        </form>
      ) : (
        <>
          <RichText text={text} />
          {children}
          <div className="message-footer">
            {edited && (
              <span
                className="message-edited-label"
                title="This is a display-only revision. The original canonical message is preserved for provenance."
              >
                Edited · original preserved
              </span>
            )}
            <MessageActions
              text={text}
              announce={announce}
              onEdit={() => {
                setDraft(text);
                setError("");
                setEditing(true);
              }}
              allowReadAloud={allowReadAloud}
              file={file}
            />
          </div>
        </>
      )}
    </div>
  );
}
