from __future__ import annotations

from pathlib import Path

from backend.app.cli import main
from backend.tests.test_api import make_settings


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
