from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent_tasks import TaskError, create_task, get_task, list_tasks, task_summary, update_task
from backend.app.config import Settings


def make_settings(root: Path) -> Settings:
    return Settings(
        project_root=root,
        data_dir=root / "data",
        input_dir=root / "data" / "input",
        processed_dir=root / "data" / "processed",
        export_dir=root / "data" / "exports",
        index_dir=root / "data" / "indexes",
        sqlite_path=root / "data" / "processed" / "tasks.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_task_dag_persists_and_computes_readiness(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    foundation = create_task("Foundation", task_id="foundation", cfg=cfg)
    dependent = create_task("Dependent", task_id="dependent", depends_on=[foundation["task_id"]], cfg=cfg)

    assert get_task(dependent["task_id"], cfg)["status"] == "TODO"
    assert task_summary(cfg)["ready_task_ids"] == [foundation["task_id"]]
    assert [task["task_id"] for task in list_tasks(cfg)] == [foundation["task_id"], dependent["task_id"]]

    with pytest.raises(TaskError) as blocked:
        update_task(dependent["task_id"], status="IN_PROGRESS", cfg=cfg)
    assert blocked.value.status == "ERROR_TASK_DEPENDENCIES_NOT_READY"

    update_task(foundation["task_id"], status="IN_PROGRESS", cfg=cfg)
    finished = update_task(foundation["task_id"], status="DONE", cfg=cfg)
    assert finished["sequence"] == 2
    started = update_task(dependent["task_id"], status="IN_PROGRESS", cfg=cfg)
    assert started["status"] == "IN_PROGRESS"
    assert update_task(dependent["task_id"], status="DONE", cfg=cfg)["status"] == "DONE"


def test_task_graph_rejects_unknown_dependencies_and_cycles(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    with pytest.raises(TaskError) as unknown:
        create_task("Broken", task_id="broken", depends_on=["missing"], cfg=cfg)
    assert unknown.value.status == "ERROR_TASK_UNKNOWN_DEPENDENCY"

    create_task("A", task_id="a", cfg=cfg)
    create_task("B", task_id="b", depends_on=["a"], cfg=cfg)
    with pytest.raises(TaskError) as cycle:
        update_task("a", depends_on=["b"], cfg=cfg)
    assert cycle.value.status == "ERROR_TASK_DEPENDENCY_CYCLE"


def test_task_transitions_require_reason_and_fail_closed_at_terminal(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    task = create_task("Blocked work", task_id="blocked", cfg=cfg)
    with pytest.raises(TaskError) as reason:
        update_task(task["task_id"], status="BLOCKED", cfg=cfg)
    assert reason.value.status == "ERROR_TASK_BLOCKED_REASON_REQUIRED"

    blocked = update_task(task["task_id"], status="BLOCKED", blocked_reason="waiting for input", cfg=cfg)
    assert blocked["blocked_reason"] == "waiting for input"
    todo = update_task(task["task_id"], status="TODO", cfg=cfg)
    done = update_task(todo["task_id"], status="IN_PROGRESS", cfg=cfg)
    done = update_task(done["task_id"], status="DONE", cfg=cfg)
    with pytest.raises(TaskError) as terminal:
        update_task(done["task_id"], status="TODO", cfg=cfg)
    assert terminal.value.status == "ERROR_TASK_TRANSITION"


def test_task_sequence_guard_rejects_stale_writer(tmp_path: Path) -> None:
    cfg = make_settings(tmp_path)
    task = create_task("Concurrent", task_id="concurrent", cfg=cfg)
    update_task(task["task_id"], status="IN_PROGRESS", cfg=cfg)
    with pytest.raises(TaskError) as conflict:
        update_task(task["task_id"], status="BLOCKED", blocked_reason="stale", expected_sequence=0, cfg=cfg)
    assert conflict.value.status == "ERROR_TASK_CONFLICT"
