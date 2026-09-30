import React from "react";

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
          <pre key={index} className="code-block" data-language={block.language}>
            <code>{block.value}</code>
          </pre>
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
