from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.agent_workflow import AgentWorkflowError, normalize_agent_actions
from backend.app.cli import main
from backend.tests.test_api import make_settings


def test_agent_plan_cli_is_read_only_and_bounded(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    assert main(["agent", "plan", "Check", "source", "status", "--format", "json"], cfg=cfg) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "AGENT_PLAN_READY"
    assert payload["actions"][0]["tool"] == "source_library.status"


def test_agent_validate_cli_accepts_anthropic_tool_use(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    payload_path = tmp_path / "provider.json"
    payload_path.write_text(
        json.dumps(
            {
                "id": "msg_cli",
                "content": [{"type": "tool_use", "id": "toolu_cli", "name": "source_library.status", "input": {}}],
            }
        ),
        encoding="utf-8",
    )
    assert main(
        ["agent", "validate-proposals", "anthropic", str(payload_path), "--model", "claude-sonnet-5", "--format", "json"],
        cfg=cfg,
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "AGENT_TOOL_PROPOSALS_VALID"
    assert result["proposal_sha256"]


def test_agent_fleet_cli_rejects_invalid_plan_without_execution(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    plan_path = tmp_path / "fleet.json"
    plan_path.write_text(json.dumps({"objective": "", "agents": []}), encoding="utf-8")
    assert main(["agent", "fleet", str(plan_path), "--format", "json"], cfg=cfg) == 2
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "ERROR_AGENT_FLEET_OBJECTIVE"


def test_agent_workflow_reorders_dependencies_deterministically() -> None:
    actions = normalize_agent_actions(
        [
            {"id": "answer", "tool": "product.audit", "arguments": {}, "depends_on": ["inspect"]},
            {"id": "inspect", "tool": "source_library.status", "arguments": {}, "depends_on": []},
        ],
        max_actions=4,
    )
    assert [row["id"] for row in actions] == ["inspect", "answer"]


@pytest.mark.parametrize(
    ("actions", "status"),
    [
        ([{"id": "a", "depends_on": ["missing"]}], "ERROR_AGENT_WORKFLOW_UNKNOWN_DEPENDENCY"),
        ([{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}], "ERROR_AGENT_WORKFLOW_CYCLE"),
    ],
)
def test_agent_workflow_rejects_invalid_graph(actions: list[dict[str, object]], status: str) -> None:
    with pytest.raises(AgentWorkflowError) as exc:
        normalize_agent_actions(actions, max_actions=4)
    assert exc.value.status == status
