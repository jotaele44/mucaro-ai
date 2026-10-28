from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .conversations import create_conversation
from .ingestion import ingest_file
from .model_routing_policy import plan_model_route
from .offline import build_offline_manifest
from .response_flow import get_pending_interaction, resolve_interaction
from .runtime_identity import current_git_head
from .tools import ToolDefinition, ToolRuntimeError, approve_tool_call, list_tool_calls
from .work_runs import (
    get_run,
    reconcile_runs_on_startup,
    resume_waiting_run,
    set_execution_mode,
    submit_message,
)

SCHEMA_VERSION = "MUCARO_MATRIX016_RECOVERY/1.0"
BENCHMARK_PATH = Path("_architecture/recovery_benchmark_v1.json")
REQUIRED_SCENARIOS = (
    "process_restart",
    "waiting_resume",
    "fifo_idempotency",
    "partial_failure_retry",
    "offline_transition",
)


class RecoveryBenchmarkError(RuntimeError):
    pass


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_benchmark(path: Path = BENCHMARK_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RecoveryBenchmarkError("ERROR_RECOVERY_SCHEMA")
    scenarios = payload.get("scenarios")
    thresholds = payload.get("thresholds")
    if not isinstance(scenarios, list) or not all(isinstance(item, dict) for item in scenarios):
        raise RecoveryBenchmarkError("ERROR_RECOVERY_SCENARIOS")
    ids = [item.get("id") for item in scenarios]
    if ids != list(REQUIRED_SCENARIOS):
        raise RecoveryBenchmarkError("ERROR_RECOVERY_SCENARIO_ORDER")
    if not isinstance(thresholds, dict) or set(thresholds) != {"min_pass_rate", "max_failed_scenarios"}:
        raise RecoveryBenchmarkError("ERROR_RECOVERY_THRESHOLDS")
    if not 0 < float(thresholds["min_pass_rate"]) <= 1 or int(thresholds["max_failed_scenarios"]) < 0:
        raise RecoveryBenchmarkError("ERROR_RECOVERY_THRESHOLDS")
    if not isinstance(payload.get("claim_boundary"), list) or not payload["claim_boundary"]:
        raise RecoveryBenchmarkError("ERROR_RECOVERY_CLAIM_BOUNDARY")
    return payload


def make_benchmark_settings(root: Path) -> Settings:
    data_dir = root / "data"
    return Settings(
        project_root=root,
        data_dir=data_dir,
        input_dir=data_dir / "input",
        processed_dir=data_dir / "processed",
        export_dir=data_dir / "exports",
        index_dir=data_dir / "indexes",
        sqlite_path=data_dir / "processed" / "recovery.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def _case(case_id: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = fn()
        return {"id": case_id, "status": "PASS", "checks": result}
    except Exception as exc:  # noqa: BLE001 - the receipt must close each case explicitly
        return {
            "id": case_id,
            "status": "FAIL",
            "checks": {},
            "error": {"type": type(exc).__name__, "detail": str(exc)[:500]},
        }


def run_benchmark(benchmark: dict[str, Any], cfg: Settings) -> dict[str, Any]:
    """Run a bounded, deterministic recovery benchmark in an isolated store.

    The process-restart case changes only the persisted state marker, which is the
    same evidence boundary used by the startup reconciler. It does not claim that
    a physical packaged application was killed and relaunched.
    """

    cfg.ensure_dirs()
    set_execution_mode("inline")
    source = cfg.input_dir / "recovery-fixture.md"
    source.write_text(
        "Recovery benchmark fixture: stable source lineage and idempotent work-run recovery are explicit.\n",
        encoding="utf-8",
    )
    ingest_file(source, cfg)

    def process_restart() -> dict[str, Any]:
        conversation_id = create_conversation("Recovery restart", cfg)["conversation_id"]
        run = submit_message(conversation_id, "/status", cfg=cfg)["run"]
        with sqlite3.connect(cfg.sqlite_path) as conn:
            conn.execute("UPDATE work_runs SET state = 'RUNNING' WHERE run_id = ?", (run["run_id"],))
        receipt = reconcile_runs_on_startup(cfg)
        recovered = get_run(run["run_id"], cfg)
        if receipt["interrupted"] != [run["run_id"]] or recovered["state"] != "INTERRUPTED":
            raise RecoveryBenchmarkError("ERROR_RECOVERY_RESTART_CONSERVATION")
        return {"run_id": run["run_id"], "state": recovered["state"], "outcome": recovered["outcome"]}

    def waiting_resume() -> dict[str, Any]:
        conversation_id = create_conversation("Recovery waiting", cfg)["conversation_id"]
        run = submit_message(conversation_id, "/status", cfg=cfg)["run"]
        before = get_run(run["run_id"], cfg)
        if before["state"] != "AWAITING_APPROVAL":
            raise RecoveryBenchmarkError("ERROR_RECOVERY_WAITING_STATE")
        reconciled = reconcile_runs_on_startup(cfg)
        call_id = before["tool_call_ids"][0]
        approve_tool_call(call_id, cfg=cfg)
        resume_waiting_run(run["run_id"], cfg)
        after = get_run(run["run_id"], cfg)
        if reconciled["interrupted"] or after["state"] != "COMPLETED":
            raise RecoveryBenchmarkError("ERROR_RECOVERY_WAITING_RESUME")
        return {"run_id": run["run_id"], "state_before": before["state"], "state_after": after["state"]}

    def fifo_idempotency() -> dict[str, Any]:
        conversation_id = create_conversation("Recovery queue", cfg)["conversation_id"]
        blocking = submit_message(conversation_id, "/status", cfg=cfg)["run"]
        first = submit_message(conversation_id, "How does source lineage work?", idempotency_key="recovery-first", cfg=cfg)["run"]
        second = submit_message(conversation_id, "What is a stable chunk id?", idempotency_key="recovery-second", cfg=cfg)["run"]
        duplicate = submit_message(conversation_id, "What is a stable chunk id?", idempotency_key="recovery-second", cfg=cfg)
        if duplicate["status"] != "RUN_DUPLICATE_RETURNED" or duplicate["run"]["run_id"] != second["run_id"]:
            raise RecoveryBenchmarkError("ERROR_RECOVERY_IDEMPOTENCY")
        approve_tool_call(blocking["tool_call_ids"][0], cfg=cfg)
        resume_waiting_run(blocking["run_id"], cfg)
        first_after = get_run(first["run_id"], cfg)
        second_after = get_run(second["run_id"], cfg)
        if first_after["state"] != "COMPLETED" or second_after["state"] != "COMPLETED":
            raise RecoveryBenchmarkError("ERROR_RECOVERY_FIFO_COMPLETION")
        if not first_after["started_at"] <= second_after["started_at"]:
            raise RecoveryBenchmarkError("ERROR_RECOVERY_FIFO_ORDER")
        return {
            "blocking_run_id": blocking["run_id"],
            "queued_run_ids": [first["run_id"], second["run_id"]],
            "duplicate_run_id": duplicate["run"]["run_id"],
            "fifo_order_preserved": True,
        }

    def partial_failure_retry() -> dict[str, Any]:
        calls = {"count": 0}

        def flaky(_arguments: dict[str, Any], _cfg: Settings) -> dict[str, Any]:
            calls["count"] += 1
            if calls["count"] == 1:
                raise ToolRuntimeError("ERROR_RECOVERY_TRANSIENT", "deterministic first-attempt failure")
            return {"status": "RECOVERY_RETRY_OK", "attempt": calls["count"]}

        from . import work_runs as work_runs_module

        old_definition = work_runs_module.TOOL_REGISTRY.get("test.recovery_flaky")
        old_command = work_runs_module.COMMANDS.get("/recovery-flaky")
        work_runs_module.TOOL_REGISTRY["test.recovery_flaky"] = ToolDefinition(
            "test.recovery_flaky", "deterministic recovery benchmark tool", False, True, flaky
        )
        work_runs_module.COMMANDS["/recovery-flaky"] = {
            "tool": "test.recovery_flaky", "usage": "/recovery-flaky", "arg": None
        }
        try:
            conversation_id = create_conversation("Recovery retry", cfg)["conversation_id"]
            run = submit_message(conversation_id, "/recovery-flaky", mode="auto", cfg=cfg)["run"]
            interaction = get_pending_interaction(conversation_id, cfg)
            if run["state"] != "AWAITING_INPUT" or interaction is None:
                raise RecoveryBenchmarkError("ERROR_RECOVERY_FAILURE_QUESTION")
            resolved = resolve_interaction(
                interaction["interaction_id"],
                expected_version=interaction["version"],
                resolved_by="benchmark",
                option_id="retry_failed",
                cfg=cfg,
            )
            resume_waiting_run(run["run_id"], cfg)
            after = get_run(run["run_id"], cfg)
            records = [call for call in list_tool_calls(cfg)["tool_calls"] if call.get("run_id") == run["run_id"]]
            if after["state"] != "COMPLETED" or [call["status"] for call in records] != ["FAILED", "EXECUTED"]:
                raise RecoveryBenchmarkError("ERROR_RECOVERY_RETRY_CONSERVATION")
            if records[1].get("retry_of") != records[0]["tool_call_id"] or calls["count"] != 2:
                raise RecoveryBenchmarkError("ERROR_RECOVERY_RETRY_DUPLICATION")
            return {
                "run_id": run["run_id"],
                "interaction_status": resolved["status"],
                "tool_statuses": [call["status"] for call in records],
                "retry_of": records[1]["retry_of"],
                "handler_attempts": calls["count"],
            }
        finally:
            if old_definition is None:
                work_runs_module.TOOL_REGISTRY.pop("test.recovery_flaky", None)
            else:
                work_runs_module.TOOL_REGISTRY["test.recovery_flaky"] = old_definition
            if old_command is None:
                work_runs_module.COMMANDS.pop("/recovery-flaky", None)
            else:
                work_runs_module.COMMANDS["/recovery-flaky"] = old_command

    def offline_transition() -> dict[str, Any]:
        manifest = build_offline_manifest(system_offline=True, cfg=cfg)
        remote = [row for row in manifest["capabilities"] if row["runtime_network_required"]]
        remote_with_dependencies = [row for row in remote if row["local_dependency_present"]]
        if not remote_with_dependencies or any(
            row["state_now"] != "UNAVAILABLE_OFFLINE" for row in remote_with_dependencies
        ):
            raise RecoveryBenchmarkError("ERROR_RECOVERY_OFFLINE_REMOTE_GATE")
        local_route = plan_model_route(task_type="cited_qa", require_offline=True, system_offline=True, cfg=cfg)
        live_route = plan_model_route(
            task_type="cited_qa",
            preferred_provider="anthropic",
            allow_live=True,
            require_live=True,
            system_offline=True,
            cfg=cfg,
        )
        if local_route["selected_provider"] != "mock" or live_route["selected_provider"] is not None:
            raise RecoveryBenchmarkError("ERROR_RECOVERY_OFFLINE_ROUTE")
        return {
            "remote_capability_count": len(remote_with_dependencies),
            "remote_states": sorted({row["state_now"] for row in remote_with_dependencies}),
            "offline_selected_provider": local_route["selected_provider"],
            "live_route_status": live_route["status"],
        }

    cases = [
        _case("process_restart", process_restart),
        _case("waiting_resume", waiting_resume),
        _case("fifo_idempotency", fifo_idempotency),
        _case("partial_failure_retry", partial_failure_retry),
        _case("offline_transition", offline_transition),
    ]
    passed = sum(case["status"] == "PASS" for case in cases)
    failed = len(cases) - passed
    return {
        "status": "RECOVERY_BENCH_OK" if failed == 0 else "RECOVERY_BENCH_FAIL",
        "scenario_count": len(cases),
        "passed": passed,
        "failed": failed,
        "arithmetic_closes": passed + failed == len(cases),
        "scenarios": cases,
    }


def certify(
    benchmark: dict[str, Any],
    report: dict[str, Any],
    *,
    head_sha: str,
) -> dict[str, Any]:
    failures: list[str] = []
    total = int(report.get("scenario_count") or 0)
    passed = int(report.get("passed") or 0)
    failed = int(report.get("failed") or 0)
    pass_rate = passed / total if total else 0.0
    if report.get("status") != "RECOVERY_BENCH_OK":
        failures.append("bench_status!=RECOVERY_BENCH_OK")
    if total != len(REQUIRED_SCENARIOS):
        failures.append("scenario_count!=required")
    if passed + failed != total or not report.get("arithmetic_closes"):
        failures.append("scenario_arithmetic_does_not_close")
    if pass_rate < float(benchmark["thresholds"]["min_pass_rate"]):
        failures.append("pass_rate<min_pass_rate")
    if failed > int(benchmark["thresholds"]["max_failed_scenarios"]):
        failures.append("failed_scenarios>max_failed_scenarios")
    if len(head_sha) != 40:
        failures.append("head_sha_invalid")
    receipt = {
        "schema_version": "mucaro-matrix016-recovery-receipt/1.0",
        "status": "MATRIX016_RECOVERY_BENCH_PASS" if not failures else "MATRIX016_RECOVERY_BENCH_FAIL",
        "benchmark_id": benchmark["benchmark_id"],
        "benchmark_sha256": stable_hash(benchmark),
        "head_sha": head_sha,
        "measurements": report,
        "thresholds": benchmark["thresholds"],
        "claim_boundary": benchmark["claim_boundary"],
        "threshold_failures": sorted(set(failures)),
    }
    receipt["receipt_sha256"] = stable_hash(receipt)
    return receipt


__all__ = [
    "BENCHMARK_PATH",
    "RecoveryBenchmarkError",
    "certify",
    "current_git_head",
    "load_benchmark",
    "make_benchmark_settings",
    "run_benchmark",
]
