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
    list_tool_audit_events,
    list_tool_calls,
    recover_tool_call,
    request_tool_call,
    run_tool_call,
)
from backend.app.utils import read_json, write_json

REPO = Path(__file__).resolve().parents[2]
SENSITIVE = "sensitive-handler-content"


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


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


def tamper_arguments(cfg: Settings, tool_call_id: str, arguments: dict) -> None:
    path = cfg.processed_dir / TOOL_CALLS_NAME
    calls = read_json(path, [])
    for call in calls:
        if call["tool_call_id"] == tool_call_id:
            call["arguments"] = arguments
    write_json(path, calls)


def ledger_call(cfg: Settings, tool_call_id: str) -> dict:
    return next(call for call in list_tool_calls(cfg)["tool_calls"] if call["tool_call_id"] == tool_call_id)


def audit_events(cfg: Settings) -> list[str]:
    return [event["event"] for event in list_tool_audit_events(cfg)["events"]]


def test_success_path_binds_arguments_and_keeps_audit_sequence(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    requested = request_tool_call("source_library.status", {"b": 2, "a": 1}, cfg=cfg, requested_by="test")
    tool_call_id = requested["tool_call"]["tool_call_id"]
    binding = requested["tool_call"]["binding_sha256"]
    assert len(binding) == 64

    approved = approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    assert approved["tool_call"]["approved_binding_sha256"] == binding

    executed = run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert executed["tool_call"]["status"] == "EXECUTED"
    assert executed["tool_call"]["started_at"]
    assert ledger_call(cfg, tool_call_id) == executed["tool_call"]
    assert audit_events(cfg) == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED", "TOOL_CALL_EXECUTED"]


def test_arguments_changed_before_approval_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    tool_call_id = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]["tool_call_id"]
    tamper_arguments(cfg, tool_call_id, {"url": "https://attacker.invalid"})

    with pytest.raises(ToolRuntimeError) as exc:
        approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    assert exc.value.status == "ERROR_TOOL_BINDING_MISMATCH"
    assert ledger_call(cfg, tool_call_id)["status"] == "PENDING_APPROVAL"
    assert audit_events(cfg)[-1] == "TOOL_CALL_BINDING_REJECTED"
    assert calls == []


