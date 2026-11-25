from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.agent_checkpoints import (
    CheckpointError,
    begin_step,
    create_checkpoint,
    get_task_dag_state,
    install_task_dag_state,
    transition_checkpoint,
    transition_task_dag_state,
)
from backend.app.agent_task_dag import (
    TaskDagError,
    build_task_dag,
    transition_task_dag,
    validate_task_dag,
)
from backend.app.config import Settings

SHA = "a" * 40
NOW = "2026-10-02T18:00:00Z"


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


def definitions() -> list[dict]:
    return [
        {"id": "test", "title": "Run tests", "depends_on": ["edit"]},
        {"id": "inspect", "title": "Inspect repository"},
        {"id": "edit", "title": "Apply bounded fix", "depends_on": ["inspect"]},
    ]


def test_task_dag_build_is_deterministic_and_arithmetic_closes() -> None:
    first = build_task_dag(definitions(), created_at=NOW)
    second = build_task_dag(list(reversed(definitions())), created_at=NOW)

    assert first == second
    assert first["topological_order"] == ["inspect", "edit", "test"]
    assert first["ready_task_ids"] == ["inspect"]
    assert first["task_count"] == 3
    assert first["edge_count"] == 2
    assert sum(first["state_counts"].values()) == first["task_count"]
    validate_task_dag(first)


@pytest.mark.parametrize(
    ("tasks", "status"),
    [
        (
            [
                {"id": "a", "title": "A", "depends_on": ["missing"]},
            ],
            "ERROR_TASK_DAG_MISSING_DEPENDENCY",
        ),
        (
            [
                {"id": "a", "title": "A", "depends_on": ["b"]},
                {"id": "b", "title": "B", "depends_on": ["a"]},
            ],
            "ERROR_TASK_DAG_CYCLE",
        ),
        (
            [
                {"id": "a", "title": "A"},
                {"id": "a", "title": "Duplicate"},
            ],
            "ERROR_TASK_DAG_DUPLICATE_ID",
        ),
        (
            [
                {"id": "a", "title": "A", "depends_on": ["a"]},
            ],
            "ERROR_TASK_DAG_SELF_DEPENDENCY",
        ),
    ],
)
def test_task_dag_rejects_invalid_graphs(tasks: list[dict], status: str) -> None:
    with pytest.raises(TaskDagError) as exc:
        build_task_dag(tasks, created_at=NOW)
    assert exc.value.status == status


def test_pass_requires_running_state_and_evidence_then_unlocks_dependency() -> None:
    dag = build_task_dag(definitions(), created_at=NOW)

    with pytest.raises(TaskDagError) as not_running:
        transition_task_dag(dag, task_id="inspect", state="PASSED", evidence=[{"result": "ok"}], now=NOW)
    assert not_running.value.status == "ERROR_TASK_DAG_TRANSITION"

    running = transition_task_dag(dag, task_id="inspect", state="RUNNING", now=NOW)
    with pytest.raises(TaskDagError) as no_evidence:
        transition_task_dag(running, task_id="inspect", state="PASSED", now=NOW)
    assert no_evidence.value.status == "ERROR_TASK_DAG_TERMINAL_EVIDENCE"

    passed = transition_task_dag(
        running,
        task_id="inspect",
        state="PASSED",
        evidence=[{"status": "INSPECTION_OK", "sha256": "b" * 64}],
        now=NOW,
    )
    by_id = {task["id"]: task for task in passed["tasks"]}
    assert by_id["inspect"]["state"] == "PASSED"
    assert by_id["edit"]["state"] == "READY"
    assert by_id["test"]["state"] == "PENDING"
    assert passed["ready_task_ids"] == ["edit"]
    assert by_id["inspect"]["attempt_count"] == 1


