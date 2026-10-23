from __future__ import annotations

import json
from pathlib import Path

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
