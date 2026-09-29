from scripts import certify_format_registry as cert


def test_format_registry_cert_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "FORMAT_REGISTRY_CERT_OK"
    assert cert.STATUS_FAILED == "FORMAT_REGISTRY_CERT_FAILED"