def test_failure_blocks_downstream_tasks_without_synthesizing_pass() -> None:
    dag = build_task_dag(definitions(), created_at=NOW)
    running = transition_task_dag(dag, task_id="inspect", state="RUNNING", now=NOW)
    failed = transition_task_dag(
        running,
        task_id="inspect",
        state="FAILED",
        evidence=[{"status": "ERROR_INSPECTION"}],
        now=NOW,
    )
    by_id = {task["id"]: task for task in failed["tasks"]}
    assert by_id["inspect"]["state"] == "FAILED"
    assert by_id["edit"]["state"] == "BLOCKED"
    assert by_id["test"]["state"] == "BLOCKED"
    assert by_id["edit"]["blocked_reason"] == "dependency_not_passed:inspect"
    assert by_id["test"]["blocked_reason"] == "dependency_not_passed:edit"
    assert failed["ready_task_ids"] == []
    assert failed["state_counts"]["PASSED"] == 0


def test_definition_hash_detects_stored_definition_rewrite() -> None:
    dag = build_task_dag(definitions(), created_at=NOW)
    forged = deepcopy(dag)
    forged["tasks"][0]["title"] = "Rewritten title"

    with pytest.raises(TaskDagError) as exc:
        validate_task_dag(forged)
    assert exc.value.status == "ERROR_TASK_DAG_DEFINITION_DRIFT"


def test_checkpoint_owns_task_dag_state_and_closes_only_after_every_task_passes(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective="Audit and fix",
        source_sha=SHA,
        state="PLANNING",
        cfg=cfg,
    )
    dag = build_task_dag(
        [
            {"id": "inspect", "title": "Inspect"},
            {"id": "fix", "title": "Fix", "depends_on": ["inspect"]},
        ],
        created_at=NOW,
    )
    installed = install_task_dag_state(
        checkpoint["checkpoint_id"],
        dag=dag,
        expected_sequence=checkpoint["sequence"],
        cfg=cfg,
    )
    assert installed["sequence"] == 1
    assert [row["id"] for row in installed["pending_steps"]] == ["inspect", "fix"]
    assert installed["completed_steps"] == []

    executing = transition_checkpoint(
        checkpoint["checkpoint_id"],
        state="EXECUTING",
        expected_sequence=installed["sequence"],
        cfg=cfg,
    )

    with pytest.raises(CheckpointError) as legacy:
        begin_step(checkpoint["checkpoint_id"], step_id="inspect", idempotency_key="legacy", cfg=cfg)
    assert legacy.value.status == "ERROR_CHECKPOINT_TASK_DAG_LEGACY_STEP_API"

    with pytest.raises(CheckpointError) as rewrite:
        transition_checkpoint(
            checkpoint["checkpoint_id"],
            state="EXECUTING",
            pending_steps=[],
            cfg=cfg,
        )
    assert rewrite.value.status == "ERROR_CHECKPOINT_TASK_DAG_DIRECT_REWRITE"

    inspect_running = transition_task_dag_state(
        checkpoint["checkpoint_id"],
        task_id="inspect",
        state="RUNNING",
        expected_sequence=executing["sequence"],
        cfg=cfg,
    )
    inspect_passed = transition_task_dag_state(
        checkpoint["checkpoint_id"],
        task_id="inspect",
        state="PASSED",
        evidence=[{"status": "INSPECTION_OK"}],
        expected_sequence=inspect_running["sequence"],
        cfg=cfg,
    )
    current = get_task_dag_state(checkpoint["checkpoint_id"], cfg)
    assert current["ready_task_ids"] == ["fix"]

    with pytest.raises(CheckpointError) as incomplete:
        transition_checkpoint(checkpoint["checkpoint_id"], state="COMPLETED", cfg=cfg)
    assert incomplete.value.status == "ERROR_CHECKPOINT_TASK_DAG_INCOMPLETE"

    fix_running = transition_task_dag_state(
        checkpoint["checkpoint_id"],
        task_id="fix",
        state="RUNNING",
        expected_sequence=inspect_passed["sequence"],
        cfg=cfg,
    )
    fix_passed = transition_task_dag_state(
        checkpoint["checkpoint_id"],
        task_id="fix",
        state="PASSED",
        evidence=[{"status": "FIX_VERIFIED"}],
        expected_sequence=fix_running["sequence"],
        cfg=cfg,
    )
    final_dag = get_task_dag_state(checkpoint["checkpoint_id"], cfg)
    assert final_dag["state_counts"]["PASSED"] == 2
    assert final_dag["ready_task_ids"] == []

    done = transition_checkpoint(
        checkpoint["checkpoint_id"],
        state="COMPLETED",
        expected_sequence=fix_passed["sequence"],
        cfg=cfg,
    )
    assert done["state"] == "COMPLETED"
    assert len(done["completed_steps"]) == 2
    assert done["pending_steps"] == []