def test_arguments_changed_after_approval_never_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    tool_call_id = request_tool_call("test.probe", {"url": "https://example.com"}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    tamper_arguments(cfg, tool_call_id, {"url": "https://attacker.invalid"})

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_TOOL_BINDING_MISMATCH"
    assert calls == []
    failed = ledger_call(cfg, tool_call_id)
    assert failed["status"] == "FAILED"
    assert failed["result"]["status"] == "ERROR_TOOL_BINDING_MISMATCH"
    assert audit_events(cfg)[-1] == "TOOL_CALL_FAILED"


def test_legacy_approval_without_binding_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
    calls = register_probe(monkeypatch, lambda arguments, cfg: {"status": "PROBE_OK"})
    tool_call_id = request_tool_call("test.probe", {}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")
    path = cfg.processed_dir / TOOL_CALLS_NAME
    ledger = read_json(path, [])
    for call in ledger:
        call.pop("binding_sha256", None)
        call.pop("approved_binding_sha256", None)
    write_json(path, ledger)

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_TOOL_APPROVAL_BINDING_MISSING"
    assert calls == []
    assert ledger_call(cfg, tool_call_id)["status"] == "FAILED"


def test_unexpected_handler_exception_is_recorded_and_not_rerunnable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)

    def broken(arguments, cfg):
        raise ValueError(SENSITIVE)

    calls = register_probe(monkeypatch, broken)
    tool_call_id = request_tool_call("test.probe", {}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    assert exc.value.status == "ERROR_TOOL_HANDLER_EXCEPTION"
    assert isinstance(exc.value.__cause__, ValueError)
    failed = ledger_call(cfg, tool_call_id)
    assert failed["status"] == "FAILED"
    assert failed["result"] == {"status": "ERROR_TOOL_HANDLER_EXCEPTION", "detail": "ValueError"}
    assert SENSITIVE not in json.dumps(list_tool_calls(cfg)) + json.dumps(list_tool_audit_events(cfg))
    assert audit_events(cfg)[-1] == "TOOL_CALL_FAILED"

    with pytest.raises(ToolRuntimeError) as rerun:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert rerun.value.status == "ERROR_TOOL_CALL_NOT_APPROVED"
    assert len(calls) == 1


def test_interrupted_execution_is_never_repeated_and_recovers_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_settings(tmp_path)

    class ProcessKilled(BaseException):
        pass

    def killed(arguments, cfg):
        raise ProcessKilled()

    calls = register_probe(monkeypatch, killed)
    tool_call_id = request_tool_call("test.probe", {"side_effect": "once"}, cfg=cfg)["tool_call"]["tool_call_id"]
    approve_tool_call(tool_call_id, cfg=cfg, approved_by="test")

    with pytest.raises(ProcessKilled):
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")

    in_flight = ledger_call(cfg, tool_call_id)
    assert in_flight["status"] == "EXECUTING"
    assert in_flight["started_at"]
    assert audit_events(cfg) == ["TOOL_CALL_REQUESTED", "TOOL_CALL_APPROVED"]

    with pytest.raises(ToolRuntimeError) as rerun:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert rerun.value.status == "ERROR_TOOL_CALL_IN_FLIGHT"
    assert len(calls) == 1

    recovered = recover_tool_call(tool_call_id, cfg=cfg, recovered_by="test", reason="worker died")
    assert recovered["tool_call"]["status"] == "INTERRUPTED"
    assert recovered["tool_call"]["recovered_by"] == "test"
    assert audit_events(cfg)[-1] == "TOOL_CALL_INTERRUPTED"

    with pytest.raises(ToolRuntimeError) as after:
        run_tool_call(tool_call_id, cfg=cfg, executed_by="test")
    assert after.value.status == "ERROR_TOOL_CALL_INTERRUPTED"
    with pytest.raises(ToolRuntimeError) as again:
        recover_tool_call(tool_call_id, cfg=cfg)
    assert again.value.status == "ERROR_TOOL_CALL_NOT_IN_FLIGHT"
    assert len(calls) == 1


def test_recover_rejects_calls_that_are_not_in_flight(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    tool_call_id = request_tool_call("source_library.status", {}, cfg=cfg)["tool_call"]["tool_call_id"]

    with pytest.raises(ToolRuntimeError) as pending:
        recover_tool_call(tool_call_id, cfg=cfg)
    assert pending.value.status == "ERROR_TOOL_CALL_NOT_IN_FLIGHT"

    approve_tool_call(tool_call_id, cfg=cfg)
    run_tool_call(tool_call_id, cfg=cfg)
    with pytest.raises(ToolRuntimeError) as executed:
        recover_tool_call(tool_call_id, cfg=cfg)
    assert executed.value.status == "ERROR_TOOL_CALL_NOT_IN_FLIGHT"


def test_calls_requested_while_a_handler_runs_are_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_settings(tmp_path)
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
    env = os.environ.copy()
    env["MUCARO_AI_ROOT"] = str(root)
    env["PYTHONPATH"] = str(REPO)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def test_process_death_mid_handler_is_recovered_in_a_fresh_process(tmp_path: Path) -> None:
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
    assert writer.returncode == 17
    tool_call_id = writer.stdout.strip()

    reader = _run(
        tmp_path,
        f"""
import json
from backend.app.config import Settings
from backend.app.tools import ToolRuntimeError, list_tool_calls, recover_tool_call, run_tool_call
cfg = Settings.load()
before = list_tool_calls(cfg)["tool_calls"][0]
try:
    run_tool_call("{tool_call_id}", cfg=cfg)
    refused = None
except ToolRuntimeError as exc:
    refused = exc.status
recovered = recover_tool_call("{tool_call_id}", cfg=cfg, recovered_by="fresh-process")
print(json.dumps({{"before": before["status"], "refused": refused, "after": recovered["tool_call"]["status"]}}))
""",
    )
    assert json.loads(reader.stdout) == {
        "before": "EXECUTING",
        "refused": "ERROR_TOOL_CALL_IN_FLIGHT",
        "after": "INTERRUPTED",
    }
