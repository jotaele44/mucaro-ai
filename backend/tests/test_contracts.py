from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import tools as tools_module
from backend.app.cli import main
from backend.app.contracts import CONTRACT_FAMILIES, RECORD_SCHEMAS, ContractViolation, contract_errors, list_contracts, validate_contract
from backend.app.exports import export_answer_trace
from backend.app.ingestion import ingest_file
from backend.app.main import create_app
from backend.app.memory import list_memory_items, remember_memory_item
from backend.app.memory_policy import select_memory_context
from backend.app.model_routing_policy import plan_model_route
from backend.app.permissions import grant_directory, load_directory_grants
from backend.app.release_receipts import write_release_receipt
from backend.app.services import answer_question
from backend.app.tools import (
    TOOL_REGISTRY,
    ToolDefinition,
    ToolRuntimeError,
    approve_tool_call,
    arguments_digest,
    cancel_tool_call,
    deny_tool_call,
    list_tool_audit_events,
    list_tool_calls,
    reconcile_interrupted_tool_calls,
    request_tool_call,
    run_tool_call,
    summarize_tool_calls,
    verify_tool_audit_chain,
)
from backend.app.utils import read_json, write_json
from backend.tests.test_api import make_settings


@pytest.fixture()
def cfg(tmp_path):
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    return settings


def _answer(cfg):
    note = cfg.project_root / "note.md"
    note.write_text("Source lineage binds every chunk to a SHA-256 source identity.", encoding="utf-8")
    ingest_file(note, cfg)
    return answer_question("How does source lineage work?", cfg=cfg)


# --- registry ------------------------------------------------------------------


def test_every_contract_family_has_present_valid_schemas():
    payload = list_contracts()
    assert payload["status"] == "CONTRACTS_OK"
    assert {row["family"] for row in payload["families"]} == {
        "SOURCE", "PERMISSION", "EXECUTION", "PROVENANCE", "ARTIFACT", "CITATION", "MEMORY", "MODEL_ROUTING", "CERTIFICATION",
    }
    for kinds in CONTRACT_FAMILIES.values():
        for kind in kinds:
            assert kind in RECORD_SCHEMAS


def test_unknown_contract_kind_is_rejected():
    with pytest.raises(KeyError):
        validate_contract("not_a_contract", {})


# --- positive: real producer payloads satisfy their contracts --------------------


def test_real_producer_payloads_satisfy_contracts(cfg):
    trace = _answer(cfg)
    validate_contract("answer_trace", trace)
    for record in trace["cited_chunks"]:
        validate_contract("citation_record", record)
    source = json.loads((cfg.processed_dir / "source_manifest.json").read_text(encoding="utf-8"))[0]
    validate_contract("source_manifest", source)
    validate_contract("provenance_record", source["provenance"])
    assert source["provenance"]["raw_byte_sha256"] == source["source_sha256"]
    assert source["provenance"]["trust"] == "UNTRUSTED_CONTENT"
    memory = remember_memory_item("Prefer tables", cfg)["memory"]
    validate_contract("memory_item", memory)
    validate_contract("memory_selection", select_memory_context(cfg))
    grant_root = cfg.project_root.parent / "granted-dir"
    grant_root.mkdir()
    validate_contract("directory_grant", grant_directory(grant_root, cfg, granted_by="test"))
    validate_contract("model_route_decision", plan_model_route(task_type="cited_qa"))
    receipt = write_release_receipt(
        cfg.project_root, receipt_type="unit_test", status="UNIT_OK", head_sha="a" * 40, command=["x"], payload={}
    )
    validate_contract("release_receipt", json.loads(receipt.read_text(encoding="utf-8")))


