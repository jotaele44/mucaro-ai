from backend.app.intelligence_plane import build_intelligence_plane_descriptor


def test_intelligence_plane_descriptor_is_provisional_and_non_required():
    descriptor = build_intelligence_plane_descriptor()

    assert descriptor["schema"] == "repo-capability-contract/1.0"
    assert descriptor["repo_id"] == "mucaro-ai"
    assert descriptor["membership_status"] == "PROVISIONAL"
    assert descriptor["roles"] == ["INTELLIGENCE_PLANE_CANDIDATE"]
    assert descriptor["baseline_required"] is False
    assert descriptor["lockstep_participant"] is True


def test_intelligence_plane_capability_states_do_not_overclaim():
    descriptor = build_intelligence_plane_descriptor()
    capabilities = descriptor["capabilities"]

    assert capabilities["skill_registry"]["state"] == "CANDIDATE"
    assert capabilities["skill_mount_runtime"]["state"] == "CANDIDATE"
    assert capabilities["execution_router"]["state"] == "ABSENT"
    assert capabilities["cross_repo_orchestration"]["state"] == "ABSENT"
    assert capabilities["model_runtime"]["state"] == "PARTIAL"
