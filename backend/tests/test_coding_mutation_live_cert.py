from scripts import certify_coding_mutation_live as cert


def test_coding_mutation_live_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "CODING_MUTATION_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "CODING_MUTATION_LIVE_CERT_FAILED"
