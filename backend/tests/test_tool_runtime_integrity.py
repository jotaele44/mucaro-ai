from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.tools import (
    TOOL_CALLS_NAME,
    TOOL_REGISTRY,
    ToolDefinition,
    ToolRuntimeError,
    approve_tool_call,
    arguments_digest,
    deny_tool_call,
    list_tool_audit_events,
    list_tool_calls,
    request_tool_call,
    run_tool_call,
    verify_tool_audit_chain,
)
from backend.app.utils import read_json, write_json
from backend.tests.test_api import make_settings

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


def register_probe(monkeypatch: pytest.MonkeyPatch, handler) -> list[dict]:
    calls: list[dict] = []

    def recording_handler(arguments, cfg):
        calls.append(dict(arguments))
        return handler(arguments, cfg)

    monkeypatch.setitem(
        TOOL_REGISTRY,
        "test.probe",
        ToolDefinition(
            name="test.probe",
            description="Controlled test handler.",
            mutates_state=True,
            requires_approval=True,
            handler=recording_handler,
        ),
    )
    return calls


def edit_ledger(cfg: Settings, tool_call_id: str, **fields) -> None:
    path = cfg.processed_dir / TOOL_CALLS_NAME
    calls = read_json(path, [])
    for call in calls:
        if call["tool_call_id"] == tool_call_id:
            for key, value in fields.items():
                if value is None:
                    call.pop(key, None)
                else:
                    call[key] = value
    write_json(path, calls)


def ledger_call(cfg: Settings, tool_call_id: str) -> dict:
    return next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_call_id"] == tool_call_id)


def audit_events(cfg: Settings) -> list[str]:
    return [event["event"] for event in list_tool_audit_events(cfg)["events"]]


def test_success_path_keeps_the_argument_digest_and_audit_sequence(cfg: Settings) -> None:
    requested = request_tool_call("source_library.status", {"b": 2, "a": 1}, cfg=cfg, requested_by="test")
    tool_call_id = requested["tool_call"]["tool_call_id"]
    assert requested["tool_call"]["arguments_sha256"] == arguments_digest({"a": 1, "b": 2})

    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["tool_call"]["status"] == "EXECUTED"
    assert ledger_call(cfg, tool_call_id) == executed["tool_call"]
    assert audit_events(cfg) == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_STARTED", "TOOL_CALL_EXECUTED"]
    assert verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"


def test_arguments_changed_before_approval_are_never_approved(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    requested = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]
    tool_call_id = requested["tool_call_id"]
    edit_ledger(cfg, tool_call_id, arguments={"url": "https://attacker.invalid"})

    # The UI sends back the digest the ledger shows; that no longer vouches for the arguments.
    for expected in (None, requested["arguments_sha256"]):
        with pytest.raises(ToolRuntimeError) as exc:
            approve_tool_call(tool_call_id, cfg=cfg, approved_by="test", expected_arguments_sha256=expected)
        assert exc.value.status == "ERROR_TOOL_BINDING_MISMATCH"

    assert ledger_call(cfg, tool_call_id)["status"] == "PENDING_APPROVAL"
    assert audit_events(cfg) == ["TOOL_CALL_REQUESTED"]
    # The operator can still close the call.
    assert deny_tool_call(tool_call_id, cfg=cfg)["tool_call"]["status"] == "DENIED"
    assert calls == []


