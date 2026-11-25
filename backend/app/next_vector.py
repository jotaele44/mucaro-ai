"""Model-proposed next vector: a bounded, inert proposal parsed from a finished answer.

Some investigations run as a relay: the answer to one prompt closes with a block that
names the next bounded operation, and the operator sends that block back as the next
prompt. The block has a small fixed grammar::

    NEXT VECTOR:
    <anchor>
    → <step>
    → <step>

    CROSS ALL T'S:
    <verification chain>
    → <check>

    MAXIMIZE YIELD:
    <reuse anchor>
    → <constraint>

    FINAL GATE:
    <gate name> = <claimed state>

This module turns such a block into a *proposal*. A proposal is data:

- It carries no authority. Starting it is always an explicit user action that goes
  through the ask-first ``work_runs`` path, and every tool call it leads to still
  needs its own approval (docs/GOAL_PLAN_EXECUTION_ENTRY_V1.md, N14).
- Gate rows are what the model *claimed*. They are recorded with ``verified: false``
  and never feed an execution gate.
- A block may not contain a line that starts with ``/``. ``work_runs`` reads such a
  line in a user message as a slash command, so relaying one would let model output
  create a tool request (INV-014). Those blocks are rejected, not repaired.
- Anything outside the stated bounds is rejected whole; the caller falls back to the
  deterministic choices.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from .injection_guard import scan_text
from .utils import stable_id

SCHEMA_VERSION = "mucaro-next-vector-proposal/1.0"
SUGGESTION_ID = "proposed"
MAX_BLOCK_CHARS = 4000
MAX_LINE_CHARS = 240
MAX_SECTIONS = 8
MAX_ROWS_PER_SECTION = 32
MAX_LABEL_CHARS = 120

# The reply-format contract. frontend/src/nextVectorOptions.js carries the same sentence for the
# deterministic choices; backend/tests/test_next_vector.py fails if the two copies drift.
RELAY_FORMAT_INSTRUCTION = (
    "End your reply with the next vector as one fenced text block: the line NEXT VECTOR: then an anchor line "
    "and → steps, then CROSS ALL T'S:, MAXIMIZE YIELD: and FINAL GATE: with one NAME = STATE row per gate. "
    "Keep every gate you have not verified OPEN or WITHHELD."
)
RELAY_EXECUTION_NOTE = (
    "Carry out this vector from the verified state of this conversation. Reuse existing evidence, keep "
    "unresolved items unresolved, and execute only actions permitted by the existing approval gates."
)

# Answer-trace flags that mean the block deserves a read before it is relayed.
_REVIEW_FLAGS = (
    ("WARN_UNTRUSTED_INSTRUCTIONS_IN_SOURCES", "INSTRUCTION_LIKE_TEXT_IN_SOURCES"),
    ("WARN_MODEL_OUTPUT_TRUNCATED", "ANSWER_TRUNCATED"),
)

STATUS_PROPOSED = "NEXT_VECTOR_PROPOSED"
STATUS_ABSENT = "NEXT_VECTOR_ABSENT"
STATUS_ECHO = "NEXT_VECTOR_ECHO"

_FENCE = re.compile(r"```[^\n`]*\n(.*?)```", re.DOTALL)
# Headers are upper case, as in the grammar above; a prose line that ends in a colon is not one.
_HEADER = re.compile(r"^([A-Z][A-Z0-9 '’/&._-]{1,38}):$")
_STEP = re.compile(r"^(?:→|->|=>|➜|⟶)\s*(\S.*)$")
_GATE_ROW = re.compile(r"^(.{1,160}?)\s=\s(.{1,80})$")
_BARE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{1,159}$")
# Every control character except tab and newline, including each one str.splitlines() breaks a line at
# (VT, FF, FS, GS, RS, NEL, LS, PS): work_runs splits a message that way before it looks for slash commands,
# so none of them may hide a second line inside a block. Lone surrogates are refused with them: they cannot
# be hashed or sent as UTF-8.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_IDENTITY = re.compile(r"[^A-Za-z0-9._:/+-]")
_FIRST_HEADER = "NEXT VECTOR"

_PASS = ("PASS",)
_FAIL = ("FAIL",)
_WITHHELD = ("WITHHELD", "BLOCKED", "DENIED", "REFUSED")
_OPEN = ("OPEN", "UNRESOLVED", "UNVERIFIED", "UNKNOWN", "PENDING", "PROVISIONAL", "PARTIAL", "AUDIT_ONLY", "CANDIDATE")


@cache
def _validator() -> jsonschema.protocols.Validator | None:
    path = Path(__file__).resolve().parents[2] / "schemas" / "next_vector_proposal.schema.json"
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        cls = jsonschema.validators.validator_for(schema)
        cls.check_schema(schema)
    except (OSError, ValueError, jsonschema.exceptions.SchemaError):
        return None
    return cls(schema)


def proposal_errors(proposal: Any) -> list[str]:
    """Contract violations for a proposal record; a missing schema is itself a violation (fail closed)."""
    validator = _validator()
    if validator is None:
        return ["schemas/next_vector_proposal.schema.json is unavailable or invalid"]
    errors = sorted(validator.iter_errors(proposal), key=lambda item: list(item.absolute_path))
    return [f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}" for error in errors]


def _utf16_len(text: str) -> int:
    """Length as the browser counts it, so a bound checked here holds in nextVectorOptions.js too."""
    return len(text.encode("utf-16-le")) // 2


def _reject(reason: str) -> tuple[None, str]:
    return None, f"NEXT_VECTOR_REJECTED_{reason}"


def _section_key(title: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", title.upper().replace("'", "").replace("’", "")).strip("_")


def _state_class(state: str) -> str:
    upper = state.upper()
    # Order matters: "NOT PASSED" style values are rare, but a FAIL or a withheld gate must never read as PASS.
    if any(word in upper for word in _FAIL):
        return "FAIL"
    if any(word in upper for word in _WITHHELD):
        return "WITHHELD"
    if any(word in upper for word in _OPEN):
        return "OPEN"
    if upper.startswith(_PASS):
        return "PASS"
    return "OTHER"


def _gate(row: str) -> dict[str, Any]:
    match = _GATE_ROW.match(row)
    if match:
        name, state = match.group(1).strip(), match.group(2).strip()
    else:
        # A bare token such as PRODUCTION_DEPLOYMENT_WITHHELD names the gate and its state together.
        name, state = row, "UNSPECIFIED"
        for word in (*_FAIL, *_WITHHELD, *_OPEN, *_PASS):
            if row.upper().endswith(word) and len(row) > len(word):
                name, state = row[: -len(word)].rstrip("_ -"), word
                break
    return {"gate": name, "claimed_state": state, "state_class": _state_class(state), "basis": "MODEL_CLAIM", "verified": False}


def _is_block_line(line: str, *, expects_anchor: bool, in_final: bool) -> bool:
    """Unfenced blocks only: decides where the block stops and ordinary prose resumes."""
    if not line or _HEADER.match(line) or _STEP.match(line) or expects_anchor:
        return True
    return in_final and bool(_GATE_ROW.match(line) or _BARE_TOKEN.match(line))


def _unfenced_block(text: str) -> str | None:
    lines = text.split("\n")
    start = None
    for index, raw in enumerate(lines):
        if raw.strip() == f"{_FIRST_HEADER}:":
            start = index  # the last one wins: the block closes the answer
    if start is None:
        return None
    kept: list[str] = []
    expects_anchor = False
    in_final = False
    for raw in lines[start:]:
        line = raw.strip()
        if line.startswith("```"):
            break
        header = _HEADER.match(line)
        if not _is_block_line(line, expects_anchor=expects_anchor, in_final=in_final):
            break
        kept.append(line)
        if header:
            in_final = _section_key(header.group(1)).startswith("FINAL")
            expects_anchor = not in_final
        else:
            expects_anchor = False  # an anchor sits directly under its header
    return "\n".join(kept)


def find_vector_block(answer: str) -> tuple[str, bool] | None:
    """The last NEXT VECTOR block in an answer and whether it was fenced, or None."""
    text = (answer or "").replace("\r\n", "\n").replace("\r", "\n")
    fenced = [
        match.group(1)
        for match in _FENCE.finditer(text)
        if match.group(1).lstrip().startswith(f"{_FIRST_HEADER}:")
    ]
    if fenced:
        return fenced[-1], True
    # Outside fences only: a fenced block that is not a vector block must not be read as one.
    block = _unfenced_block(_FENCE.sub("\n", text))
    return (block, False) if block is not None else None


def parse_vector_block(block: str) -> tuple[dict[str, Any] | None, str]:
    """Parse and bound one block. Returns (parsed, status); parsed is None unless status is PROPOSED."""
    block = (block or "").replace("\r\n", "\n").replace("\r", "\n")
    if _CONTROL.search(block):
        return _reject("CONTROL_CHARACTERS")
    lines = [line.strip() for line in block.replace("\t", " ").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines)
    if not text:
        return _reject("EMPTY")
    if _utf16_len(text) > MAX_BLOCK_CHARS:
        return _reject("TOO_LONG")
    if "```" in text:
        return _reject("NESTED_FENCE")

    sections: list[dict[str, Any]] = []
    for line in lines:
        if not line:
            continue
        if _utf16_len(line) > MAX_LINE_CHARS:
            return _reject("LINE_TOO_LONG")
        if line.startswith("/"):
            # work_runs would read this line of a user message as a slash command.
            return _reject("SLASH_COMMAND")
        header = _HEADER.match(line)
        if header:
            if len(sections) >= MAX_SECTIONS:
                return _reject("TOO_MANY_SECTIONS")
            title = " ".join(header.group(1).split())
            key = _section_key(title)
            if any(section["key"] == key for section in sections):
                return _reject("DUPLICATE_SECTION")
            sections.append({"key": key, "title": title.upper(), "anchor": None, "steps": [], "rows": []})
            continue
        if not sections:
            return _reject("NO_HEADER")
        section = sections[-1]
        if len(section["steps"]) + len(section["rows"]) >= MAX_ROWS_PER_SECTION:
            return _reject("TOO_MANY_ROWS")
        step = _STEP.match(line)
        if step:
            section["steps"].append(step.group(1).strip())
        elif section["anchor"] is None and not section["steps"] and not section["rows"] and not section["key"].startswith("FINAL"):
            section["anchor"] = line
        else:
            section["rows"].append(line)

    if not sections or sections[0]["key"] != _section_key(_FIRST_HEADER):
        return _reject("NO_HEADER")
    first = sections[0]
    if not first["anchor"] and not first["steps"]:
        return _reject("NO_STEPS")

    gates = [_gate(row) for section in sections if section["key"].startswith("FINAL") for row in (*section["rows"], *section["steps"])]
    return {
        "text": text,
        "anchor": first["anchor"] or first["steps"][0],
        "sections": sections,
        "claimed_gates": gates,
        "step_count": len(first["steps"]),
    }, STATUS_PROPOSED


def _normalized_lines(text: str) -> str:
    """Line endings and edge whitespace as parse_vector_block leaves them, so block text can be compared."""
    return "\n".join(line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ").split("\n"))


def _identity(value: Any, fallback: str) -> str:
    cleaned = _IDENTITY.sub("", str(value or ""))[:80]
    return cleaned or fallback


def build_relay_prompt(text: str, *, model_provider: Any, model_name: Any, work_run_id: str) -> str:
    """The exact message sent when the user starts the proposed vector. Shown in full before it is sent."""
    origin = f"{_identity(model_provider, 'unknown-provider')}/{_identity(model_name, 'unknown-model')}"
    return (
        f"Model-proposed next vector (written by {origin} in run {_identity(work_run_id, 'unknown-run')}; "
        f"context, not a new authorization):\n\n{text}\n\n{RELAY_EXECUTION_NOTE}\n{RELAY_FORMAT_INSTRUCTION}"
    )


def propose_next_vector(
    answer: str,
    *,
    work_run_id: str,
    answer_run_id: str | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    validation_flags: list[str] | tuple[str, ...] = (),
    network_tools: list[str] | tuple[str, ...] = (),
    prompt: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """(proposal, status) for a finished answer. The proposal is None when no valid block closes it.

    validation_flags are the answer trace's own flags; network_tools names the network tools whose
    output the run read. Both only describe where the text came from: they never widen what a
    proposal may do. prompt is the message the run answered: a block that only repeats the vector
    that message relayed is an echo, not a next vector, and proposes nothing.
    """
    found = find_vector_block(answer)
    if found is None:
        return None, STATUS_ABSENT
    block, fenced = found
    parsed, status = parse_vector_block(block)
    if parsed is None:
        return None, status
    text = parsed["text"]
    if prompt and text in _normalized_lines(prompt):
        return None, STATUS_ECHO
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    scan = scan_text(text)
    reasons = [reason for flag, reason in _REVIEW_FLAGS if flag in validation_flags]
    if scan["suspected"]:
        reasons.insert(0, "INSTRUCTION_LIKE_TEXT_IN_VECTOR")
    label = f"Model-proposed: {' '.join(parsed['anchor'].split())}"
    while _utf16_len(label) > MAX_LABEL_CHARS:
        label = label[:-2].rstrip() + "…"
    proposal = {
        "schema_version": SCHEMA_VERSION,
        "vector_id": stable_id("nvec", work_run_id, digest),
        "vector_sha256": digest,
        "status": "PROPOSED",
        "authority": "NONE",
        "source": {
            "kind": "MODEL_OUTPUT",
            "work_run_id": work_run_id,
            "answer_run_id": answer_run_id,
            "model_provider": model_provider,
            "model_name": model_name,
            "fenced": fenced,
            "network_tools": sorted({str(name) for name in network_tools}),
        },
        "anchor": parsed["anchor"],
        "step_count": parsed["step_count"],
        "sections": parsed["sections"],
        "claimed_gates": parsed["claimed_gates"],
        "text": text,
        # Advisory, like every injection_guard verdict: the interface stops preselecting a flagged
        # proposal and says why; it does not hide it and nothing is executed either way.
        "review": {"preselect": not reasons, "reasons": reasons, "signals": scan["signals"]},
        "suggestion": {
            "id": SUGGESTION_ID,
            "label": label,
            "prompt": build_relay_prompt(text, model_provider=model_provider, model_name=model_name, work_run_id=work_run_id),
        },
    }
    if proposal_errors(proposal):
        return _reject("CONTRACT")
    return proposal, STATUS_PROPOSED
