from backend.app.self_build_controller import MutationBudget, classify_failure, decide_after_patch, mutation_allowed

def test_classifier_positive_and_environmental_negative():
    assert classify_failure("python_tests","AssertionError") == "TEST_FAILURE"
    assert classify_failure("frontend_build","network registry unreachable") == "NETWORK_UNAVAILABLE"
    assert classify_failure("mystery","opaque") == "UNCLASSIFIED_FAILURE"

def test_environment_does_not_authorize_coding():
    ok, reason = mutation_allowed("NETWORK_UNAVAILABLE",files=1,patch_bytes=10,iteration=1)
    assert not ok and reason == "NETWORK_UNAVAILABLE"

def test_budget_fails_closed():
    ok, reason = mutation_allowed("TEST_FAILURE",files=6,patch_bytes=10,iteration=1)
    assert not ok and reason == "MUTATION_BUDGET_EXCEEDED"

def test_keep_requires_strict_improvement_and_both_regressions():
    assert decide_after_patch({"TEST_FAILURE","TYPE_FAILURE"},{"TYPE_FAILURE"},targeted_pass=True,full_pass=True) == "KEEP"
    assert decide_after_patch({"TEST_FAILURE"},{"TEST_FAILURE"},targeted_pass=True,full_pass=True) == "ROLLBACK_NO_IMPROVEMENT"
    assert decide_after_patch({"TEST_FAILURE"},set(),targeted_pass=False,full_pass=True) == "ROLLBACK_REGRESSION"

def test_oscillation_rolls_back():
    prior=frozenset({"TYPE_FAILURE"})
    assert decide_after_patch({"TEST_FAILURE","TYPE_FAILURE"},{"TYPE_FAILURE"},targeted_pass=True,full_pass=True,history=[prior]) == "ROLLBACK_OSCILLATION"

def test_iteration_and_patch_size_limits():
    assert mutation_allowed("TEST_FAILURE",files=1,patch_bytes=10,iteration=4)[1] == "MUTATION_BUDGET_EXCEEDED"
    assert mutation_allowed("TEST_FAILURE",files=1,patch_bytes=524289,iteration=1)[1] == "MUTATION_BUDGET_EXCEEDED"

def test_authority_and_unknown_fail_closed():
    assert mutation_allowed("SECRET_REQUIRED",files=1,patch_bytes=10,iteration=1) == (False,"SECRET_REQUIRED")
    assert mutation_allowed("UNCLASSIFIED_FAILURE",files=1,patch_bytes=10,iteration=1) == (False,"UNCLASSIFIED_FAILURE")
