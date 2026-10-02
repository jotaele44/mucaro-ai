import React, { useEffect, useRef, useState } from "react";
import { copyFailureMessage, copyText } from "../clipboard.js";
import { RichText } from "./RichText.jsx";

// One canonical per-message action surface. Copy/edit never mutate the canonical transcript.
export function MessageActions({ text, announce, onEdit, allowReadAloud = false }) {
  const [speaking, setSpeaking] = useState(false);
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

  return (
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
    </div>
  );
}

export function EditableMessage({
  text,
  revision = 0,
  edited = false,
  onSave,
  announce,
  allowReadAloud = false,
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
            />
          </div>
        </>
      )}
    </div>
  );
}
