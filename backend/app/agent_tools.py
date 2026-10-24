"""Client tools Claude can call from the chat agent.

Every tool maps to a governed ``TOOL_REGISTRY`` entry, so each call is requested,
approved, executed and audited through the same runtime as slash commands.
``auto`` tools are local and read-only: in the agent mode they are approved by
policy. ``approval`` tools change state and always wait for the user.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .coding_patch import validate_patch_proposal
from .coding_workspace import CodingWorkspaceError
from .config import Settings

RESULT_TEXT_LIMIT = 60_000
SOURCE_URI = "mucaro://source/"


class AgentToolInputError(ValueError):
    """Input Claude sent cannot become a governed call; reported back to Claude as an error result."""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _quote(text: Any, limit: int = 60) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _name_of(path: Any) -> str:
    return Path(str(path or "")).name or str(path or "")


def _json_text(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > RESULT_TEXT_LIMIT:
        text = text[:RESULT_TEXT_LIMIT] + f'… [truncated {len(text) - RESULT_TEXT_LIMIT} characters]'
    return text


def source_uri(source_id: Any, chunk_id: Any = None) -> str:
    return f"{SOURCE_URI}{source_id}" + (f"#{chunk_id}" if chunk_id else "")


def _search_results(result: dict[str, Any]) -> Any:
    rows = result.get("results") or []
    if not rows:
        if result.get("status") == "SOURCE_SEARCH_NO_SOURCES":
            return "The user's document library is empty. Suggest adding files under Sources."
        return "No passages in the user's documents match that query."
    return [
        {
            "type": "search_result",
            "source": source_uri(row.get("source_id"), row.get("chunk_id")),
            "title": str(row.get("file_name") or row.get("source_id")),
            "content": [{"type": "text", "text": str(row.get("text"))}],
            "citations": {"enabled": True},
        }
        for row in rows
    ]


def _source_document(result: dict[str, Any]) -> Any:
    chunks = [chunk for chunk in result.get("chunks") or [] if str(chunk.get("text") or "").strip()]
    if not chunks:
        return "This document has no readable text."
    note = " (truncated: ask for a later part with search_sources)" if result.get("truncated") else ""
    return [
        {
            "type": "search_result",
            "source": source_uri(result.get("source_id")),
            "title": f"{result.get('file_name') or result.get('source_id')}{note}",
            "content": [{"type": "text", "text": str(chunk["text"])} for chunk in chunks],
            "citations": {"enabled": True},
        }
    ]


def _memory_rows(result: dict[str, Any]) -> str:
    rows = [{"kind": row.get("kind"), "content": row.get("content")} for row in result.get("memories") or []]
    return _json_text({"memories": rows}) if rows else "The user has no saved memories."


def _default_render(result: dict[str, Any]) -> str:
    return _json_text(result)


def _patch_arguments(tool_input: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Validate the patch now so the user approves the exact HEAD, patch hash and preimages that will be applied."""
    try:
        check = validate_patch_proposal(tool_input["repo_root"], tool_input["patch"], cfg=cfg)
    except CodingWorkspaceError as exc:
        raise AgentToolInputError(exc.status, exc.detail) from exc
    if not check.get("applicable"):
        raise AgentToolInputError(
            "ERROR_CODING_PATCH_NOT_APPLICABLE", f"git apply --check rejected the patch: {check.get('git_apply_detail') or 'no detail'}"
        )
    return {
        "repo_root": check["repo_root"],
        "patch": tool_input["patch"],
        "expected_head_sha": check["head_sha"],
        "expected_patch_sha256": check["patch_sha256"],
        "expected_preimages": check["preimages"],
        "summary": tool_input.get("summary") or "",
    }


