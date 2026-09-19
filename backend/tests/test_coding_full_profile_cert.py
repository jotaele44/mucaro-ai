from scripts import certify_coding_full_profile as cert


def test_full_profile_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "CODING_FULL_PROFILE_CERT_OK"
    assert cert.STATUS_FAILED == "CODING_FULL_PROFILE_CERT_FAILED"
