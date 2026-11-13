from scripts import certify_coding_lifecycle_live as cert


def test_live_lifecycle_cert_closes_local_branch_commit_pr_request_safety() -> None:
    receipt = cert.run_certification()
    failed_invariants = sorted(key for key, value in receipt["invariants"].items() if not value)
    failed_negative_cases = {
        key: (value["expected"], value["observed"])
        for key, value in receipt["negative_cases"].items()
        if not value["passed"]
    }
    assert receipt["status"] == cert.STATUS_OK, {
        "failed_invariants": failed_invariants,
        "failed_negative_cases": failed_negative_cases,
    }
    assert receipt["negative_case_count"] == 9
    assert receipt["negative_pass_count"] == 9
    assert all(receipt["invariants"].values())
    assert receipt["positive"]["pr_request"]["network_action_performed"] is False
    assert receipt["positive"]["pr_request"]["requires_authenticated_remote_adapter"] is True
    assert len(receipt["receipt_sha256"]) == 64
