from scripts import certify_coding_regression_corpus as cert


def test_corpus_terminal_target_is_complete_and_fail_closed() -> None:
    evidence = {
        "container": "container:evidence",
        "full_profile": "profile:evidence",
        "mutation": "mutation:evidence",
        "review": "review:evidence",
        "lifecycle": "lifecycle:evidence",
        "hosted_ci": "hosted:evidence",
    }
    rows = cert._build_rows(evidence, hosted_ci_pass=True)
    assert len(rows) == 23
    assert len({row["requirement_id"] for row in rows}) == 23
    states = {
        state: sum(row["state"] == state for row in rows)
        for state in ("PASS", "FAIL", "BLOCKED", "UNRESOLVED")
    }
    assert states == cert.EXPECTED_COUNTS
    assert {
        row["requirement_id"]
        for row in rows
        if row["state"] == "BLOCKED"
    } == cert.EXPECTED_BLOCKED
    assert cert.STATUS_OK == "CODING_REGRESSION_CORPUS_TERMINAL_BLOCKED"


def test_remote_git_and_full_certification_are_never_promoted_by_hosted_ci() -> None:
    evidence = {
        "container": "container:evidence",
        "full_profile": "profile:evidence",
        "mutation": "mutation:evidence",
        "review": "review:evidence",
        "lifecycle": "lifecycle:evidence",
        "hosted_ci": "hosted:evidence",
    }
    rows = {row["requirement_id"]: row for row in cert._build_rows(evidence, hosted_ci_pass=True)}
    assert rows["CODE-R21"]["state"] == "BLOCKED"
    assert rows["CODE-R23"]["state"] == "BLOCKED"
    assert "credential" in rows["CODE-R21"]["detail"].lower()
