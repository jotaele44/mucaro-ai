from scripts import certify_ocr_v1_runtime as cert


def test_ocr_v1_runtime_markers_are_fail_closed() -> None:
    assert cert.STATUS_OK == "OCR_V1_RUNTIME_BOUNDARY_CERT_OK"
    assert cert.STATUS_FAILED == "OCR_V1_RUNTIME_BOUNDARY_CERT_FAILED"
