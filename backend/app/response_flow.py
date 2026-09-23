"""Channel neutral, immutable response assembly for saved conversations.

Only the web adapter is wired in this release. Other channels consume the same
envelope without changing its parts. No code execution occurs in this module.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4


FENCE = re.compile(r"(?m)^```([^\n`]*)\n([\s\S]*?)^```[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Part:
    part_id: str
    part_type: str
    content: str = ""
    language: str = ""
    filename: str | None = None
    executable: bool = False
    prompt: str = ""
    options: tuple[tuple[str, str], ...] = ()
    interaction_id: str | None = None
    allow_freeform: bool = True
    multi_select: bool = False


def content_parts(content: str) -> tuple[Part, ...]:
    parts: list[Part] = []
    start = 0
    for match in FENCE.finditer(content):
        if match.start() > start:
            parts.append(Part(str(uuid4()), "TEXT", content=content[start:match.start()]))
        parts.append(Part(str(uuid4()), "CODE_BLOCK", content=match.group(2), language=match.group(1).strip().split(" ", 1)[0], executable=False))
        start = match.end()
    if start < len(content) or not parts:
        parts.append(Part(str(uuid4()), "TEXT", content=content[start:]))
    return tuple(parts)


def assemble(session_id: str, turn_id: str, content: str, interaction: dict[str, Any] | None = None) -> dict[str, Any]:
    parts = list(content_parts(content))
    if interaction is not None:
        kind = interaction.get("type")
        prompt = str(interaction.get("prompt", "")).strip()
        if not prompt or kind not in {"CLARIFICATION_REQUEST", "MULTIPLE_CHOICE"}:
            raise ValueError("ERROR_INVALID_INTERACTION")
        options = tuple((str(o["id"]), str(o["label"])) for o in interaction.get("options", []))
        if kind == "MULTIPLE_CHOICE" and (len(options) < 2 or len(options) > 20 or len({o[0] for o in options}) != len(options) or any(not i or not label for i, label in options)):
            raise ValueError("ERROR_INVALID_OPTIONS")
        if kind == "CLARIFICATION_REQUEST" and options:
            raise ValueError("ERROR_INVALID_OPTIONS")
        parts.append(Part(str(uuid4()), kind, prompt=prompt, options=options, interaction_id=str(uuid4())))
    interactive = [p for p in parts if p.interaction_id]
    if len(interactive) > 1:
        raise ValueError("ERROR_MULTIPLE_PENDING_INTERACTIONS")
    state = "AWAITING_CHOICE" if interactive and interactive[0].part_type == "MULTIPLE_CHOICE" else "AWAITING_CLARIFICATION" if interactive else "NONE"
    return {"envelope_id": str(uuid4()), "session_id": session_id, "turn_id": turn_id,
            "parts": [asdict(p) for p in parts], "interaction_state": state,
            "pending_interaction_id": interactive[0].interaction_id if interactive else None}


def resolve(pending: dict[str, Any], raw_text: str, *, choice_id: str | None = None, interaction_id: str | None = None, dismiss: bool = False) -> tuple[str, str]:
    """Return (resolution state, value); reject stale and unknown button IDs."""
    if interaction_id and interaction_id != pending["interaction_id"]:
        raise ValueError("ERROR_STALE_INTERACTION")
    if dismiss:
        if choice_id:
            raise ValueError("ERROR_INVALID_DISMISSAL")
        return "DISMISSED", raw_text
    if pending["part_type"] == "MULTIPLE_CHOICE":
        options = pending["options"]
        if choice_id is not None:
            matched = [o for o in options if o[0] == choice_id]
            if len(matched) != 1:
                raise ValueError("ERROR_INVALID_CHOICE")
            return "RESOLVED", matched[0][1]
        exact = [o for o in options if raw_text.casefold().strip() in {o[0].casefold(), o[1].casefold()}]
        if len(exact) == 1:
            return "RESOLVED", exact[0][1]
        if len(exact) > 1:
            raise ValueError("ERROR_AMBIGUOUS_CHOICE")
        if not pending.get("allow_freeform", True):
            raise ValueError("ERROR_CHOICE_REQUIRED")
    if not raw_text.strip():
        raise ValueError("ERROR_EMPTY_INTERACTION_RESPONSE")
    return "RESOLVED", raw_text


def web_render(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Explicit web payload; preserve stored envelope and keep code inert."""
    rendered = []
    for part in envelope["parts"]:
        kind = part["part_type"]
        if kind == "CODE_BLOCK":
            rendered.append({"type": "code_block", "content": part["content"], "language": part["language"], "filename": part["filename"], "copy_button": True, "run_button": False})
        elif kind == "TEXT":
            rendered.append({"type": "text", "content": part["content"]})
        else:
            rendered.append({"type": "choice" if kind == "MULTIPLE_CHOICE" else "clarification", "prompt": part["prompt"], "interaction_id": part["interaction_id"], "options": [{"id": i, "label": label} for i, label in part["options"]], "freeform_input": part["allow_freeform"], "multi_select": part["multi_select"]})
    return rendered
