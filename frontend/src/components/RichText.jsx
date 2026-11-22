import React, { useEffect, useRef, useState } from "react";
import { copyFailureMessage, copyText } from "../clipboard.js";
import { CheckIcon, CopyIcon } from "./Icons.jsx";

// A fenced block with its own copy button, so one block (a command, a next vector) can be taken without the
// rest of the message. Copying reads the block's text; nothing in it is run.
function CodeBlock({ language, value }) {
  const [copied, setCopied] = useState(false);
  const [failure, setFailure] = useState("");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    const result = await copyText(value.replace(/\n$/, ""));
    setFailure(result.ok ? "" : copyFailureMessage(result));
    setCopied(result.ok);
    clearTimeout(timer.current);
    if (result.ok) timer.current = setTimeout(() => setCopied(false), 1500);
  }

  const label = language === "text" ? "Plain text" : language;
  return (
    <div className="code-figure">
      <div className="code-bar">
        <span className="code-language">{label}</span>
        <button type="button" className="ghost-button icon-action" onClick={copy} aria-label="Copy this block" title={copied ? "Copied" : "Copy block"}>
          {copied ? <CheckIcon /> : <CopyIcon />}
        </button>
      </div>
      {/* A long block scrolls, so it takes keyboard focus like the diff view does. */}
      <pre className="code-block" data-language={language} tabIndex={0} aria-label={`${label} block`}>
        <code>{value}</code>
      </pre>
      <span className="sr-only" role="status">{copied ? "Block copied to clipboard." : failure}</span>
      {failure && <p className="error-text small" aria-hidden="true">{failure}</p>}
    </div>
  );
}

// Renders model/tool text inertly: React escapes everything, fenced code is shown as
// text in <pre>, and nothing is executed or turned into links automatically.
export function RichText({ text }) {
  const blocks = [];
  const pattern = /```([\w+-]*)\n?([\s\S]*?)```/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) blocks.push({ kind: "text", value: text.slice(last, match.index) });
    blocks.push({ kind: "code", language: match[1] || "text", value: match[2] });
    last = pattern.lastIndex;
  }
  if (last < text.length) blocks.push({ kind: "text", value: text.slice(last) });
  return (
    <div className="rich-text">
      {blocks.map((block, index) =>
        block.kind === "code" ? (
          <CodeBlock key={index} language={block.language} value={block.value} />
        ) : (
          block.value
            .split(/\n{2,}/)
            .filter((para) => para.trim())
            .map((para, pIndex) => (
              <p key={`${index}-${pIndex}`}>
                {para.split("\n").map((line, lIndex, lines) => (
                  <React.Fragment key={lIndex}>
                    {line}
                    {lIndex < lines.length - 1 && <br />}
                  </React.Fragment>
                ))}
              </p>
            ))
        ),
      )}
    </div>
  );
}
