import React, { useEffect, useRef, useState } from "react";

const MODE_LABEL = { plan: "Plan", ask: "Ask first", auto: "Auto (local read-only)" };

// Message composer (F05/F06): empty send disabled, Enter sends (Shift+Enter for a new line),
// IME composition never submits, and the draft survives sheets and reloads per conversation.
export function Composer({ conversationId, modes, mode, onModeChange, onSend, onStop, activeRun, queued, onCancelQueued, disabled }) {
  const storageKey = `mucaro.draft.${conversationId || "new"}`;
  const [text, setText] = useState(() => {
    try {
      return window.localStorage.getItem(storageKey) || "";
    } catch {
      return "";
    }
  });
  const [menuOpen, setMenuOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const area = useRef(null);

  useEffect(() => {
    try {
      setText(window.localStorage.getItem(storageKey) || "");
    } catch {
      setText("");
    }
  }, [storageKey]);

  useEffect(() => {
    try {
      if (text) window.localStorage.setItem(storageKey, text);
      else window.localStorage.removeItem(storageKey);
    } catch {
      /* ignore storage failures */
    }
  }, [storageKey, text]);

  useEffect(() => {
    const node = area.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 240)}px`;
  }, [text]);

  async function send() {
    const prompt = text.trim();
    if (!prompt || sending || disabled) return;
    setSending(true);
    try {
      await onSend(prompt);
      setText("");
    } finally {
      setSending(false);
      area.current?.focus();
    }
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      send();
    }
  }

  const empty = !text.trim();
  return (
    <div className="composer">
      {queued.length > 0 && (
        <ul className="queue" aria-label="Queued messages">
          {queued.map((run) => (
            <li key={run.run_id}>
              <span className="muted small">Queued:</span> {run.prompt_preview}
              <button type="button" className="ghost-button" onClick={() => onCancelQueued(run.run_id)} aria-label={`Cancel queued message ${run.prompt_preview}`}>
                Cancel
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="composer-box">
        <label htmlFor="composer-input" className="sr-only">
          Message Mucaro
        </label>
        <textarea
          id="composer-input"
          ref={area}
          rows={1}
          value={text}
          placeholder={activeRun ? "Message will be queued until the current turn finishes" : "Ask a question, or type /status, /fetch <url>…"}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
        />
        <div className="composer-controls">
          <div className="mode-picker">
            <button type="button" className="mode-chip" aria-haspopup="listbox" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
              Mode: {MODE_LABEL[mode] || mode}
            </button>
            {menuOpen && (
              <ul className="mode-menu" role="listbox" aria-label="Execution mode">
                {(modes?.modes || []).map((row) => (
                  <li key={row.mode}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={row.mode === mode}
                      onClick={() => {
                        onModeChange(row.mode);
                        setMenuOpen(false);
                        area.current?.focus();
                      }}
                    >
                      <strong>{MODE_LABEL[row.mode] || row.mode}</strong>
                      <span className="muted small block">{row.description}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {activeRun && (
            <button type="button" className="secondary-button" onClick={onStop} aria-label="Stop the current turn">
              Stop
            </button>
          )}
          <button type="button" className="primary-button" onClick={send} disabled={empty || sending || disabled} aria-label={activeRun ? "Queue message" : "Send message"}>
            {activeRun ? "Queue" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
