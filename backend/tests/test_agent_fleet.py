from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.app.agent_checkpoints import begin_steps, create_checkpoint, get_checkpoint
from backend.app.agent_fleet import FleetError, execute_fleet_plan, normalize_fleet_plan, resume_fleet_plan
from backend.app.config import Settings


SHA = "a" * 40


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


def test_fleet_plan_rejects_duplicate_identity() -> None:
    with pytest.raises(FleetError) as exc:
        normalize_fleet_plan(
            "Review the source",
            [
                {"id": "research", "role": "researcher", "prompt": "Find evidence."},
                {"id": "research", "role": "reviewer", "prompt": "Duplicate."},
            ],
        )
    assert exc.value.status == "ERROR_AGENT_FLEET_DUPLICATE"


def test_fleet_plan_batches_independent_agents_and_rejects_cycles() -> None:
    plan = normalize_fleet_plan(
        "Review the source",
        [
            {"id": "research", "role": "researcher", "prompt": "Find evidence."},
            {"id": "audit", "role": "reviewer", "prompt": "Check the evidence."},
            {"id": "synthesis", "role": "analyst", "prompt": "Summarize both.", "depends_on": ["research", "audit"]},
        ],
        max_parallel=2,
    )
    assert plan["batches"] == [["research", "audit"], ["synthesis"]]
    assert plan["plan_sha256"]

    with pytest.raises(FleetError) as exc:
        normalize_fleet_plan(
            "Cycle",
            [
                {"id": "a", "role": "analyst", "prompt": "A", "depends_on": ["b"]},
                {"id": "b", "role": "analyst", "prompt": "B", "depends_on": ["a"]},
            ],
        )
    assert exc.value.status == "ERROR_AGENT_FLEET_CYCLE"


def test_fleet_executes_parallel_batches_and_passes_dependency_reports(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    plan = normalize_fleet_plan(
        "Review the source",
        [
            {"id": "research", "role": "researcher", "prompt": "Find evidence."},
            {"id": "audit", "role": "reviewer", "prompt": "Check the evidence."},
            {"id": "synthesis", "role": "analyst", "prompt": "Summarize both.", "depends_on": ["research", "audit"]},
        ],
        max_parallel=2,
    )
    lock = threading.Lock()
    seen: list[tuple[str, dict]] = []

    def executor(agent: dict, context: dict) -> dict:
        with lock:
            seen.append((agent["id"], context))
        return {"status": "PASS", "answer": f"report:{agent['id']}"}

    result = execute_fleet_plan(plan, session_id="session", run_id="run", source_sha=SHA, executor=executor, cfg=cfg)
    assert result["status"] == "FLEET_COMPLETED"
    assert set(result["agents"]) == {"research", "audit", "synthesis"}
    synthesis_context = next(context for agent_id, context in seen if agent_id == "synthesis")
    assert set(synthesis_context["dependencies"]) == {"research", "audit"}
    checkpoint = get_checkpoint(result["checkpoint_id"], cfg)
    assert checkpoint is not None and checkpoint["state"] == "COMPLETED"
    assert checkpoint["in_flight_steps"] == []


def test_fleet_failure_closes_unexecuted_dependents(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    plan = normalize_fleet_plan(
        "Review the source",
        [
            {"id": "research", "role": "researcher", "prompt": "Fail."},
            {"id": "synthesis", "role": "analyst", "prompt": "Use research.", "depends_on": ["research"]},
        ],
    )

    def executor(agent: dict, context: dict) -> dict:
        if agent["id"] == "research":
            raise FleetError("ERROR_TEST_SUBAGENT", "evidence unavailable")
        return {"status": "PASS", "answer": "unexpected"}

    result = execute_fleet_plan(plan, session_id="session", run_id="run", source_sha=SHA, executor=executor, cfg=cfg)
    assert result["status"] == "FLEET_FAILED"
    assert result["agents"]["research"]["status"] == "ERROR_TEST_SUBAGENT"
    assert result["agents"]["synthesis"]["executed"] is False
    assert get_checkpoint(result["checkpoint_id"], cfg)["state"] == "FAILED"


def test_fleet_resume_reclaims_in_flight_batch_after_crash(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    plan = normalize_fleet_plan("Resume work", [{"id": "research", "role": "researcher", "prompt": "Continue."}])
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective=plan["objective"],
        source_sha=SHA,
        state="EXECUTING",
        pending_steps=[{"id": "research", "status": "PENDING"}],
        metadata={"workflow": "agent_fleet", "fleet_id": plan["fleet_id"], "plan_sha256": plan["plan_sha256"], "options": {}},
        cfg=cfg,
    )
    begin_steps(checkpoint["checkpoint_id"], steps=[{"step_id": "research", "idempotency_key": f"{plan['fleet_id']}:research"}], cfg=cfg)

    result = resume_fleet_plan(
        checkpoint["checkpoint_id"],
        plan,
        executor=lambda agent, context: {"status": "PASS", "answer": "recovered"},
        cfg=cfg,
    )
    assert result["status"] == "FLEET_COMPLETED"
    assert get_checkpoint(checkpoint["checkpoint_id"], cfg)["state"] == "COMPLETED"


def test_fleet_resume_rejects_plan_identity_drift(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    plan = normalize_fleet_plan("Original", [{"id": "a", "role": "analyst", "prompt": "A"}])
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective=plan["objective"],
        source_sha=SHA,
        state="EXECUTING",
        metadata={"workflow": "agent_fleet", "fleet_id": plan["fleet_id"], "plan_sha256": plan["plan_sha256"], "options": {}},
        cfg=cfg,
    )
    changed = normalize_fleet_plan("Changed", [{"id": "a", "role": "analyst", "prompt": "A"}])
    with pytest.raises(FleetError) as exc:
        resume_fleet_plan(checkpoint["checkpoint_id"], changed, cfg=cfg)
    assert exc.value.status == "ERROR_AGENT_FLEET_PLAN_MISMATCH"
