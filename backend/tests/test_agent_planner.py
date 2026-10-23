from pathlib import Path

from backend.app.agent_planner import build_agent_plan
from backend.app.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        input_dir=tmp_path / "data" / "input",
        processed_dir=tmp_path / "data" / "processed",
        export_dir=tmp_path / "data" / "exports",
        index_dir=tmp_path / "data" / "indexes",
        sqlite_path=tmp_path / "data" / "processed" / "test.sqlite3",
        default_model_provider="mock",
        default_model_name="mock",
    )


def test_agent_planner_selects_only_explicit_bounded_intents(tmp_path: Path) -> None:
    plan = build_agent_plan(
        "Check source status, search the web for local-first RAG, and fetch https://example.com/docs",
        cfg=settings(tmp_path),
    )
    assert plan["status"] == "AGENT_PLAN_READY"
    assert plan["counts"]["selected_actions"] == 3
    assert [row["tool"] for row in plan["actions"]] == ["source_library.status", "web.fetch", "web.search"]
    assert all(plan["invariants"].values())


def test_agent_planner_fails_closed_for_unrecognized_intent(tmp_path: Path) -> None:
    plan = build_agent_plan("Please think deeply about this.", cfg=settings(tmp_path))
    assert plan["status"] == "AGENT_NO_SAFE_ACTIONS"
    assert plan["actions"] == []
    assert plan["invariants"]["unrecognized_intent_fails_closed"] is True
