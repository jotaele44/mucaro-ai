from backend.app.execution_gate import analyze_task, approve_execution


def test_execution_gate_planning_and_approval():
    gate = analyze_task(
        "EXECUTE_NEXT_VECTOR: PATCH_MUCARO_AI_VECTOR_WITH_EXECUTION_GATE → BUILD_LOCAL_RAG_PROTOTYPE",
        inputs_available=["execution_mode: local in-chat", "target_project: mucaro-ai"],
        approval_state="EXPLICIT_VECTOR",
    )
    assert gate["active_vector"] == "planning_gate_execution_control"
    assert gate["readiness_percent"] >= 75
    assert gate["approval_required"] is True
    approved = approve_execution(gate, gate["recommended_mode"])
    assert approved["state"] == "EXECUTING"
    assert approved["execution_trace_id"].startswith("trace_")