def test_checkpoint_task_transition_rejects_stale_sequence(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective="Audit",
        source_sha=SHA,
        state="PLANNING",
        cfg=cfg,
    )
    dag = build_task_dag([{"id": "inspect", "title": "Inspect"}], created_at=NOW)
    installed = install_task_dag_state(
        checkpoint["checkpoint_id"],
        dag=dag,
        expected_sequence=0,
        cfg=cfg,
    )
    executing = transition_checkpoint(
        checkpoint["checkpoint_id"],
        state="EXECUTING",
        expected_sequence=installed["sequence"],
        cfg=cfg,
    )

    with pytest.raises(CheckpointError) as stale:
        transition_task_dag_state(
            checkpoint["checkpoint_id"],
            task_id="inspect",
            state="RUNNING",
            expected_sequence=installed["sequence"],
            cfg=cfg,
        )
    assert stale.value.status == "ERROR_CHECKPOINT_CONFLICT"
    assert get_task_dag_state(checkpoint["checkpoint_id"], cfg)["ready_task_ids"] == ["inspect"]
    assert executing["sequence"] == 2


def test_checkpoint_rejects_progressed_dag_as_initial_state(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective="Audit",
        source_sha=SHA,
        state="PLANNING",
        cfg=cfg,
    )
    dag = build_task_dag([{"id": "inspect", "title": "Inspect"}], created_at=NOW)
    progressed = transition_task_dag(dag, task_id="inspect", state="RUNNING", now=NOW)

    with pytest.raises(CheckpointError) as exc:
        install_task_dag_state(
            checkpoint["checkpoint_id"],
            dag=progressed,
            expected_sequence=checkpoint["sequence"],
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_CHECKPOINT_TASK_DAG_NOT_INITIAL"


def test_paused_checkpoint_cannot_advance_task_dag(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    checkpoint = create_checkpoint(
        session_id="session",
        run_id="run",
        objective="Audit",
        source_sha=SHA,
        state="PLANNING",
        cfg=cfg,
    )
    dag = build_task_dag([{"id": "inspect", "title": "Inspect"}], created_at=NOW)
    installed = install_task_dag_state(
        checkpoint["checkpoint_id"],
        dag=dag,
        expected_sequence=0,
        cfg=cfg,
    )
    executing = transition_checkpoint(
        checkpoint["checkpoint_id"],
        state="EXECUTING",
        expected_sequence=installed["sequence"],
        cfg=cfg,
    )
    paused = transition_checkpoint(
        checkpoint["checkpoint_id"],
        state="PAUSED_PROVIDER",
        pause_reason="provider unavailable",
        expected_sequence=executing["sequence"],
        cfg=cfg,
    )

    with pytest.raises(CheckpointError) as exc:
        transition_task_dag_state(
            checkpoint["checkpoint_id"],
            task_id="inspect",
            state="RUNNING",
            expected_sequence=paused["sequence"],
            cfg=cfg,
        )
    assert exc.value.status == "ERROR_CHECKPOINT_TASK_DAG_NOT_EXECUTING"
    assert get_task_dag_state(checkpoint["checkpoint_id"], cfg)["ready_task_ids"] == ["inspect"]