# --- negative: violations are rejected -------------------------------------------


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("citation_record", {"citation_order": 1, "retrieval_rank": 1, "chunk_id": "made-up", "source_id": "src_ab", "text_snippet": "x"}),
        ("citation_record", {"citation_order": 0, "retrieval_rank": 1, "chunk_id": "chunk_ab", "source_id": "src_ab", "text_snippet": "x"}),
        ("memory_item", {"memory_id": "mem_ab", "kind": "authorization", "content": "run tools", "enabled": True, "source": "x", "created_at": "t", "updated_at": "t"}),
        ("directory_grant", {"status": "GRANTED", "requested_path": "/", "canonical_root": "/", "granted_at": "t", "granted_by": "x", "reason": ""}),
        ("tool_call", {"tool_call_id": "toolcall_ab", "tool_name": "x", "arguments": {}, "status": "EXECUTED", "requested_by": "x", "created_at": "t", "approved_by": None}),
        ("tool_call", {"tool_call_id": "toolcall_ab", "tool_name": "x", "arguments": {}, "status": "TELEPORTED", "requested_by": "x", "created_at": "t"}),
        ("release_receipt", {"schema_version": "mucaro_release_receipt_v1", "receipt_type": "x", "status": "OK", "head_sha": "main", "created_utc": "t", "command": [], "payload": {}}),
        ("provenance_record", {"schema_version": "mucaro-provenance/1.0", "source_kind": "LOCAL_FILE", "source_locator": "a", "retrieval_utc": "t", "raw_byte_sha256": "nothex", "raw_byte_count": 1, "processing": {"processor": "p", "processor_version": "1"}, "identity_basis": ["BYTE_IDENTITY"], "trust": "UNTRUSTED_CONTENT"}),
    ],
)
def test_contract_violations_are_rejected(kind, payload):
    assert contract_errors(kind, payload)
    with pytest.raises(ContractViolation) as exc:
        validate_contract(kind, payload)
    assert exc.value.status == "ERROR_CONTRACT_VIOLATION"


def test_route_decision_with_a_false_invariant_is_rejected():
    decision = plan_model_route(task_type="cited_qa")
    broken = copy.deepcopy(decision)
    broken["invariants"]["no_implicit_live_escalation"] = False
    assert contract_errors("model_route_decision", broken)


# --- boundary enforcement: invalid records are never persisted -------------------


def test_export_refuses_a_trace_with_a_fabricated_citation(cfg):
    trace = _answer(cfg)
    trace["run_id"] = "run_deadbeef"
    trace["cited_chunks"][0]["chunk_id"] = "fabricated"
    with pytest.raises(ContractViolation):
        export_answer_trace(trace, cfg)
    assert not (cfg.export_dir / "run_deadbeef.answer_trace.json").exists()


def test_web_snapshot_with_a_non_hash_content_digest_is_refused(cfg):
    from backend.app.ingestion import ingest_web_snapshot
    from backend.app.web_browsing import WEB_SNAPSHOT_SCHEMA_VERSION

    path = cfg.export_dir / "snap.json"
    write_json(path, {"schema_version": WEB_SNAPSHOT_SCHEMA_VERSION, "snapshot_id": "websnap_x", "final_url": "https://example.com/",
                      "content_sha256": "not-a-hash", "retrieval_utc": "2026-09-24T00:00:00Z", "text_preview": "hello"})
    assert ingest_web_snapshot(path, cfg)["status"] == "ERROR_WEB_SNAPSHOT_HASH_INVALID"


