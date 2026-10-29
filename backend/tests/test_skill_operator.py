from __future__ import annotations

from pathlib import Path
import json
import pytest

from backend.app.cli import main
from backend.tests.test_api import make_settings
from backend.app.skill_runtime import activate_skill, deactivate_skill, list_skill_executions, mount_skill
from backend.app.tools import ToolRuntimeError, approve_tool_call, request_tool_call, run_tool_call


SKILL_ID = "mucaro/intelligence-plane-health"


def test_skill_cli_mount_activate_execute_and_list_receipts(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    assert main(["skills", "mount", SKILL_ID, "--format", "json"], cfg=cfg) == 0
    assert '"status": "SKILL_MOUNTED"' in capsys.readouterr().out

    assert main(["skills", "activate", SKILL_ID, "--policy-id", "cli-skill-policy", "--format", "json"], cfg=cfg) == 0
    activation = capsys.readouterr().out
    assert '"status": "SKILL_ACTIVATED"' in activation
    assert '"policy_id": "cli-skill-policy"' in activation

    assert main(["skills", "execute", SKILL_ID, "--arguments", "{}", "--format", "json"], cfg=cfg) == 0
    execution = capsys.readouterr().out
    assert '"status": "SKILL_EXECUTED"' in execution
    assert '"artifact_lineage"' in execution

    assert main(["skills", "executions", "--format", "json"], cfg=cfg) == 0
    assert '"status": "SKILL_EXECUTIONS_OK"' in capsys.readouterr().out


def test_skill_cli_requires_activation_before_invocation(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    assert main(["skills", "execute", SKILL_ID, "--format", "json"], cfg=cfg) == 2
    output = capsys.readouterr().err
    assert "ERROR_SKILL_NOT_ACTIVE" in output


def test_skill_cli_deactivation_revokes_and_can_be_explicitly_reauthorized(tmp_path: Path, capsys) -> None:
    cfg = make_settings(tmp_path)
    for command in ("mount", "activate", "execute", "deactivate"):
        assert main(["skills", command, SKILL_ID], cfg=cfg) == 0
        result = json.loads(capsys.readouterr().out)
    assert result["status"] == "SKILL_DEACTIVATED"
    assert main(["skills", "execute", SKILL_ID], cfg=cfg) == 2
    assert "ERROR_SKILL_NOT_ACTIVE" in capsys.readouterr().err
    assert main(["skills", "deactivate", SKILL_ID], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SKILL_ALREADY_INACTIVE"
    assert main(["skills", "activate", SKILL_ID], cfg=cfg) == 0
    capsys.readouterr()
    assert main(["skills", "execute", SKILL_ID], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "SKILL_EXECUTED"


def test_previously_approved_tool_invocation_cannot_reuse_a_revoked_skill_grant(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    mount_skill(SKILL_ID, cfg=cfg)
    activate_skill(SKILL_ID, cfg=cfg)
    call = request_tool_call("skill.execute", {"skill_id": SKILL_ID}, cfg=cfg)["tool_call"]
    approve_tool_call(call["tool_call_id"], cfg=cfg, expected_arguments_sha256=call["arguments_sha256"])
    deactivate_skill(SKILL_ID, cfg=cfg)
    with pytest.raises(ToolRuntimeError) as refused:
        run_tool_call(call["tool_call_id"], cfg=cfg)
    assert refused.value.status == "ERROR_SKILL_NOT_ACTIVE"
    assert list_skill_executions(cfg)["executions"] == []
