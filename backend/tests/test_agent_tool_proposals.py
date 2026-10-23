from __future__ import annotations

import pytest

from backend.app.agent_tool_proposals import (
    AgentToolProposalError,
    parse_provider_tool_proposals,
    provider_tool_schemas,
    validate_provider_tool_proposals,
)


def test_anthropic_tool_use_is_normalized_with_provenance() -> None:
    result = parse_provider_tool_proposals(
        "anthropic",
        {
            "id": "msg_123",
            "content": [{"type": "tool_use", "id": "toolu_123", "name": "source_library.status", "input": {}}],
        },
        model="claude-sonnet-5",
        response_id="msg_123",
    )
    assert result["status"] == "AGENT_TOOL_PROPOSALS_VALID"
    assert result["proposals"][0]["provider_call_id"] == "toolu_123"
    assert result["proposals"][0]["tool"] == "source_library.status"
    assert len(result["proposal_sha256"]) == 64


def test_openai_arguments_must_be_valid_json_objects() -> None:
    with pytest.raises(AgentToolProposalError) as exc:
        parse_provider_tool_proposals(
            "openai_compatible_local",
            {"choices": [{"message": {"tool_calls": [{"id": "call_1", "function": {"name": "source_library.status", "arguments": "[]"}}]}}]},
            model="llama3.1:8b",
        )
    assert exc.value.status == "ERROR_AGENT_TOOL_ARGUMENTS_NOT_OBJECT"


@pytest.mark.parametrize(
    ("candidate", "status"),
    [
        ({"provider_call_id": "call_1", "tool": "shell.exec", "arguments": {}}, "ERROR_AGENT_TOOL_NOT_ALLOWED"),
        ({"provider_call_id": "call_1", "tool": "source_library.status", "arguments": []}, "ERROR_AGENT_TOOL_ARGUMENTS_NOT_OBJECT"),
        ({"provider_call_id": "call_1", "tool": "source_library.status", "arguments": {}}, "ERROR_AGENT_TOOL_PROPOSAL_DUPLICATE"),
    ],
)
def test_provider_proposals_fail_closed(candidate: dict, status: str) -> None:
    if status.endswith("DUPLICATE"):
        duplicate = {**candidate, "provider_call_id": "call_2"}
        candidates = [candidate, duplicate]
    else:
        candidates = [candidate]
    with pytest.raises(AgentToolProposalError) as exc:
        validate_provider_tool_proposals(candidates, provider="anthropic", model="claude-sonnet-5")
    assert exc.value.status == status


def test_provider_tool_schemas_are_allowlist_derived() -> None:
    names = {row["name"] for row in provider_tool_schemas()}
    assert "source_library.status" in names
    assert "shell.exec" not in names
