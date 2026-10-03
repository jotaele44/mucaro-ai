"""Untrusted-content handling and prompt-injection signals (TGT-022, INV-014).

The hard guarantees are structural, not lexical:

- tool calls are created only from the user's own message (slash commands),
  never from retrieved text, web pages, documents or model output;
- approvals come only from explicit user decisions or the documented auto
  policy, never from memory or content;
- retrieved text reaches models only inside fenced <source> data blocks whose
  closing tag cannot be forged, under a system prompt that denies it authority;
- prompts are built only from retrieved text, conversation and memory, never
  from environment variables or credentials.

This module adds advisory detection on top: instruction-like text in sources
or model output is surfaced as a warning so the user can see it, but nothing
is executed or blocked because of a detector verdict.
"""

from __future__ import annotations

import re
from typing import Any

SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OVERRIDE_INSTRUCTIONS", re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|system|your)\b.{0,40}\b(instructions?|prompts?|rules?|guidelines?)\b",
        re.IGNORECASE | re.DOTALL)),
    ("ROLE_HIJACK", re.compile(r"\b(you are now|act as (an?|the)|new instructions|system prompt|developer mode|jailbreak)\b", re.IGNORECASE)),
    ("TOOL_INVOCATION", re.compile(
        r"(^|\s)/(fetch|search|render|repo|diff|status|audit|memory)\b|\b(call|run|execute|invoke|use)\b.{0,20}\b(tools?|commands?|functions?|shell)\b",
        re.IGNORECASE | re.MULTILINE)),
    ("APPROVAL_FORGERY", re.compile(r"\b(auto-?approve|approve (all|every|this)|allow once|grant (access|permissions?)|pre-?approved)\b", re.IGNORECASE)),
    ("EXFILTRATION", re.compile(
        r"\b(send|post|upload|exfiltrate|leak|forward|email)\b.{0,60}(https?://|\bto (this|the following) (url|address|endpoint|email))",
        re.IGNORECASE | re.DOTALL)),
    ("SECRET_REQUEST", re.compile(
        r"\b(reveal|print|show|send|output|include|leak)\b.{0,40}\b(api[_ -]?keys?|passwords?|secrets?|tokens?|credentials?|environment variables?)\b",
        re.IGNORECASE | re.DOTALL)),
)

FENCE_OPEN = "<source"
FENCE_CLOSE = "</source>"


def scan_text(text: str) -> dict[str, Any]:
    matches = []
    for signal, pattern in SIGNAL_PATTERNS:
        found = pattern.search(text or "")
        if found:
            start = max(0, found.start() - 30)
            matches.append({"signal": signal, "excerpt": " ".join((text or "")[start:found.end() + 30].split())[:160]})
    return {"suspected": bool(matches), "signals": [row["signal"] for row in matches], "matches": matches}


def scan_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flagged = []
    for chunk in chunks:
        result = scan_text(str(chunk.get("text") or ""))
        if result["suspected"]:
            flagged.append({"chunk_id": chunk.get("chunk_id"), "source_id": chunk.get("source_id"), "signals": result["signals"], "matches": result["matches"]})
    return flagged


def neutralize_fence(text: str, tag: str = "source") -> str:
    """Untrusted text cannot close or open a `<tag>` fence (prevents fence-breaking injection)."""
    return re.sub(rf"<\s*(/?)\s*{re.escape(tag)}", lambda match: f"<{match.group(1)}{tag}-text", text, flags=re.IGNORECASE)