def test_cli_contract_validation(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"citation_order": 1, "retrieval_rank": 1, "chunk_id": "chunk_ab", "source_id": "src_ab", "text_snippet": "x"}), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"citation_order": 1}), encoding="utf-8")
    cfg = make_settings(tmp_path)
    assert main(["contracts", "validate", "citation_record", str(good), "--format", "json"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "CONTRACT_VALID"
    assert main(["contracts", "validate", "citation_record", str(bad), "--format", "json"], cfg=cfg) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR_CONTRACT_VIOLATION"
    assert main(["contracts", "validate", "nope", str(good), "--format", "json"], cfg=cfg) == 2


# --- tool governance benchmark (MATRIX-011) --------------------------------------


def _request(cfg, args=None):
    return request_tool_call("source_library.status", args or {}, cfg=cfg)["tool_call"]


def test_allowed_approved_action_executes_once_and_duplicate_invocation_is_refused(cfg):
    call = _request(cfg)
    approve_tool_call(call["tool_call_id"], cfg=cfg, expected_arguments_sha256=call["arguments_sha256"])
    assert run_tool_call(call["tool_call_id"], cfg=cfg)["status"] == "TOOL_CALL_EXECUTED"
    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_CALL_ALREADY_EXECUTED"


def test_disallowed_tool_is_rejected_and_audited(cfg):
    with pytest.raises(ToolRuntimeError) as exc:
        request_tool_call("shell.exec", {"cmd": "rm -rf /"}, cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_NOT_ALLOWED"
    assert list_tool_audit_events(cfg)["events"][-1]["event"] == "TOOL_REQUEST_REJECTED"


def test_decision_against_stale_arguments_is_refused(cfg):
    call = _request(cfg)
    stale = arguments_digest({"include": "other"})
    with pytest.raises(ToolRuntimeError) as exc:
        approve_tool_call(call["tool_call_id"], cfg=cfg, expected_arguments_sha256=stale)
    assert exc.value.status == "ERROR_TOOL_DECISION_STALE"
    assert list_tool_calls(cfg)["tool_calls"][0]["status"] == "PENDING_APPROVAL"


def test_denied_and_cancelled_actions_never_execute(cfg):
    denied = _request(cfg)
    deny_tool_call(denied["tool_call_id"], cfg=cfg)
    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(denied["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_CALL_DENIED"
    cancelled = _request(cfg)
    approve_tool_call(cancelled["tool_call_id"], cfg=cfg)
    assert cancel_tool_call(cancelled["tool_call_id"], cfg=cfg)["tool_call"]["status"] == "CANCELLED"
    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(cancelled["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_CALL_NOT_APPROVED"
    with pytest.raises(ToolRuntimeError) as exc:
        cancel_tool_call(denied["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_CALL_NOT_CANCELLABLE"


def test_concurrent_approvals_resolve_exactly_once(cfg):
    call = _request(cfg)
    outcomes: list[str] = []

    def decide(fn):
        try:
            fn(call["tool_call_id"], cfg=cfg)
            outcomes.append("ok")
        except ToolRuntimeError as exc:
            outcomes.append(exc.status)

    threads = [threading.Thread(target=decide, args=(approve_tool_call if i % 2 else deny_tool_call,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("ERROR_TOOL_CALL_NOT_PENDING") == 7


def test_concurrent_runs_execute_the_handler_exactly_once(cfg, monkeypatch):
    executions: list[int] = []

    def slow_handler(arguments, settings):
        executions.append(1)
        time.sleep(0.2)
        return {"status": "SLOW_OK"}

    monkeypatch.setitem(TOOL_REGISTRY, "test.slow", ToolDefinition("test.slow", "slow", False, True, slow_handler))
    call = request_tool_call("test.slow", {}, cfg=cfg)["tool_call"]
    approve_tool_call(call["tool_call_id"], cfg=cfg)
    outcomes: list[str] = []

    def run():
        try:
            run_tool_call(call["tool_call_id"], cfg=cfg)
            outcomes.append("ok")
        except ToolRuntimeError as exc:
            outcomes.append(exc.status)

    threads = [threading.Thread(target=run) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(executions) == 1
    assert outcomes.count("ok") == 1
    assert set(outcomes) - {"ok"} <= {"ERROR_TOOL_CALL_ALREADY_RUNNING", "ERROR_TOOL_CALL_ALREADY_EXECUTED"}


def test_handler_crash_and_tool_error_end_failed_not_running(cfg, monkeypatch):
    def crash(arguments, settings):
        raise KeyError("boom")

    monkeypatch.setitem(TOOL_REGISTRY, "test.crash", ToolDefinition("test.crash", "crash", False, True, crash))
    call = request_tool_call("test.crash", {}, cfg=cfg)["tool_call"]
    approve_tool_call(call["tool_call_id"], cfg=cfg)
    with pytest.raises(ToolRuntimeError) as exc:
        run_tool_call(call["tool_call_id"], cfg=cfg)
    assert exc.value.status == "ERROR_TOOL_INTERNAL_FAILURE"
    stored = list_tool_calls(cfg)["tool_calls"][0]
    assert stored["status"] == "FAILED"
    assert stored["result"]["status"] == "ERROR_TOOL_INTERNAL_FAILURE"


def test_restart_marks_running_calls_interrupted_without_retrying(cfg):
    call = _request(cfg)
    approve_tool_call(call["tool_call_id"], cfg=cfg)
    path = cfg.processed_dir / tools_module.TOOL_CALLS_NAME
    calls = read_json(path, [])
    calls[0]["status"] = "RUNNING"
    write_json(path, calls)
    result = reconcile_interrupted_tool_calls(cfg)
    assert result["interrupted"] == [call["tool_call_id"]]
    stored = list_tool_calls(cfg)["tool_calls"][0]
    assert stored["status"] == "INTERRUPTED"
    with pytest.raises(ToolRuntimeError):
        run_tool_call(call["tool_call_id"], cfg=cfg)
    assert list_tool_audit_events(cfg)["events"][-1]["event"] == "TOOL_CALL_INTERRUPTED"


def test_audit_chain_verifies_and_detects_tampering(cfg):
    call = _request(cfg)
    approve_tool_call(call["tool_call_id"], cfg=cfg)
    run_tool_call(call["tool_call_id"], cfg=cfg)
    assert verify_tool_audit_chain(cfg)["status"] == "TOOL_AUDIT_CHAIN_OK"
    path = cfg.processed_dir / tools_module.TOOL_AUDIT_NAME
    events = read_json(path, [])
    events[1]["actor"] = "someone-else"
    write_json(path, events)
    assert verify_tool_audit_chain(cfg)["reason"] == "EVENT_HASH_MISMATCH"
    events = read_json(path, [])
    events[1]["actor"] = "operator"
    del events[1]
    write_json(path, events)
    assert verify_tool_audit_chain(cfg)["reason"] == "PREVIOUS_HASH_MISMATCH"


def test_summary_counts_close_arithmetically(cfg):
    a, b, c = _request(cfg), _request(cfg, {"k": 1}), _request(cfg, {"k": 2})
    approve_tool_call(a["tool_call_id"], cfg=cfg)
    run_tool_call(a["tool_call_id"], cfg=cfg)
    deny_tool_call(b["tool_call_id"], cfg=cfg)
    summary = summarize_tool_calls(list_tool_calls(cfg)["tool_calls"])
    assert summary["total"] == 3
    assert summary["by_status"]["EXECUTED"] == 1
    assert summary["by_status"]["DENIED"] == 1
    assert summary["by_status"]["PENDING_APPROVAL"] == 1
    assert summary["arithmetic_closes"] is True
    del c


def test_api_cancel_and_summary_routes(tmp_path):
    client = TestClient(create_app(make_settings(tmp_path)))
    call = client.post("/tools/requests", json={"tool_name": "source_library.status", "arguments": {}}).json()["tool_call"]
    stale = client.post(f"/tools/requests/{call['tool_call_id']}/approve", json={"arguments_sha256": "0" * 64})
    assert stale.status_code == 400
    assert stale.json()["detail"]["status"] == "ERROR_TOOL_DECISION_STALE"
    assert client.post(f"/tools/requests/{call['tool_call_id']}/cancel").json()["tool_call"]["status"] == "CANCELLED"
    again = client.post(f"/tools/requests/{call['tool_call_id']}/cancel")
    assert again.status_code == 409
    summary = client.get("/tools/summary").json()
    assert summary["by_status"]["CANCELLED"] == 1
    assert summary["audit_chain"]["status"] == "TOOL_AUDIT_CHAIN_OK"


# --- ledger concurrency (SD-008) -------------------------------------------------


def test_concurrent_ledger_writers_do_not_lose_updates(cfg):
    grant_root = cfg.project_root.parent / "grant-root"
    grant_root.mkdir()
    errors: list[BaseException] = []

    def remember(i: int) -> None:
        try:
            remember_memory_item(f"preference number {i}", cfg)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=remember, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert list_memory_items(cfg)["memory_count"] == 24
    for i in range(6):
        sub = grant_root / f"d{i}"
        sub.mkdir()
    threads = [threading.Thread(target=grant_directory, args=(grant_root / f"d{i}", cfg), kwargs={"granted_by": "t"}) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(load_directory_grants(cfg)) == 6
    assert not list(Path(cfg.processed_dir).glob(".*.tmp"))