def test_arguments_changed_after_approval_never_execute(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    tool_call_id = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    edit_ledger(cfg, tool_call_id, arguments={"url": "https://attacker.invalid"})

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_TOOL_BINDING_MISMATCH"
    assert calls == []
    failed = ledger_call(cfg, tool_call_id)
    assert failed["status"] == "FAILED"
    assert failed["result"]["status"] == "ERROR_TOOL_BINDING_MISMATCH"
    assert audit_events(cfg)[-1] == "TOOL_CALL_FAILED"
    assert "TOOL_CALL_STARTED" not in audit_events(cfg)
    assert verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"

    with pytest.raises(ToolRuntimeError) as rerun:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert rerun.value.status == "ERROR_TOOL_CALL_NOT_APPROVED"
    assert calls == []


def test_call_without_an_argument_digest_fails_closed(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    pending = request_tool_call("test.probe", {}, cfg=cfg)["tool_call"]["tool_call_id"]
    approved = request_tool_call("test.probe", {"n": 1}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(approved, cfg=cfg, approved_by="test")
    edit_ledger(cfg, pending, arguments_sha256=None)
    edit_ledger(cfg, approved, arguments_sha256=None)

    with pytest.raises(ToolRuntimeError) as not_approved:
        approve_tool_call(pending, cfg=cfg, approved_by="test")
    assert not_approved.value.status == "ERROR_TOOL_BINDING_MISSING"
    with pytest.raises(ToolRuntimeError) as not_run:
        run_tool_call(approved, cfg=cfg, executed_by="test")
    assert not_run.value.status == "ERROR_TOOL_BINDING_MISSING"

    assert ledger_call(cfg, pending)["status"] == "PENDING_APPROVAL"
    assert ledger_call(cfg, approved)["status"] == "FAILED"
    assert calls == []


def test_calls_requested_while_a_handler_runs_are_preserved(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    requested_during: list[str] = []

    def requests_another_call(arguments, cfg):
        requested_during.append(request_tool_call("source_library.status", {}, cfg=cfg)["tool_call"]["tool_call_id"])
        return {"status": "PROBE_OK"}

    register_probe(monkeypatch, requests_another_call)
    tool_call_id = request_tool_call("test.probe", {}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg)
    run_tool_call(tool_call_id, cfg=cfg)

    statuses = {call["tool_call_id"]: call["status"] for call in list_tool_calls(cfg)["tool_calls"]}
    assert statuses == {tool_call_id: "EXECUTED", requested_during[0]: "PENDING_APPROVAL"}


def _run(root: Path, code: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    # The repository stays the project root (contract schemas live there); only the data moves.
    env = os.environ.copy()
    env.pop("MUCARO_AI_ROOT", None)
    env["MUCARO_AI_DATA_DIR"] = str(root / "data")
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def test_process_death_mid_handler_is_reconciled_in_a_fresh_process(tmp_path: Path) -> None:
    writer = _run(
        tmp_path,
        """
import os
from backend.app.config import Settings
from backend.app.tools import TOOL_REGISTRY, ToolDefinition, approve_tool_call, request_tool_call, run_tool_call

def dies(arguments, cfg):
    os._exit(17)

TOOL_REGISTRY["test.probe"] = ToolDefinition(
    name="test.probe", description="dies", mutates_state=True, requires_approval=True, handler=dies
)
cfg = Settings.load()
tool_call_id = request_tool_call("test.probe", {"side_effect": "once"}, cfg=cfg)["tool_call"]["tool_call_id"]
approve_tool_call(tool_call_id, cfg=cfg)
print(tool_call_id, flush=True)
run_tool_call(tool_call_id, cfg=cfg)
""",
        check=False,
    )
    assert writer.returncode == 17, writer.stderr
    tool_call_id = writer.stdout.strip()

    reader = _run(
        tmp_path,
        f"""
import json
from backend.app.config import Settings
from backend.app.tools import ToolRuntimeError, list_tool_calls, reconcile_interrupted_tool_calls, run_tool_call
cfg = Settings.load()

def refusal():
    try:
        run_tool_call("{tool_call_id}", cfg=cfg)
    except ToolRuntimeError as exc:
        return exc.status
    return None

before = list_tool_calls(cfg)["tool_calls"][0]["status"]
while_running = refusal()
reconciled = reconcile_interrupted_tool_calls(cfg)["interrupted"]
after = list_tool_calls(cfg)["tool_calls"][0]["status"]
print(json.dumps({{"before": before, "while_running": while_running, "reconciled": reconciled, "after": after, "rerun": refusal()}}))
""",
    )
    assert json.loads(reader.stdout) == {
        "before": "RUNNING",
        "while_running": "ERROR_TOOL_CALL_ALREADY_RUNNING",
        "reconciled": [tool_call_id],
        "after": "INTERRUPTED",
        "rerun": "ERROR_TOOL_CALL_NOT_APPROVED",
    }


def test_rewriting_arguments_and_digest_together_is_caught_by_the_audit_log(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    forged = {"url": "https://attacker.invalid"}

    before = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]["tool_call_id"]
    edit_ledger(cfg, before, arguments=forged, arguments_sha256=arguments_digest(forged))
    with pytest.raises(ToolRuntimeError) as not_approved:
        approve_tool_call(before, cfg=cfg, approved_by="test")
    assert not_approved.value.status == "ERROR_TOOL_BINDING_MISMATCH"
    assert ledger_call(cfg, before)["status"] == "PENDING_APPROVAL"

    after = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(after, cfg=cfg, approved_by="test")
    edit_ledger(cfg, after, arguments=forged, arguments_sha256=arguments_digest(forged))
    with pytest.raises(ToolRuntimeError) as not_run:
        run_tool_call(after, cfg=cfg, executed_by="test")
    assert not_run.value.status == "ERROR_TOOL_BINDING_MISMATCH"
    assert ledger_call(cfg, after)["status"] == "FAILED"
    assert calls == []


def test_a_broken_audit_chain_blocks_approval(cfg: Settings) -> None:
    from backend.app.tools import TOOL_AUDIT_NAME

    tool_call_id = request_tool_call("source_library.status", {}, cfg=cfg)["tool_call"]["tool_call_id"]
    path = cfg.processed_dir / TOOL_AUDIT_NAME
    events = read_json(path, [])
    events[-1]["actor"] = "someone-else"
    write_json(path, events)

    with pytest.raises(ToolRuntimeError) as exc:
        approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    assert exc.value.status == "ERROR_TOOL_AUDIT_CHAIN_BROKEN"
    assert ledger_call(cfg, tool_call_id)["status"] == "PENDING_APPROVAL"
