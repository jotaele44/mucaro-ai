import React, { useEffect, useState } from "react";

// Copy exact text and read aloud (F20). Neither action changes the transcript.
export function MessageActions({ text, announce }) {
  const [speaking, setSpeaking] = useState(false);
  const canSpeak = typeof window !== "undefined" && "speechSynthesis" in window;

  useEffect(() => () => canSpeak && window.speechSynthesis.cancel(), [canSpeak]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      announce?.("Copied to clipboard.");
    } catch {
      announce?.("Copy failed: clipboard access was not allowed.");
    }
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
    <div className="message-actions">
      <button type="button" className="ghost-button" onClick={copy} aria-label="Copy message text">
        Copy
      </button>
      {canSpeak && (
        <button type="button" className="ghost-button" onClick={toggleSpeech} aria-pressed={speaking} aria-label={speaking ? "Stop reading aloud" : "Read message aloud"}>
          {speaking ? "Stop reading" : "Read aloud"}
        </button>
      )}
    </div>
  );
}
