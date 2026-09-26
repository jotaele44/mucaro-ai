from scripts import shadow_qa


def test_adjacent_paths_expands_backend_frontend_schema_and_workflow_surfaces():
    paths = [
        "backend/app/response_flow.py",
        "frontend/src/App.jsx",
        "schemas/answer_trace.schema.json",
        ".github/workflows/ci.yml",
    ]

    adjacent = shadow_qa.adjacent_paths(paths)

    assert "backend/tests" in adjacent
    assert "backend/app/main.py" in adjacent
    assert "backend/app/cli.py" in adjacent
    assert "frontend/src" in adjacent
    assert "scripts" in adjacent
    assert "docs" in adjacent


def test_rendered_frontend_changes_fail_closed_without_browser_evidence():
    state, reason = shadow_qa.classify_rendered(["frontend/src/App.jsx"])

    assert state == "BLOCKED"
    assert "browser/device" in reason


def test_non_frontend_checkpoint_does_not_invent_rendered_pass():
    state, reason = shadow_qa.classify_rendered(["backend/app/response_flow.py"])

    assert state == "AUDIT_ONLY"
    assert "No frontend surface changed" in reason