@dataclass(frozen=True)
class AgentTool:
    name: str
    registry_name: str
    description: str
    input_schema: dict[str, Any]
    gate: str  # "auto" or "approval"
    progress: Callable[[dict[str, Any]], str]
    done: Callable[[dict[str, Any]], str]
    arguments: Callable[[dict[str, Any], Settings], dict[str, Any]] = lambda tool_input, cfg: dict(tool_input)
    render: Callable[[dict[str, Any]], Any] = _default_render

    def definition(self) -> dict[str, Any]:
        # Inputs stream as they are generated; validate() checks them before anything runs.
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema, "eager_input_streaming": True}

    def validate(self, tool_input: Any) -> str | None:
        if not isinstance(tool_input, dict):
            return "Tool input must be a JSON object."
        errors = sorted(Draft202012Validator(self.input_schema).iter_errors(tool_input), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            where = ".".join(str(part) for part in first.path) or "input"
            return f"Invalid input at {where}: {first.message}"
        return None


_PATH = {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Absolute path on the user's Mac."}
_REPO = {"type": "string", "minLength": 1, "maxLength": 4096, "description": "Absolute path to the repository root."}

AGENT_TOOLS: tuple[AgentTool, ...] = (
    AgentTool(
        name="search_sources",
        registry_name="sources.search",
        description=(
            "Search the user's own document library (files they added to Mucaro under Sources). Returns the most relevant "
            "passages as citable search results. Use it whenever the user's documents may contain the answer."
        ),
        input_schema=_object(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Passages to return (default 5)."},
            },
            ["query"],
        ),
        gate="auto",
        progress=lambda value: f"Searching your documents for “{_quote(value.get('query'))}”",
        done=lambda result: (
            "Your document library is empty"
            if result.get("status") == "SOURCE_SEARCH_NO_SOURCES"
            else f"Found {len(result.get('results') or [])} passages in your documents"
        ),
        render=_search_results,
    ),
    AgentTool(
        name="read_source",
        registry_name="sources.read",
        description="Read the text of one document from the user's library, by the source_id that search_sources returned.",
        input_schema=_object(
            {
                "source_id": {"type": "string", "minLength": 1, "maxLength": 200},
                "max_chars": {"type": "integer", "minimum": 1000, "maximum": 200000},
            },
            ["source_id"],
        ),
        gate="auto",
        progress=lambda value: "Opening a document from your library",
        done=lambda result: f"Read “{_quote(result.get('file_name'))}”",
        render=_source_document,
    ),
    AgentTool(
        name="list_memory",
        registry_name="memory.list",
        description="List the facts and preferences the user explicitly saved for you to remember across conversations.",
        input_schema=_object({}, []),
        gate="auto",
        progress=lambda value: "Checking your saved memory",
        done=lambda result: f"Found {len([row for row in result.get('memories') or []])} saved memories",
        arguments=lambda value, cfg: {"include_disabled": False},
        render=_memory_rows,
    ),
    AgentTool(
        name="remember",
        registry_name="memory.remember",
        description=(
            "Save one short fact or preference about the user for future conversations. The user approves every save, so "
            "call it only when they asked you to remember something or would clearly want it kept."
        ),
        input_schema=_object(
            {
                "content": {"type": "string", "minLength": 1, "maxLength": 800},
                "kind": {"type": "string", "enum": ["preference", "profile", "instruction", "fact"]},
            },
            ["content", "kind"],
        ),
        gate="approval",
        progress=lambda value: f"Asking to remember: “{_quote(value.get('content'))}”",
        done=lambda result: "Saved to memory",
    ),
    AgentTool(
        name="list_directory",
        registry_name="files.list_directory",
        description="List one folder on the user's Mac. Works only inside folders the user granted under Sources > Folder access.",
        input_schema=_object({"path": _PATH}, ["path"]),
        gate="auto",
        progress=lambda value: f"Listing {_quote(value.get('path'), 80)}",
        done=lambda result: f"Found {len(result.get('entries') or [])} items",
    ),
    AgentTool(
        name="read_file",
        registry_name="files.read_file",
        description=(
            "Read one file on the user's Mac (text, Markdown, PDF, Word, CSV, JSON, HTML, email and more). Works only inside "
            "folders the user granted under Sources > Folder access."
        ),
        input_schema=_object(
            {"path": _PATH, "max_chars": {"type": "integer", "minimum": 1000, "maximum": 200000}},
            ["path"],
        ),
        gate="auto",
        progress=lambda value: f"Reading {_quote(_name_of(value.get('path')))}",
        done=lambda result: f"Read {_quote(_name_of(result.get('path')))}" + (" (truncated)" if result.get("truncated") else ""),
    ),
    AgentTool(
        name="repo_status",
        registry_name="coding.repo_status",
        description="Inspect a local Git repository inside a granted folder: branch, HEAD commit and changed files. Read-only.",
        input_schema=_object({"repo_root": _REPO}, ["repo_root"]),
        gate="auto",
        progress=lambda value: f"Checking the repository at {_quote(_name_of(value.get('repo_root')))}",
        done=lambda result: f"Repository is on {result.get('branch') or 'a detached HEAD'}",
    ),
    AgentTool(
        name="repo_diff",
        registry_name="coding.diff",
        description="Show the uncommitted changes in a local Git repository inside a granted folder, compared with a base ref (default HEAD).",
        input_schema=_object({"repo_root": _REPO, "base": {"type": "string", "minLength": 1, "maxLength": 200}}, ["repo_root"]),
        gate="auto",
        progress=lambda value: f"Reading changes in {_quote(_name_of(value.get('repo_root')))}",
        done=lambda result: "Read the repository changes",
    ),
    AgentTool(
        name="read_repo_file",
        registry_name="coding.read_file",
        description="Read one file from a local Git repository inside a granted folder, by repository-relative path.",
        input_schema=_object({"repo_root": _REPO, "path": {"type": "string", "minLength": 1, "maxLength": 4096}}, ["repo_root", "path"]),
        gate="auto",
        progress=lambda value: f"Reading {_quote(value.get('path'))}",
        done=lambda result: f"Read {_quote(result.get('path') or 'the file')}",
    ),
    AgentTool(
        name="apply_patch",
        registry_name="coding.patch_apply",
        description=(
            "Apply a unified diff (git format, with diff --git headers) to a local Git repository inside a granted folder. "
            "The patch is validated first; if it does not apply cleanly you get the reason and nothing changes. The user "
            "approves every patch, so include a one-line summary of what it changes."
        ),
        input_schema=_object(
            {
                "repo_root": _REPO,
                "patch": {"type": "string", "minLength": 1, "maxLength": 1_000_000},
                "summary": {"type": "string", "minLength": 1, "maxLength": 300},
            },
            ["repo_root", "patch", "summary"],
        ),
        gate="approval",
        progress=lambda value: f"Asking to apply a patch: {_quote(value.get('summary'), 80)}",
        done=lambda result: "Applied the patch",
        arguments=_patch_arguments,
    ),
)

AGENT_TOOLS_BY_NAME: dict[str, AgentTool] = {tool.name: tool for tool in AGENT_TOOLS}


def client_tool_definitions() -> list[dict[str, Any]]:
    return [tool.definition() for tool in AGENT_TOOLS]


def error_text(status: str, detail: str | None) -> str:
    return f"{status}: {detail}" if detail else status
