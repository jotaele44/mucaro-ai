from backend.app.learning_eval import evaluate, execute
from backend.tests.test_api import make_settings


def test_held_out_effectiveness_is_reproducible_and_not_training_feedback(tmp_path):
    first = evaluate(make_settings(tmp_path))
    assert first == evaluate(make_settings(tmp_path))
    assert first["status"] == "PASS"
    assert first["measured"]["held_out_total"] == 12
    assert first["measured"]["held_out_passed"] == 12
    assert first["measured"]["reward_episodes"] == 24
    assert first["measured"]["held_out_feedback_unchanged"] is True
    assert first["inverted_reward_control"]["held_out_passed"] == 0
    assert first["effectiveness"]["difference_percentage_points"] == 50.0
    assert first["split_algebra"]["intersection"] == []
    assert first["split_algebra"]["union_count"] == 24


def test_correct_tie_adapter_preserves_all_candidates_and_unique_winner():
    assert execute("eval/ties", "preserve", [{"id": "x", "score": 2}, {"id": "y", "score": 1}]) == {
        "status": "BOUND", "candidates": ["x"]}
    assert execute("eval/ties", "preserve", [{"id": "x", "score": 2}, {"id": "y", "score": 2}]) == {
        "status": "UNRESOLVED", "candidates": ["x", "y"]}


def test_training_and_held_out_overlap_fails_closed(tmp_path, monkeypatch):
    import pytest
    from backend.app import learning_eval
    cases = learning_eval.fixtures()
    cases[12]["id"] = cases[0]["id"]
    monkeypatch.setattr(learning_eval, "fixtures", lambda: cases)
    with pytest.raises(ValueError, match="TRAIN_HELD_OUT_OVERLAP"):
        learning_eval.evaluate(make_settings(tmp_path))
