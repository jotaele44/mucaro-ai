from pathlib import Path

from scripts import certify_coding_container_live as cert


def test_live_cert_constants_are_fail_closed() -> None:
    assert cert.STATUS_OK == "CODING_CONTAINER_LIVE_CERT_OK"
    assert cert.STATUS_FAILED == "CODING_CONTAINER_LIVE_CERT_FAILED"
