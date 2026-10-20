from scripts import certify_coding_lifecycle_live as cert


def test_live_lifecycle_cert_closes_local_branch_commit_pr_request_safety() -> None:
    receipt = cert.run_certification()
    assert receipt["status"] == cert.STATUS_OK, {
        "invariants": receipt["invariants"],
        "negative_cases": {
            key: {
                "expected": value["expected"],
                "observed": value["observed"],
                "passed": value["passed"],
            }
            for key, value in receipt["negative_cases"].items()
        },
        "rollback_state": receipt["rollback_state"],
    }
    assert receipt["negative_case_count"] == 9
    assert receipt["negative_pass_count"] == 9
    assert all(receipt["invariants"].values())
    assert receipt["positive"]["pr_request"]["network_action_performed"] is False
    assert receipt["positive"]["pr_request"]["requires_authenticated_remote_adapter"] is True
    assert len(receipt["receipt_sha256"]) == 64
