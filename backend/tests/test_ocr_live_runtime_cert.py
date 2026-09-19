from scripts import certify_ocr_live_runtime as cert


def test_ocr_live_runtime_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "OCR_LIVE_RUNTIME_CERT_OK"
    assert cert.STATUS_FAILED == "OCR_LIVE_RUNTIME_CERT_FAILED"
