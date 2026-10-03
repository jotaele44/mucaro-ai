from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from backend.app.cli import main
from backend.tests.test_coding_workspace import make_settings

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def run(capsys: pytest.CaptureFixture[str], cfg, *argv: str) -> tuple[int, dict]:
    code = main(list(argv), cfg)
    captured = capsys.readouterr()
    return code, json.loads(captured.out or captured.err)


def declare(cfg, servers: dict) -> None:
    (cfg.data_dir / "mcp_servers.json").write_text(json.dumps({"servers": servers}), encoding="utf-8")


def test_mcp_servers_tools_and_call_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_settings(tmp_path)
    code, out = run(capsys, cfg, "mcp", "servers")
    assert code == 0 and out["servers"] == []

    declare(cfg, {"fake": {"command": [sys.executable, FAKE]}})
    code, out = run(capsys, cfg, "mcp", "servers")
    assert [s["name"] for s in out["servers"]] == ["fake"]

    code, err = run(capsys, cfg, "mcp", "tools", "fake")
    assert code == 2 and err["status"] == "ERROR_MCP_CONFIRMATION_REQUIRED"

    code, out = run(capsys, cfg, "mcp", "tools", "fake", "--yes")
    assert code == 0 and out["tools"][0]["name"] == "echo" and out["server_info"]["name"] == "fake"

    code, out = run(capsys, cfg, "mcp", "call", "fake", "echo", "--args", '{"text": "hi"}', "--yes")
    assert code == 0 and out["status"] == "MCP_TOOL_OK" and out["result"]["content"][0]["text"] == "hi"

    code, err = run(capsys, cfg, "mcp", "call", "fake", "boom", "--yes")
    assert code == 2 and err["status"] == "ERROR_MCP_SERVER_ERROR"


def test_mcp_rejects_unknown_server_bad_config_and_bad_args(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_settings(tmp_path)
    declare(cfg, {"fake": {"command": [sys.executable, FAKE]}})
    code, err = run(capsys, cfg, "mcp", "tools", "other", "--yes")
    assert code == 2 and err["status"] == "ERROR_MCP_SERVER_UNKNOWN"
    code, err = run(capsys, cfg, "mcp", "call", "fake", "echo", "--args", "{nope", "--yes")
    assert code == 2 and err["status"] == "ERROR_MCP_CALL_INVALID"

    declare(cfg, {"bad name": {"command": ["x"]}})
    code, err = run(capsys, cfg, "mcp", "servers")
    assert code == 2 and err["status"] == "ERROR_MCP_CONFIG_INVALID"

    declare(cfg, {"dead": {"command": ["/nonexistent/binary"]}})
    code, err = run(capsys, cfg, "mcp", "tools", "dead", "--yes")
    assert code == 2 and err["status"] == "ERROR_MCP_SERVER_START_FAILED"
