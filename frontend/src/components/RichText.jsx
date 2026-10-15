import React, { useState } from "react";
import { copyFailureMessage, copyText } from "../clipboard.js";

// Renders model and tool text as Markdown, inertly: everything becomes React elements (React escapes the text),
// raw HTML is shown as text, and only http(s) and mailto links are made, opening outside the app.
// ⟦n⟧ marks a citation; it renders as a small [n] that opens the evidence when a handler is given.

const CITE = /⟦(\d+)⟧/;
const SAFE_URL = /^(https?:\/\/|mailto:)/i;

function safeHref(url) {
  const value = String(url || "").trim();
  return SAFE_URL.test(value) ? value : null;
}

// --- inline ---------------------------------------------------------------------------------

const INLINE = [
  { kind: "code", re: /`([^`\n]+)`/ },
  { kind: "cite", re: CITE },
  { kind: "link", re: /\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/ },
  { kind: "url", re: /\bhttps?:\/\/[^\s<>()\]]+[^\s<>()\].,;:!?'"]/ },
  { kind: "bold", re: /\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/ },
  { kind: "strike", re: /~~([^~\n]+?)~~/ },
  { kind: "em", re: /(?<![\w*])\*([^*\n]+?)\*(?!\*)|(?<![\w_])_([^_\n]+?)_(?![\w_])/ },
];

function renderInline(text, ctx, keyBase = "i") {
  const out = [];
  let rest = String(text ?? "");
  let n = 0;
  while (rest) {
    let best = null;
    for (const rule of INLINE) {
      const match = rule.re.exec(rest);
      if (match && (best === null || match.index < best.match.index)) best = { rule, match };
    }
    if (!best) {
      out.push(rest);
      break;
    }
    const { rule, match } = best;
    if (match.index > 0) out.push(rest.slice(0, match.index));
    const key = `${keyBase}-${n++}`;
    if (rule.kind === "code") out.push(<code key={key} className="inline-code">{match[1]}</code>);
    else if (rule.kind === "cite") {
      const number = Number(match[1]);
      out.push(
        ctx.onCite ? (
          <button key={key} type="button" className="cite-mark" onClick={() => ctx.onCite(number)} aria-label={`Open source ${number}`}>
            [{number}]
          </button>
        ) : (
          <sup key={key} className="cite-mark">[{number}]</sup>
        ),
      );
    } else if (rule.kind === "link" || rule.kind === "url") {
      const label = rule.kind === "link" ? match[1] : match[0];
      const href = safeHref(rule.kind === "link" ? match[2] : match[0]);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {rule.kind === "link" ? renderInline(label, ctx, key) : label}
          </a>
        ) : (
          label
        ),
      );
    } else if (rule.kind === "bold") out.push(<strong key={key}>{renderInline(match[1] ?? match[2], ctx, key)}</strong>);
    else if (rule.kind === "strike") out.push(<del key={key}>{renderInline(match[1], ctx, key)}</del>);
    else out.push(<em key={key}>{renderInline(match[1] ?? match[2], ctx, key)}</em>);
    rest = rest.slice(match.index + match[0].length);
  }
  return out;
}

function withBreaks(text, ctx, keyBase) {
  const lines = String(text).split("\n");
  return lines.map((line, index) => (
    <React.Fragment key={`${keyBase}-l${index}`}>
      {renderInline(line, ctx, `${keyBase}-l${index}`)}
      {index < lines.length - 1 && <br />}
    </React.Fragment>
  ));
}

// --- blocks ----------------------------------------------------------------------------------

const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s*>\s?/;
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function splitRow(line) {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|") && !value.endsWith("\\|")) value = value.slice(0, -1);
  return value.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function parseList(lines, start) {
  const first = LIST_ITEM.exec(lines[start]);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let index = start;
  while (index < lines.length) {
    const match = LIST_ITEM.exec(lines[index]);
    if (match && match[1].length === indent && /\d/.test(match[2]) === ordered) {
      items.push({ text: [match[3]], children: [] });
      index += 1;
      continue;
    }
    if (match && match[1].length > indent && items.length) {
      const nested = parseList(lines, index);
      items[items.length - 1].children.push(nested.block);
      index = nested.next;
      continue;
    }
    const line = lines[index];
    if (line.trim() && items.length && /^\s+\S/.test(line) && !match) {
      items[items.length - 1].text.push(line.trim());
      index += 1;
      continue;
    }
    break;
  }
  const startNumber = ordered ? parseInt(first[2], 10) : undefined;
  return { block: { type: "list", ordered, start: startNumber, items }, next: index };
}

export function parseMarkdown(source) {
  const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const body = [];
      index += 1;
      while (index < lines.length && !(lines[index].trim().startsWith(fence[1][0].repeat(fence[1].length)) && !lines[index].trim().slice(fence[1].length).trim())) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // closing fence (or end of text while streaming)
      blocks.push({ type: "code", language: fence[2] || "", text: body.join("\n") });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }
    if (RULE.test(line)) {
      blocks.push({ type: "rule" });
      index += 1;
      continue;
    }
    if (QUOTE.test(line)) {
      const body = [];
      while (index < lines.length && lines[index].trim() && QUOTE.test(lines[index])) {
        body.push(lines[index].replace(QUOTE, ""));
        index += 1;
      }
      blocks.push({ type: "quote", children: parseMarkdown(body.join("\n")) });
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && TABLE_SEP.test(lines[index + 1])) {
      const header = splitRow(line);
      const align = splitRow(lines[index + 1]).map((cell) => (cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : undefined));
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        rows.push(splitRow(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", header, align, rows });
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const list = parseList(lines, index);
      blocks.push(list.block);
      index = list.next;
      continue;
    }
    const body = [];
    while (index < lines.length && lines[index].trim() && !FENCE.test(lines[index]) && !HEADING.test(lines[index]) && !QUOTE.test(lines[index])
      && !LIST_ITEM.test(lines[index]) && !RULE.test(lines[index])) {
      body.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: body.join("\n") });
  }
  return blocks;
}

function CodeBlock({ language, text }) {
  const [state, setState] = useState("idle");
  async function copy() {
    const result = await copyText(text);
    setState(result.ok ? "copied" : copyFailureMessage(result));
    window.setTimeout(() => setState("idle"), 2000);
  }
  return (
    <div className="code-wrap">
      <div className="code-head">
        <span className="code-lang">{language || "text"}</span>
        <button type="button" className="code-copy" onClick={copy} aria-label={`Copy ${language || "code"}`}>
          {state === "idle" ? "Copy" : state === "copied" ? "Copied" : state}
        </button>
      </div>
      <pre className="code-block" data-language={language || "text"}>
        <code>{text}</code>
      </pre>
    </div>
  );
}

function renderBlocks(blocks, ctx, keyBase = "b") {
  return blocks.map((block, index) => {
    const key = `${keyBase}-${index}`;
    switch (block.type) {
      case "code":
        return <CodeBlock key={key} language={block.language} text={block.text} />;
      case "heading": {
        const Tag = `h${Math.min(6, block.level + 2)}`;
        return <Tag key={key} className={`md-heading md-h${block.level}`}>{renderInline(block.text, ctx, key)}</Tag>;
      }
      case "rule":
        return <hr key={key} />;
      case "quote":
        return <blockquote key={key}>{renderBlocks(block.children, ctx, key)}</blockquote>;
      case "table":
        return (
          <div key={key} className="table-wrap">
            <table>
              <thead>
                <tr>{block.header.map((cell, c) => <th key={c} style={{ textAlign: block.align[c] }}>{renderInline(cell, ctx, `${key}-h${c}`)}</th>)}</tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>{block.header.map((_, c) => <td key={c} style={{ textAlign: block.align[c] }}>{renderInline(row[c] ?? "", ctx, `${key}-${r}-${c}`)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      case "list": {
        const Tag = block.ordered ? "ol" : "ul";
        return (
          <Tag key={key} start={block.ordered && block.start !== 1 ? block.start : undefined}>
            {block.items.map((item, i) => (
              <li key={i}>
                {withBreaks(item.text.join("\n"), ctx, `${key}-${i}`)}
                {renderBlocks(item.children, ctx, `${key}-${i}c`)}
              </li>
            ))}
          </Tag>
        );
      }
      default:
        return <p key={key}>{withBreaks(block.text, ctx, key)}</p>;
    }
  });
}

export function RichText({ text, onCite }) {
  return <div className="rich-text">{renderBlocks(parseMarkdown(text), { onCite })}</div>;
}
